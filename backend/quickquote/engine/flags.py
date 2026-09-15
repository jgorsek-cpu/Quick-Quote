"""Owner-keyed risk flags.

Every flag belongs to exactly one function. Owners are never collapsed,
reordered or renamed. Every default the system applies raises a flag that
names the default.
"""
from __future__ import annotations

from datetime import date

from ..config import ACCEPTED, NEEDS_REVIEW, UNMATCHED
from ..reference.loader import ReferenceData
from ..schemas import (
    CostedIngredient,
    CostedPackaging,
    Flag,
    ManufacturingEstimate,
    ProductSpec,
)

PURCHASING = "Purchasing"
RND = "R&D"
OPERATIONS = "Operations"
SALES = "Sales"
FINANCE = "Finance"


def _po_flags(
    label: str,
    line: CostedIngredient | CostedPackaging,
    reference: ReferenceData,
    as_of: date,
) -> list[Flag]:
    """Purchasing flags that come from the PO record behind an Accepted line."""
    flags: list[Flag] = []
    match = line.match
    if match.matched_code is None:
        return flags

    if match.latest_po_date is not None:
        age = (as_of - match.latest_po_date).days
        if age > reference.stale_po_days:
            flags.append(
                Flag(
                    PURCHASING,
                    label,
                    f"Latest PO for {match.matched_code} is {age} days old "
                    f"({match.latest_po_date.isoformat()}) - confirm current pricing.",
                )
            )

    low, high = match.min_unit_cost_ever, match.max_unit_cost_ever
    latest = match.latest_unit_cost
    if low is not None and high is not None and latest:
        if (high - low) > reference.variance_flag_pct * latest:
            flags.append(
                Flag(
                    PURCHASING,
                    label,
                    f"Major historical price variance: ${low:,.2f}-${high:,.2f} "
                    f"against a latest of ${latest:,.2f} - review before quoting.",
                )
            )

    if match.unique_vendor_count == 1:
        flags.append(
            Flag(
                PURCHASING,
                label,
                f"Single-supplier dependency - {match.vendor or 'one vendor'} "
                f"is the only source on record across {match.po_count} POs.",
            )
        )
    return flags


def build_flags(
    product: ProductSpec,
    ingredients: list[CostedIngredient],
    packaging: list[CostedPackaging],
    manufacturing: ManufacturingEstimate,
    reference: ReferenceData,
    as_of: date,
    missing_fields: list[str],
) -> list[Flag]:
    """Generate the full owner-keyed flag set for one quote."""
    flags: list[Flag] = []

    # -- Purchasing ---------------------------------------------------
    for line in ingredients:
        label = line.name
        if line.match.status == UNMATCHED:
            flags.append(Flag(PURCHASING, label, line.match.reason + " - cost unknown.", "blocking"))
        elif line.match.status == NEEDS_REVIEW:
            detail = line.match.reason
            if line.match.candidates:
                detail += f" Candidates: {'; '.join(line.match.candidates)}."
            flags.append(Flag(PURCHASING, label, detail, "blocking"))
        else:
            flags.extend(_po_flags(label, line, reference, as_of))

    for line in packaging:
        label = f"{line.role.replace('_', ' ').title()} - {line.description}"
        if line.match.status == UNMATCHED:
            flags.append(Flag(PURCHASING, label, line.match.reason + " - cost unknown.", "blocking"))
        elif line.match.status == NEEDS_REVIEW:
            detail = line.match.reason
            if line.match.candidates:
                detail += f" Candidates: {'; '.join(line.match.candidates)}."
            flags.append(Flag(PURCHASING, label, detail, "blocking"))
        else:
            flags.extend(_po_flags(label, line, reference, as_of))

    for line in [*ingredients, *packaging]:
        for note in line.notes:
            if "unit-of-measure" in note.lower():
                name = getattr(line, "name", None) or getattr(line, "description", "")
                flags.append(Flag(PURCHASING, name, note))

    # An Accepted line that still produced no cost contributes nothing to the
    # total. That silence is itself a risk, so it is surfaced rather than left
    # to be read as a genuine zero.
    costless = [
        line
        for line in packaging
        if line.match.status == ACCEPTED and line.cost_per_bottle is None
    ]
    for line in costless:
        label = f"{line.role.replace('_', ' ').title()} - {line.description}"
        if line.qty_per_bottle is None:
            reason = (
                "Matched to a PO record but quantity per bottle is not known, so this "
                "component contributes nothing to the total. Supply the quantity "
                "(for a shipper, bottles per case) before quoting."
            )
        else:
            reason = (
                "Matched to a PO record but no unit cost is available, so this "
                "component contributes nothing to the total."
            )
        flags.append(Flag(OPERATIONS, label, reason, "blocking"))

    # -- R&D ----------------------------------------------------------
    if not product.capsule_size and "capsule" in (product.dosage_form or "").lower():
        flags.append(Flag(RND, "Capsule size", "Capsule size not specified - fill check skipped."))
    if not product.dosage_form:
        flags.append(Flag(RND, "Dosage form", "Dosage form not specified on the quote sheet."))
    if not ingredients:
        flags.append(Flag(RND, "Ingredient list", "No ingredient list was found in the source document.", "blocking"))

    for line in ingredients:
        if line.potency_source.startswith("Default"):
            flags.append(
                Flag(RND, line.name, "Potency unknown - default 1.0 applied. Confirm form and claim basis.")
            )
        if line.overage_class == "default":
            flags.append(
                Flag(
                    RND,
                    line.name,
                    "Overage class could not be determined - 5% default applied. Confirm the correct class.",
                )
            )
        if line.claimed_mg is None:
            flags.append(Flag(RND, line.name, "No claimed mg per serving - cost could not be computed.", "blocking"))

    # -- Operations / R&D: capsule fill --------------------------------
    capacity = reference.capsule_capacity_mg(product.capsule_size)
    total_fill = sum(line.formula_mg_per_serving or 0.0 for line in ingredients)
    if capacity and total_fill:
        caps_per_serving = 1
        if product.count_per_bottle and product.servings_per_bottle:
            caps_per_serving = max(1, round(product.count_per_bottle / product.servings_per_bottle))
        fill_per_capsule = total_fill / caps_per_serving
        utilisation = fill_per_capsule / capacity
        if utilisation > 1.0:
            message = (
                f"Fill weight {fill_per_capsule:,.0f} mg per capsule exceeds the "
                f"{capacity:,.0f} mg capacity of size {product.capsule_size} "
                f"({utilisation:.0%}) - reformulation or a larger capsule is required."
            )
            flags.append(Flag(RND, "Capsule fill", message, "blocking"))
            flags.append(Flag(OPERATIONS, "Capsule fill", message, "blocking"))
        elif utilisation > 0.90:
            message = (
                f"Fill weight {fill_per_capsule:,.0f} mg per capsule is {utilisation:.0%} "
                f"of the {capacity:,.0f} mg capacity of size {product.capsule_size} - "
                "near capacity, confirm density."
            )
            flags.append(Flag(RND, "Capsule fill", message))
            flags.append(Flag(OPERATIONS, "Capsule fill", message))

    # -- Operations ---------------------------------------------------
    if manufacturing.machine_assumed:
        flags.append(
            Flag(OPERATIONS, "Machine", f"Machine not specified - assumed {manufacturing.machine}.")
        )
    if not manufacturing.estimated:
        flags.append(Flag(OPERATIONS, "Volume", manufacturing.reason, "blocking"))
    if product.mfg_loss_factor is None:
        flags.append(
            Flag(
                OPERATIONS,
                "Yield loss",
                f"Manufacturing yield loss not specified - default {reference.mfg_loss_factor} applied.",
            )
        )

    # -- Sales --------------------------------------------------------
    if not product.annual_volume_bottles:
        flags.append(Flag(SALES, "Volume", "Volume not specified - required before pricing can be finalised.", "blocking"))
    if not product.moq:
        flags.append(Flag(SALES, "MOQ", "MOQ not specified."))
    if not product.timeline:
        flags.append(Flag(SALES, "Timeline", "Timeline not specified."))

    # -- Finance ------------------------------------------------------
    unmatched = [line for line in [*ingredients, *packaging] if line.match.status == UNMATCHED]
    review = [line for line in [*ingredients, *packaging] if line.match.status == NEEDS_REVIEW]

    if unmatched:
        unmatched_ing = sum(1 for line in unmatched if isinstance(line, CostedIngredient))
        unmatched_pkg = len(unmatched) - unmatched_ing
        flags.append(
            Flag(
                FINANCE,
                "Unmatched items",
                f"{unmatched_ing} ingredient(s) and {unmatched_pkg} packaging item(s) have no cost. "
                "The primary total understates true cost.",
                "blocking",
            )
        )
    if costless:
        flags.append(
            Flag(
                FINANCE,
                "Costless accepted items",
                f"{len(costless)} accepted packaging item(s) produced no cost and add nothing "
                "to the total: " + "; ".join(
                    f"{line.role.replace('_', ' ')} - {line.description}" for line in costless
                ) + ".",
                "blocking",
            )
        )
    if review:
        exposure = sum(line.cost_per_bottle or 0.0 for line in review)
        flags.append(
            Flag(
                FINANCE,
                "Needs Review items",
                f"{len(review)} item(s) await Purchasing confirmation, carrying "
                f"${exposure:,.4f}/bottle of tentative cost excluded from the primary total.",
                "blocking",
            )
        )

    for field_name in missing_fields:
        flags.append(Flag(SALES, "Missing field", f"'{field_name}' was not present in the source document."))

    return _dedupe(flags)


def _dedupe(flags: list[Flag]) -> list[Flag]:
    """Drop exact duplicates while preserving owner order and first appearance."""
    from ..config import FLAG_OWNERS

    seen: set[tuple[str, str, str]] = set()
    unique: list[Flag] = []
    for flag in flags:
        key = (flag.owner, flag.item, flag.reason)
        if key in seen:
            continue
        seen.add(key)
        unique.append(flag)

    order = {owner: index for index, owner in enumerate(FLAG_OWNERS)}
    return sorted(unique, key=lambda f: order.get(f.owner, len(order)))
