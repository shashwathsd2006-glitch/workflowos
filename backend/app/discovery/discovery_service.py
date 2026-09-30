"""Discovery service — orchestrates the discovery pipeline and persistence.

Pipeline:  Activity Events → Sequence Builder → Normalizer → Similarity
           → Pattern Detector → Workflow Candidate → SQLite

Discovery is explicit and deterministic: it runs only when
``POST /api/workflows/discover`` is called, never in the background.
"""

from __future__ import annotations

from typing import List, Optional

from app.config import settings
from app.database import get_connection
from app.discovery.pattern_detector import DetectedPattern, detect_patterns
from app.discovery.sequence_builder import build_sequences
from app.models import activity as activity_model
from app.models import workflow_candidate as candidate_model
from app.schemas.discovery import (
    DiscoverResponse,
    DiscoveredWorkflowsResponse,
    WorkflowCandidate,
)


def _to_candidate(pattern: DetectedPattern, identifier: str) -> WorkflowCandidate:
    return WorkflowCandidate(
        id=identifier,
        name=pattern.name,
        sequence=pattern.sequence,
        occurrence_count=pattern.occurrence_count,
        session_ids=pattern.session_ids,
        similarity_score=pattern.similarity_score,
        confidence=pattern.confidence,
        confidence_label=pattern.confidence_label,
        applications=pattern.applications,
        first_seen=pattern.first_seen,
        last_seen=pattern.last_seen,
        status=pattern.status,
    )


def run_discovery(threshold: Optional[float] = None) -> DiscoverResponse:
    """Analyse all stored activity and persist the detected candidates.

    Previously *detected* candidates are replaced so repeated runs stay
    deterministic; reviewed/approved/rejected rows (Phase 5+) are kept.
    """
    active_threshold = (
        settings.similarity_threshold if threshold is None else threshold
    )

    with get_connection() as connection:
        activity_model.ensure_table(connection)
        candidate_model.ensure_table(connection)
        rows = activity_model.select_all_events(connection)
        events = [activity_model.row_to_event(row) for row in rows]

    sequences = build_sequences(events)
    patterns = detect_patterns(sequences, threshold=active_threshold)

    candidates: List[WorkflowCandidate] = [
        _to_candidate(pattern, f"workflow-{index:03d}")
        for index, pattern in enumerate(patterns, start=1)
    ]

    with get_connection() as connection:
        activity_model.ensure_table(connection)
        candidate_model.ensure_table(connection)
        candidate_model.delete_detected(connection)
        for candidate in candidates:
            candidate_model.insert_candidate(connection, candidate)

    return DiscoverResponse(
        workflows=candidates,
        count=len(candidates),
        sequences_analyzed=len(sequences),
        threshold=active_threshold,
    )


def get_discovered() -> DiscoveredWorkflowsResponse:
    with get_connection() as connection:
        candidate_model.ensure_table(connection)
        candidates = candidate_model.select_candidates(connection)
    return DiscoveredWorkflowsResponse(
        workflows=candidates, count=len(candidates)
    )
