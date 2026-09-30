"""Sequence Similarity — deterministic, explainable, no LLM.

Two normalized sequences are compared with:

1. **Edit similarity** — Levenshtein distance over normalized steps,
   converted to ``1 - distance / max(len)``. Order matters.
2. **Jaccard similarity** — overlap of the step *sets*. Order-insensitive,
   so a reordered sequence still scores partly.

``similarity = 0.7 * edit_similarity + 0.3 * jaccard_similarity``

Scores are in ``[0.0, 1.0]``:

    1.00 identical workflow sequence
    0.90 extremely similar
    0.75 probably related      ← default detection threshold
    0.50 weak similarity
    0.00 unrelated
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from app.discovery.normalizer import NormalizedStep

DEFAULT_THRESHOLD = 0.75
EDIT_WEIGHT = 0.7
JACCARD_WEIGHT = 0.3


def levenshtein(left: Sequence, right: Sequence) -> int:
    """Classic edit distance between two sequences."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, left_item in enumerate(left, start=1):
        current = [i]
        for j, right_item in enumerate(right, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (left_item != right_item)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def edit_similarity(left: Sequence, right: Sequence) -> float:
    if not left and not right:
        return 0.0
    longest = max(len(left), len(right))
    if longest == 0:
        return 0.0
    return 1.0 - (levenshtein(left, right) / longest)


def jaccard_similarity(left: Sequence, right: Sequence) -> float:
    set_left = set(left)
    set_right = set(right)
    if not set_left and not set_right:
        return 0.0
    union = set_left | set_right
    if not union:
        return 0.0
    return len(set_left & set_right) / len(union)


def sequence_similarity(
    left: Sequence[NormalizedStep],
    right: Sequence[NormalizedStep],
) -> float:
    """Combined similarity of two normalized sequences, rounded to 4 decimals."""
    if not left or not right:
        return 0.0

    left_keys: List[Tuple[str, str, str]] = [step.key for step in left]
    right_keys: List[Tuple[str, str, str]] = [step.key for step in right]

    score = (
        EDIT_WEIGHT * edit_similarity(left_keys, right_keys)
        + JACCARD_WEIGHT * jaccard_similarity(left_keys, right_keys)
    )
    return round(min(max(score, 0.0), 1.0), 4)


def position_agreement(
    left: Sequence[NormalizedStep],
    right: Sequence[NormalizedStep],
) -> float:
    """Fraction of positions (over the shorter length) that match exactly."""
    shortest = min(len(left), len(right))
    if shortest == 0:
        return 0.0
    matches = sum(
        1 for index in range(shortest) if left[index].key == right[index].key
    )
    return round(matches / shortest, 4)
