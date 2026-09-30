"""Activity API tests: create, retrieve, filter, simulate, clear."""

from __future__ import annotations

from datetime import datetime

from app.agents.activity_source import SEQUENCES


def parse_iso(value: str) -> datetime:
    """Python 3.9 ``fromisoformat`` does not accept the 'Z' suffix."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "WorkFlowOS"}


def test_create_activity_event_and_metadata_roundtrip(client):
    payload = {
        "application": "Gmail",
        "category": "communication",
        "action": "open_email",
        "description": "Opened customer request email",
        "metadata": {"subject": "Customer Request - ABC Industries"},
    }
    created = client.post("/api/activity", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["id"].startswith("event-")
    assert body["session_id"].startswith("session-")
    assert body["timestamp"]
    assert body["metadata"] == payload["metadata"]

    listed = client.get("/api/activity").json()
    assert listed["total"] == 1
    stored = listed["events"][0]
    assert stored["metadata"]["subject"] == "Customer Request - ABC Industries"
    assert stored["application"] == "Gmail"
    assert stored["category"] == "communication"


def test_create_activity_event_accepts_explicit_id_and_session(client):
    payload = {
        "id": "event-manual-1",
        "timestamp": "2026-09-26T10:00:00+00:00",
        "application": "Slack",
        "category": "communication",
        "action": "send_notification",
        "description": "Sent notification to support team",
        "session_id": "session-manual",
    }
    created = client.post("/api/activity", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["id"] == "event-manual-1"
    assert body["session_id"] == "session-manual"
    assert body["timestamp"].startswith("2026-09-26T10:00:00")


def test_create_activity_event_rejects_unknown_category(client):
    response = client.post(
        "/api/activity",
        json={
            "application": "Gmail",
            "category": "not-a-category",
            "action": "open_email",
            "description": "nope",
        },
    )
    assert response.status_code == 422


def test_retrieve_events_in_chronological_order(client):
    client.post("/api/activity/simulate", json={"workflow": "customer_request"})
    body = client.get("/api/activity").json()
    timestamps = [event["timestamp"] for event in body["events"]]
    assert timestamps == sorted(timestamps)
    assert body["count"] == len(SEQUENCES["customer_request"])
    assert body["total"] == body["count"]


def test_retrieve_events_respects_limit(client):
    client.post("/api/activity/simulate", json={"workflow": "customer_request"})
    body = client.get("/api/activity?limit=2").json()
    assert body["count"] == 2
    assert body["total"] == 6


def test_filter_by_application(client):
    client.post("/api/activity/simulate", json={"workflow": "customer_request"})
    body = client.get("/api/activity?application=Gmail").json()
    assert body["count"] > 0
    assert {event["application"] for event in body["events"]} == {"Gmail"}
    assert body["total"] == body["count"]


def test_filter_by_category(client):
    client.post("/api/activity/simulate", json={"workflow": "customer_request"})
    body = client.get("/api/activity?category=file").json()
    assert body["count"] == 1
    assert body["events"][0]["action"] == "download_attachment"
    assert body["events"][0]["category"] == "file"


def test_filter_by_session(client):
    response = client.post(
        "/api/activity/simulate",
        json={"workflow": "customer_request", "repetitions": 3},
    )
    assert response.status_code == 200
    sessions = response.json()["session_ids"]
    assert len(set(sessions)) == 3

    body = client.get(f"/api/activity?session_id={sessions[1]}").json()
    assert body["count"] == len(SEQUENCES["customer_request"])
    assert {event["session_id"] for event in body["events"]} == {sessions[1]}


def test_simulate_defaults_to_customer_request(client):
    body = client.post("/api/activity/simulate", json={}).json()
    assert body["workflow"] == "customer_request"
    assert body["repetitions"] == 1
    assert body["generated"] == len(SEQUENCES["customer_request"])
    assert len(body["session_ids"]) == 1

    stored = client.get("/api/activity").json()
    assert stored["total"] == body["generated"]


def test_simulate_generates_spaced_timestamps(client):
    body = client.post("/api/activity/simulate", json={}).json()
    timestamps = [event["timestamp"] for event in body["events"]]
    parsed = [parse_iso(value) for value in timestamps]
    assert parsed == sorted(parsed)
    assert len(set(timestamps)) == len(timestamps)
    # realistic spacing: never identical, never in the future
    assert all(parsed[i] < parsed[i + 1] for i in range(len(parsed) - 1))
    assert parsed[-1] <= datetime.now(parsed[-1].tzinfo)


def test_simulate_three_repetitions_creates_three_sessions(client):
    body = client.post(
        "/api/activity/simulate",
        json={"workflow": "customer_request", "repetitions": 3},
    ).json()
    assert body["generated"] == len(SEQUENCES["customer_request"]) * 3
    assert len(body["session_ids"]) == 3
    assert len(set(body["session_ids"])) == 3

    stats = client.get("/api/activity/stats").json()
    assert stats["total_events"] == body["generated"]


def test_simulate_all_supported_workflows(client):
    for workflow in ("customer_request", "document_processing", "meeting_followup"):
        response = client.post(
            "/api/activity/simulate", json={"workflow": workflow}
        )
        assert response.status_code == 200
        assert response.json()["workflow"] == workflow


def test_simulate_unknown_workflow_returns_400(client):
    response = client.post(
        "/api/activity/simulate", json={"workflow": "does_not_exist"}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "http_error"
    assert "does_not_exist" in error["detail"]


def test_stats_endpoint(client):
    empty = client.get("/api/activity/stats").json()
    assert empty["total_events"] == 0
    assert empty["total_today"] == 0
    assert empty["applications"] == []
    assert empty["last_activity"] is None
    assert empty["current_session"] is None

    client.post(
        "/api/activity/simulate",
        json={"workflow": "customer_request", "repetitions": 2},
    )
    stats = client.get("/api/activity/stats").json()
    assert stats["total_events"] == 12
    assert stats["total_today"] == 12
    assert stats["applications"] == ["CRM", "Gmail", "Slack"]
    assert stats["application_count"] == 3
    assert stats["last_activity"] is not None
    assert stats["current_session"] is not None
    assert stats["session_event_count"] == 6


def test_clear_activity(client):
    client.post("/api/activity/simulate", json={"workflow": "customer_request"})
    assert client.get("/api/activity").json()["total"] == 6

    deleted = client.delete("/api/activity")
    assert deleted.status_code == 200
    assert deleted.json() == {"cleared": 6}

    assert client.get("/api/activity").json()["total"] == 0
    assert client.delete("/api/activity").json()["cleared"] == 0


def test_limit_validation(client):
    assert client.get("/api/activity?limit=0").status_code == 422
    assert client.get("/api/activity?limit=1000").status_code == 422
