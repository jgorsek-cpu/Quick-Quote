"""Three-tier matching of input lines against purchase-order history.

The first tier that succeeds determines the result. No fuzzy similarity
scoring is used anywhere: when the system is not highly confident, the line
is left Unmatched.
"""
from __future__ import annotations

from ..config import ACCEPTED, NEEDS_REVIEW, UNMATCHED
from ..reference.loader import PoRow, ReferenceData
from ..schemas import MatchResult
from .identity import Resolution, guard_conflict, resolve_identity
from .text import SizeSignature, extract_size


def po_rows_by_identity(reference: ReferenceData, packaging: bool) -> dict[str, list[PoRow]]:
    """Canonical identity -> the PO rows that resolve to it.

    Built once per reference-data load. Without it every line re-resolved
    every purchase-order row against every alias, which is what made a
    seven-rung price ladder take seconds.
    """
    if reference.identity_index is None:
        index: dict[bool, dict[str, list[PoRow]]] = {False: {}, True: {}}
        for mode in (False, True):
            for row in reference.po_rows:
                resolution = resolve_identity(row.description, reference, packaging=mode)
                if resolution is None or guard_conflict(reference, resolution):
                    continue
                index[mode].setdefault(resolution.canonical, []).append(row)
        reference.identity_index = index
    return reference.identity_index[packaging]


def po_rows_by_role(reference: ReferenceData, role: str) -> list[PoRow]:
    """Every PO row whose packaging identity carries this role.

    Lets a pack-out be built from the catalogue that exists rather than only
    from the hand-curated canonical names, while still refusing to choose
    between several candidates.
    """
    index = po_rows_by_identity(reference, packaging=True)
    rows: list[PoRow] = []
    for entry in reference.packaging_identities:
        if entry.role == role:
            rows.extend(index.get(entry.canonical, []))
    return rows


def _result_from_po(row: PoRow, status: str, reason: str, method: str, **extra) -> MatchResult:
    return MatchResult(
        status=status,
        reason=reason,
        method=method,
        matched_code=row.part_number,
        po_description=row.description,
        latest_unit_cost=row.latest_unit_cost,
        uom=row.uom,
        latest_po_date=row.latest_po_date,
        vendor=row.latest_vendor,
        unique_vendor_count=row.unique_vendor_count,
        po_count=row.po_count,
        min_unit_cost_ever=row.min_unit_cost_ever,
        max_unit_cost_ever=row.max_unit_cost_ever,
        **extra,
    )


def match_line(
    description: str | None,
    part_code: str | None,
    reference: ReferenceData,
    packaging: bool = False,
    size_hint: SizeSignature | None = None,
) -> MatchResult:
    """Run one input line through Tier 1, Tier 2 and Tier 3 in order."""

    # -- Tier 1: exact part code ------------------------------------
    if part_code:
        row = reference.po_by_part(part_code)
        if row is not None:
            return _result_from_po(
                row,
                ACCEPTED,
                f"Exact part code match on {row.part_number}",
                "Tier 1 - exact part code",
                identity=_canonical_or_none(row.description, reference, packaging),
            )

    # -- Tier 2: identity match --------------------------------------
    resolution = resolve_identity(description, reference, packaging=packaging)
    if resolution is not None:
        ambiguous = guard_conflict(reference, resolution)
        if ambiguous:
            return MatchResult(
                status=UNMATCHED,
                reason=ambiguous,
                method="Tier 3 - ambiguous identity",
                identity=None,
            )
    if resolution is None:
        reason = "No alias in the identity reference resolves this description"
        if part_code:
            reason = (
                f"Part code '{part_code}' is not in PO history and "
                "no alias resolves this description"
            )
        return MatchResult(status=UNMATCHED, reason=reason, method="Tier 3 - no identity")

    input_size = size_hint or extract_size(description, role=resolution.role)
    size_required = packaging and resolution.entry.size_required

    if size_required and input_size.empty:
        return MatchResult(
            status=UNMATCHED,
            reason=(
                f"Identity '{resolution.canonical}' requires an exact size and "
                "none was stated - size is never substituted"
            ),
            method="Tier 3 - size not stated",
            identity=resolution.canonical,
        )

    candidates: list[PoRow] = []
    size_rejected: list[str] = []
    guard_rejected: list[str] = []

    for row in po_rows_by_identity(reference, packaging).get(resolution.canonical, []):
        if size_required:
            row_size = extract_size(row.description, role=resolution.role)
            if not row_size.covers(input_size):
                size_rejected.append(f"{row.part_number} ({row_size.describe()})")
                continue
        candidates.append(row)

    if len(candidates) == 1:
        row = candidates[0]
        detail = f" at {input_size.describe()}" if size_required else ""
        return _result_from_po(
            row,
            ACCEPTED,
            f"Identity '{resolution.canonical}'{detail} resolved to a single PO record",
            f"Tier 2 - identity match via alias '{resolution.alias}'",
            identity=resolution.canonical,
        )

    if len(candidates) > 1:
        row = min(candidates, key=lambda r: r.latest_unit_cost)
        result = _result_from_po(
            row,
            NEEDS_REVIEW,
            (
                f"{len(candidates)} PO records share identity "
                f"'{resolution.canonical}' - Purchasing must select the correct grade"
            ),
            f"Tier 2 - identity match via alias '{resolution.alias}'",
            identity=resolution.canonical,
        )
        result.candidates = [f"{r.part_number} - {r.description}" for r in candidates]
        return result

    # -- Tier 3: nothing matched -------------------------------------
    if size_rejected:
        reason = (
            f"No PO record for identity '{resolution.canonical}' at "
            f"{input_size.describe()} - size is never substituted "
            f"(rejected: {', '.join(size_rejected[:4])})"
        )
        method = "Tier 3 - size mismatch"
    elif guard_rejected:
        reason = (
            f"No PO record for identity '{resolution.canonical}'; "
            "candidates were blocked by the distinct-identity guard list"
        )
        method = "Tier 3 - guarded"
    else:
        reason = f"No PO record found for identity '{resolution.canonical}'"
        method = "Tier 3 - no PO record"

    return MatchResult(
        status=UNMATCHED, reason=reason, method=method, identity=resolution.canonical
    )


def _canonical_or_none(
    description: str, reference: ReferenceData, packaging: bool
) -> str | None:
    resolution = resolve_identity(description, reference, packaging=packaging)
    return resolution.canonical if resolution else None


def part_code_identity_conflict(
    description: str | None,
    row_description: str | None,
    reference: ReferenceData,
    packaging: bool = False,
) -> str | None:
    """Guard cross-check for a Tier 1 match.

    A Tier 1 match uses the PO row directly, as specified. When the typed
    description nevertheless resolves to an identity the guard list forbids
    pairing with the PO row's identity, that disagreement is surfaced to
    Purchasing rather than silently accepted.
    """
    typed = resolve_identity(description, reference, packaging=packaging)
    found = resolve_identity(row_description, reference, packaging=packaging)
    if typed is None or found is None:
        return None
    from .identity import guard_blocks

    blocked = guard_blocks(reference, typed.canonical, found.canonical)
    if blocked:
        return (
            f"Part code resolves to '{found.canonical}' but the description reads as "
            f"'{typed.canonical}' - {blocked}"
        )
    return None
