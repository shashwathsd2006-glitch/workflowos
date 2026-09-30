"""Discovery tests: sequences, normalization, similarity, repetition, API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.discovery.normalizer import normalize_sequence
from app.discovery.pattern_detector import (
    cluster_sequences,
    compute_confidence,
    detect_patterns,
    label_confidence,
    name_candidate,
    repetition_score,
)
from app.discovery.sequence_builder import build_sequences
from app.discovery.similarity import sequence_similarity
from app.schemas.activity import ActivityEvent

BASE = datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone.utc)


def make_event(
    application: str,
    action: str,
    category: str,
    session_id: str,
    offset: int = 0,
    event_id: Optional[str] = None,
    description: str = "",
    metadata: Optional[dict] = None,
    timestamp: Optional[datetime] = None,
) -> ActivityEvent:
    return ActivityEvent(
        id=event_id or f"{session_id}-e{offset}",
        timestamp=timestamp or (BASE + timedelta(seconds=offset)),
        application=application,
        category=category,
        action=action,
        description=description or action,
        metadata=metadata or {},
        session_id=session_id,
    )


def customer_request_events(session_id: str, start: int = 0) -> List[ActivityEvent]:
    steps = [
        ("Gmail", "open_email", "communication"),
        ("Gmail", "read_email", "communication"),
        ("Gmail", "download_attachment", "file"),
        ("CRM", "find_customer", "crm"),
        ("CRM", "update_customer", "crm"),
        ("Slack", "send_notification", "communication"),
    ]
    return [
        make_event(app, action, category, session_id, start + index * 20)
        for index, (app, action, category) in enumerate(steps)
    ]


def meeting_events(session_id: str, start: int = 0) -> List[ActivityEvent]:
    steps = [
        ("Calendar", "open_meeting", "application"),
        ("Calendar", "read_meeting_details", "application"),
        ("Gmail", "compose_email", "communication"),
        ("Gmail", "send_email", "communication"),
    ]
    return [
        make_event(app, action, category, session_id, start + index * 20)
        for index, (app, action, category) in enumerate(steps)
    ]


# ---------------------------------------------------------------- sequences

def test_sequence_construction_groups_by_session():
    events = customer_request_events("session-001") + meeting_events("session-002")
    sequences = build_sequences(events)
    assert [s.session_id for s in sequences] == ["session-001", "session-002"]
    assert len(sequences[0]) == 6
    assert len(sequences[1]) == 4


def test_sequence_ordering_uses_timestamps_not_input_order():
    events = customer_request_events("session-001")
    shuffled = list(reversed(events))
    sequence = build_sequences(shuffled)[0]
    timestamps = [event.timestamp for event in sequence.events]
    assert timestamps == sorted(timestamps)
    assert sequence.events[0].action == "open_email"
    assert sequence.events[-1].action == "send_notification"


def test_sequence_preserves_chronology_across_sessions():
    early = customer_request_events("session-001", start=0)
    late = customer_request_events("session-002", start=600)
    sequences = build_sequences(late + early)
    assert [s.session_id for s in sequences] == ["session-001", "session-002"]


# ------------------------------------------------------------ normalization

def test_normalization_ignores_metadata_ids_and_timestamps():
    first = customer_request_events(
        "session-001",
    )
    second = [
        make_event(
            event.application,
            event.action,
            event.category,
            "session-002",
            offset=index * 999 + 5000,
            event_id=f"other-{index}",
            metadata={"subject": f"variant-{index}", "file": f"file-{index}.pdf"},
            timestamp=BASE + timedelta(days=1, seconds=index * 37),
        )
        for index, event in enumerate(first)
    ]
    assert normalize_sequence(first) == normalize_sequence(second)


def test_normalization_lowercases_and_trims():
    event = make_event("  Gmail ", "Open_Email", "communication", "session-001")
    step = normalize_sequence([event])[0]
    assert step.application == "gmail"
    assert step.action == "open_email"
    assert step.category == "communication"


# --------------------------------------------------------------- similarity

def test_identical_sequences_score_one():
    left = normalize_sequence(customer_request_events("session-001"))
    right = normalize_sequence(customer_request_events("session-002"))
    assert sequence_similarity(left, right) == 1.0


def test_single_step_difference_stays_above_threshold():
    left = normalize_sequence(customer_request_events("session-001"))
    altered = customer_request_events("session-002")
    altered[4] = make_event("CRM", "merge_customer", "crm", "session-002", 80)
    right = normalize_sequence(altered)
    score = sequence_similarity(left, right)
    assert 0.75 <= score < 1.0


def test_partial_sequence_is_below_threshold():
    left = normalize_sequence(customer_request_events("session-001"))
    right = normalize_sequence(customer_request_events("session-002")[:4])
    score = sequence_similarity(left, right)
    assert score < 0.75


def test_unrelated_sequences_score_zero():
    left = normalize_sequence(customer_request_events("session-001"))
    right = normalize_sequence(meeting_events("session-002"))
    assert sequence_similarity(left, right) == 0.0


def test_empty_sequences_score_zero():
    assert sequence_similarity([], []) == 0.0


# ------------------------------------------------------------ repetition

def test_repetition_detection_requires_two_occurrences():
    single = build_sequences(customer_request_events("session-001"))
    assert detect_patterns(single, threshold=0.75) == []

    repeated = build_sequences(
        customer_request_events("session-001")
        + customer_request_events("session-002", start=600)
    )
    patterns = detect_patterns(repeated, threshold=0.75)
    assert len(patterns) == 1
    assert patterns[0].occurrence_count == 2


def test_three_identical_sessions_detected_once():
    events = (
        customer_request_events("session-001", start=0)
        + customer_request_events("session-002", start=600)
        + customer_request_events("session-003", start=1200)
    )
    patterns = detect_patterns(build_sequences(events), threshold=0.75)
    assert len(patterns) == 1
    pattern = patterns[0]
    assert pattern.occurrence_count == 3
    assert pattern.session_ids == ["session-001", "session-002", "session-003"]
    assert pattern.similarity_score == 1.0


def test_unrelated_sequences_do_not_cluster():
    events = (
        customer_request_events("session-001", start=0)
        + customer_request_events("session-002", start=600)
        + meeting_events("session-003", start=1200)
        + meeting_events("session-004", start=1800)
    )
    patterns = detect_patterns(build_sequences(events), threshold=0.75)
    assert len(patterns) == 2
    assert {pattern.occurrence_count for pattern in patterns} == {2}
    names = {pattern.name for pattern in patterns}
    assert names == {"Customer Request Processing", "Meeting Follow-up"}


def test_cluster_threshold_is_configurable():
    sequences = build_sequences(
        customer_request_events("session-001")
        + customer_request_events("session-002")[:4]
        + customer_request_events("session-003", start=600)[:4]
    )
    strict = cluster_sequences(sequences, threshold=0.99)
    relaxed = cluster_sequences(sequences, threshold=0.4)
    assert len(relaxed) < len(strict)


# ------------------------------------------------------------- confidence

def test_repetition_score_scale():
    assert repetition_score(2) == 0.25
    assert repetition_score(3) == 0.5
    assert repetition_score(5) == 1.0
    assert repetition_score(50) == 1.0


def test_confidence_formula_is_documented_and_reproducible():
    # 0.5*1.0 + 0.3*0.5 + 0.2*1.0 = 0.85 for three identical runs
    assert compute_confidence(1.0, 3, 1.0) == 0.85
    # two identical runs: 0.5*1.0 + 0.3*0.25 + 0.2*1.0 = 0.775
    assert compute_confidence(1.0, 2, 1.0) == 0.775
    # five identical runs: 0.5*1.0 + 0.3*1.0 + 0.2*1.0 = 1.0
    assert compute_confidence(1.0, 5, 1.0) == 1.0
    assert compute_confidence(0.0, 1, 0.0) == 0.0


def test_confidence_labels():
    assert label_confidence(0.4) == "low"
    assert label_confidence(0.775) == "medium"
    assert label_confidence(0.85) == "high"


def test_three_runs_produce_high_confidence():
    events = (
        customer_request_events("session-001", start=0)
        + customer_request_events("session-002", start=600)
        + customer_request_events("session-003", start=1200)
    )
    pattern = detect_patterns(build_sequences(events), threshold=0.75)[0]
    assert pattern.confidence == 0.85
    assert pattern.confidence_label == "high"


# ---------------------------------------------------------------- naming

def test_deterministic_naming_for_known_signatures():
    assert name_candidate(["Gmail", "CRM", "Slack"]) == "Customer Request Processing"
    assert name_candidate(["Gmail", "Finder", "Excel"]) == "Document Processing"
    assert name_candidate(["Calendar", "Gmail"]) == "Meeting Follow-up"


def test_generic_naming_fallback():
    assert (
        name_candidate(["Notes", "Terminal"])
        == "Repeated Workflow — Notes → Terminal"
    )


def test_pattern_fields_populated():
    events = (
        customer_request_events("session-001", start=0)
        + customer_request_events("session-002", start=600)
    )
    pattern = detect_patterns(build_sequences(events), threshold=0.75)[0]
    assert pattern.name == "Customer Request Processing"
    assert pattern.applications == ["Gmail", "CRM", "Slack"]
    assert len(pattern.sequence) == 6
    assert pattern.sequence[0].action == "open_email"
    assert pattern.status == "detected"
    assert pattern.first_seen < pattern.last_seen


# --------------------------------------------------------------------- API

def test_discover_on_empty_activity_returns_nothing(client):
    response = client.post("/api/workflows/discover")
    assert response.status_code == 200
    body = response.json()
    assert body["workflows"] == []
    assert body["count"] == 0
    assert body["sequences_analyzed"] == 0
    assert body["threshold"] == 0.75

    listed = client.get("/api/workflows/discovered").json()
    assert listed == {"workflows": [], "count": 0}


def test_discover_detects_customer_request_after_three_repetitions(client):
    client.post(
        "/api/activity/simulate",
        json={"workflow": "customer_request", "repetitions": 3},
    )
    result = client.post("/api/workflows/discover").json()
    assert result["count"] == 1
    assert result["sequences_analyzed"] == 3

    workflow = result["workflows"][0]
    assert workflow["id"] == "workflow-001"
    assert workflow["name"] == "Customer Request Processing"
    assert workflow["occurrence_count"] >= 3
    assert workflow["similarity_score"] == 1.0
    assert workflow["confidence"] == 0.85
    assert workflow["confidence_label"] == "high"
    assert workflow["applications"] == ["Gmail", "CRM", "Slack"]
    assert workflow["status"] == "detected"
    assert len(workflow["session_ids"]) == 3
    assert len(workflow["sequence"]) == 6

    listed = client.get("/api/workflows/discovered").json()
    assert listed["count"] == 1
    assert listed["workflows"][0]["id"] == "workflow-001"


def test_discover_keeps_unrelated_workflows_apart(client):
    client.post(
        "/api/activity/simulate",
        json={"workflow": "customer_request", "repetitions": 2},
    )
    client.post(
        "/api/activity/simulate",
        json={"workflow": "meeting_followup", "repetitions": 2},
    )
    result = client.post("/api/workflows/discover").json()
    assert result["count"] == 2
    names = {workflow["name"] for workflow in result["workflows"]}
    assert names == {"Customer Request Processing", "Meeting Follow-up"}
    for workflow in result["workflows"]:
        assert workflow["occurrence_count"] == 2


def test_single_session_is_not_a_candidate(client):
    client.post("/api/activity/simulate", json={"workflow": "customer_request"})
    result = client.post("/api/workflows/discover").json()
    assert result["count"] == 0
    assert result["sequences_analyzed"] == 1


def test_discover_is_deterministic_across_runs(client):
    client.post(
        "/api/activity/simulate",
        json={"workflow": "document_processing", "repetitions": 3},
    )
    first = client.post("/api/workflows/discover").json()
    second = client.post("/api/workflows/discover").json()
    assert first["count"] == second["count"] == 1
    assert first["workflows"] == second["workflows"]
    assert second["workflows"][0]["name"] == "Document Processing"


def test_metadata_differences_do_not_split_candidates(client):
    """Two sessions whose only difference is subject/customer metadata."""
    for index, session in enumerate(("session-a", "session-b")):
        for step, (app, action, category) in enumerate(
            [
                ("Gmail", "open_email", "communication"),
                ("Gmail", "read_email", "communication"),
                ("CRM", "find_customer", "crm"),
                ("Slack", "send_notification", "communication"),
            ]
        ):
            client.post(
                "/api/activity",
                json={
                    "id": f"event-m-{index}-{step}",
                    "session_id": session,
                    "timestamp": (
                        datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone.utc)
                        + timedelta(minutes=index * 10 + step)
                    ).isoformat(),
                    "application": app,
                    "category": category,
                    "action": action,
                    "description": action,
                    "metadata": {
                        "subject": f"Request #{index}-{step}",
                        "customer": f"Customer {index}",
                    },
                },
            )

    result = client.post("/api/workflows/discover").json()
    assert result["count"] == 1
    assert result["workflows"][0]["occurrence_count"] == 2
    assert result["workflows"][0]["similarity_score"] == 1.0


def test_multiple_unrelated_single_sessions_produce_nothing(client):
    client.post("/api/activity/simulate", json={"workflow": "customer_request"})
    client.post("/api/activity/simulate", json={"workflow": "meeting_followup"})
    result = client.post("/api/workflows/discover").json()
    assert result["count"] == 0
    assert result["sequences_analyzed"] == 2
