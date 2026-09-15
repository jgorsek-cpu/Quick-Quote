"""Per-line and quote-level confidence scoring."""
from __future__ import annotations

from datetime import date

from ..config import ACCEPTED, HIGH, LOW, MEDIUM
from ..reference.loader import ReferenceData
from ..schemas import CostedIngredient, CostedPackaging, MatchResult


def line_confidence(match: MatchResult, reference: ReferenceData, as_of: date) -> str:
    """High / Medium / Low for a single line.

    High    Accepted exact part-code match on a price under six months old.
    Medium  Accepted identity match, or an exact match on a price between six
            months and a year old.
    Low     anything not Accepted, and any Accepted line whose price is over a
            year old or has no date at all -- an unknown-age price is not a
            confident one, however the line was matched.

    Recency is weighted rather than applied as a single cliff, because a price
    that has not been tested in a year is not evidence of today's cost.
    """
    if match.status != ACCEPTED:
        return LOW

    age = None
    if match.latest_po_date is not None:
        age = (as_of - match.latest_po_date).days

    if age is None:
        return LOW
    if age > reference.stale_po_days:
        return LOW
    if age > reference.stale_po_warn_days:
        return MEDIUM

    return HIGH if match.method.startswith("Tier 1") else MEDIUM


def quote_confidence(
    ingredients: list[CostedIngredient],
    packaging: list[CostedPackaging],
    reference: ReferenceData,
) -> str:
    """Roll per-line confidence up to the quote.

    Low     any Needs Review or Unmatched line exists, or any major-spend
            Accepted line is Low.
    Medium  no Needs Review or Unmatched lines, but some major-spend
            Accepted line is Medium.
    High    every major-spend Accepted line is High and nothing is
            outstanding.

    A major-spend line is one accounting for more than 10% of primary cost.
    """
    all_lines: list[CostedIngredient | CostedPackaging] = [*ingredients, *packaging]
    if not all_lines:
        return LOW

    if any(not line.match.accepted for line in all_lines):
        return LOW

    primary_total = sum(
        line.cost_per_bottle or 0.0 for line in all_lines if line.match.accepted
    )
    threshold = primary_total * reference.major_spend_pct

    major_lines = [
        line
        for line in all_lines
        if line.match.accepted and (line.cost_per_bottle or 0.0) > threshold
    ]
    if not major_lines:
        major_lines = [line for line in all_lines if line.match.accepted]

    if any(line.confidence == LOW for line in major_lines):
        return LOW
    if any(line.confidence == MEDIUM for line in major_lines):
        return MEDIUM
    return HIGH


def is_major_spend(
    cost_per_bottle: float | None, primary_total: float, reference: ReferenceData
) -> bool:
    """True when this line is more than 10% of primary cost."""
    if not cost_per_bottle or primary_total <= 0:
        return False
    return cost_per_bottle > primary_total * reference.major_spend_pct
