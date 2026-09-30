"""Pattern Detector — repetition detection, confidence and naming.

Deterministic only: no LLM, no learned model. Given normalized sequences it
returns workflow candidates (patterns seen at least ``MIN_OCCURRENCES`` times).

Confidence formula (documented, reproducible)::

    confidence = 0.50 * similarity_score
               + 0.30 * repetition_score
               + 0.20 * sequence_consistency

    repetition_score      = min(1.0, (occurrence_count - 1) / 4)
                            (2 → 0.25, 3 → 0.50, 5+ → 1.00)
    similarity_score      = mean similarity of each member vs the exemplar
    sequence_consistency  = mean positional agreement of each member vs the
                            exemplar (identical runs score 1.0)

Labels: ``confidence < 0.60 → low``, ``< 0.85 → medium``, else ``high``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Sequence

from app.discovery.normalizer import NormalizedStep, normalize_sequence
from app.discovery.sequence_builder import ActivitySequence
from app.discovery.similarity import position_agreement, sequence_similarity
from app.schemas.discovery import ConfidenceLabel, WorkflowStatus, WorkflowStep

MIN_OCCURRENCES = 2
SIMILARITY_WEIGHT = 0.5
REPETITION_WEIGHT = 0.3
CONSISTENCY_WEIGHT = 0.2
REPETITION_CEILING = 5

# Deterministic intent lookup. Phase 5 replaces this with LLM understanding.
KNOWN_SIGNATURES = {
    frozenset({"Gmail", "CRM", "Slack"}): "Customer Request Processing",
    frozenset({"Gmail", "Finder", "Excel"}): "Document Processing",
    frozenset({"Calendar", "Gmail"}): "Meeting Follow-up",
}


@dataclass
class DetectedPattern:
    """A repeated sequence found in the activity stream (id assigned later)."""

    name: str
    sequence: List[WorkflowStep]
    occurrence_count: int
    session_ids: List[str]
    similarity_score: float
    confidence: float
    confidence_label: ConfidenceLabel
    applications: List[str]
    first_seen: datetime
    last_seen: datetime
    status: WorkflowStatus = "detected"
    steps: List[NormalizedStep] = field(default_factory=list)


def cluster_sequences(
    sequences: Sequence[ActivitySequence],
    threshold: float,
) -> List[List[ActivitySequence]]:
    """Greedy exemplar clustering: every sequence joins the first cluster whose
    exemplar is at least ``threshold`` similar, otherwise it opens a new one."""
    clusters: List[List[ActivitySequence]] = []
    exemplars: List[List[NormalizedStep]] = []

    for sequence in sequences:
        normalized = normalize_sequence(sequence.events)
        matched = None
        for index, exemplar in enumerate(exemplars):
            if sequence_similarity(normalized, exemplar) >= threshold:
                matched = index
                break
        if matched is None:
            clusters.append([sequence])
            exemplars.append(normalized)
        else:
            clusters[matched].append(sequence)

    return clusters


def repetition_score(occurrence_count: int) -> float:
    return round(min(1.0, (occurrence_count - 1) / (REPETITION_CEILING - 1)), 4)


def compute_confidence(
    similarity_score: float,
    occurrence_count: int,
    sequence_consistency: float,
) -> float:
    score = (
        SIMILARITY_WEIGHT * similarity_score
        + REPETITION_WEIGHT * repetition_score(occurrence_count)
        + CONSISTENCY_WEIGHT * sequence_consistency
    )
    return round(min(max(score, 0.0), 1.0), 4)


def label_confidence(value: float) -> ConfidenceLabel:
    if value < 0.6:
        return "low"
    if value < 0.85:
        return "medium"
    return "high"


def name_candidate(applications: Sequence[str]) -> str:
    """Deterministic naming — recognisable app signatures, else a generic name."""
    signature = frozenset(applications)
    known = KNOWN_SIGNATURES.get(signature)
    if known:
        return known
    chain = " → ".join(applications) if applications else "unknown"
    return f"Repeated Workflow — {chain}"


def build_pattern(cluster: Sequence[ActivitySequence]) -> Optional[DetectedPattern]:
    """Turn a cluster (>= MIN_OCCURRENCES members) into a workflow candidate."""
    if len(cluster) < MIN_OCCURRENCES:
        return None

    ordered = sorted(cluster, key=lambda item: (item.first_seen, item.session_id))
    exemplar = ordered[0]
    exemplar_steps = normalize_sequence(exemplar.events)

    similarities: List[float] = []
    consistencies: List[float] = []
    for sequence in ordered:
        steps = normalize_sequence(sequence.events)
        similarities.append(sequence_similarity(steps, exemplar_steps))
        consistencies.append(position_agreement(steps, exemplar_steps))

    similarity_score = round(sum(similarities) / len(similarities), 4)
    consistency = round(sum(consistencies) / len(consistencies), 4)
    confidence = compute_confidence(
        similarity_score, len(ordered), consistency
    )

    applications: List[str] = []
    for event in exemplar.events:
        if event.application not in applications:
            applications.append(event.application)

    display_sequence = [
        WorkflowStep(
            application=event.application,
            category=event.category,
            action=event.action,
            description=event.description,
        )
        for event in exemplar.events
    ]

    return DetectedPattern(
        name=name_candidate(applications),
        sequence=display_sequence,
        occurrence_count=len(ordered),
        session_ids=[sequence.session_id for sequence in ordered],
        similarity_score=similarity_score,
        confidence=confidence,
        confidence_label=label_confidence(confidence),
        applications=applications,
        first_seen=min(sequence.first_seen for sequence in ordered),
        last_seen=max(sequence.last_seen for sequence in ordered),
        steps=exemplar_steps,
    )


def detect_patterns(
    sequences: Sequence[ActivitySequence],
    threshold: float,
) -> List[DetectedPattern]:
    """Cluster sequences and return candidates with >= 2 occurrences."""
    clusters = cluster_sequences(sequences, threshold)
    patterns = [build_pattern(cluster) for cluster in clusters]
    detected = [pattern for pattern in patterns if pattern is not None]
    detected.sort(key=lambda item: (-item.occurrence_count, item.first_seen))
    return detected
