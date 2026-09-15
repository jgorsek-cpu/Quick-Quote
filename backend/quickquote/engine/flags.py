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
        stamp = match.latest_po_date.isoformat()
        if age > reference.stale_po_days:
            flags.append(
                Flag(
                    PURCHASING,
                    label,
                    f"Latest PO for {match.matched_code} is {age} days old "
                    f"({stamp}) - price is over a year old, confirm before quoting.",
                    "blocking",
                )
            )
        elif age > reference.stale_po_warn_days:
            flags.append(
                Flag(
                    PURCHASING,
                    label,
                    f"Latest PO for {match.matched_code} is {age} days old "
                    f"({stamp}) - over six months, confirm current pricing.",
                )
            )
    else:
        flags.append(
            Flag(
                PURCHASING,
                label,
                f"No purchase-order date on record for {match.matched_code} - "
                "the age of this price is unknown.",
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


def _smaller_shell(
    reference: ReferenceData, needed_ml: float, current_size: str | None
) -> str | None:
    """The smallest stocked shell that still holds this fill, if smaller."""
    current = reference.capsule_volume_ml(current_size)
    if current is None:
        return None
    candidates = [
        (volume, size)
        for size, volume in reference.capsule_volume.items()
        if needed_ml <= volume * reference.near_capacity_threshold and volume < current
    ]
    if not candidates:
        return None
    return min(candidates)[1]


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
    # Fill is a volume question, not a weight one: 500mg of silicon dioxide
    # and 500mg of magnesium oxide occupy very different space. Each material
    # contributes its own bulk volume.
    capsule_size = product.capsule_size
    shell_ml = reference.capsule_volume_ml(capsule_size)
    caps_per_serving = product.capsules_per_serving or 1
    if product.count_per_bottle and product.servings_per_bottle and not product.capsules_per_serving:
        caps_per_serving = max(1, round(product.count_per_bottle / product.servings_per_bottle))

    fill_ml = 0.0
    density_defaulted: list[str] = []
    for line in ingredients:
        milligrams = line.formula_mg_per_serving
        if not milligrams:
            continue
        density, defaulted = reference.density_for(line.identity, line.overage_class)
        if defaulted:
            density_defaulted.append(line.name)
        fill_ml += (milligrams / 1000.0) / density

    for name in density_defaulted:
        flags.append(
            Flag(
                RND,
                name,
                f"Bulk density not on file - the reference default "
                f"({reference.bulk_density.get('default', 0.55)} g/mL) was used for the "
                "fill check. Confirm the true density.",
            )
        )

    if shell_ml and fill_ml:
        per_capsule_ml = fill_ml / caps_per_serving
        utilisation = per_capsule_ml / shell_ml
        detail = (
            f"{per_capsule_ml:.3f} mL per capsule against {shell_ml:.2f} mL of "
            f"size {capsule_size} shell ({utilisation:.0%})"
        )
        if utilisation > 1.0:
            message = (
                f"Fill volume exceeds the shell: {detail}. A larger capsule, more "
                "capsules per serving, or reformulation is required."
            )
            flags.append(Flag(RND, "Capsule fill", message, "blocking"))
            flags.append(Flag(OPERATIONS, "Capsule fill", message, "blocking"))
        elif utilisation > reference.near_capacity_threshold:
            message = f"Fill is near shell capacity: {detail}. Confirm on a trial run."
            flags.append(Flag(RND, "Capsule fill", message))
            flags.append(Flag(OPERATIONS, "Capsule fill", message))
        elif utilisation < reference.underfill_threshold:
            smaller = _smaller_shell(reference, per_capsule_ml, capsule_size)
            message = f"Capsule is over-sized for the fill: {detail}."
            if smaller:
                message += (
                    f" Size {smaller} would hold it and costs less per thousand - "
                    "confirm with R&D and Purchasing."
                )
            flags.append(Flag(RND, "Capsule fill", message))

    # -- Operations ---------------------------------------------------
    # Only forms that actually run on a selectable machine raise this; a tablet
    # or gummy line has its own step flags instead.
    if manufacturing.machine_assumed and manufacturing.machine:
        basis = manufacturing.machine_basis or "default assumption"
        line = manufacturing.bottling_line
        assumed = f"{manufacturing.machine} + {line}" if line else manufacturing.machine
        flags.append(
            Flag(OPERATIONS, "Machine",
                 f"Machine not specified - assumed {assumed} ({basis}).")
        )
    if not manufacturing.estimated:
        flags.append(Flag(OPERATIONS, "Manufacturing", manufacturing.reason, "blocking"))

    # A step with no rate on file is named individually, so Operations knows
    # exactly which number to supply rather than being told the total is wrong.
    for step in manufacturing.steps:
        if step.cost_per_bottle is None and step.reason:
            severity = "blocking" if step.step in manufacturing.uncosted_critical_steps else "review"
            flags.append(Flag(OPERATIONS, f"{manufacturing.dosage_form} - {step.step}",
                              step.reason, severity))

    if manufacturing.estimated and manufacturing.uncosted_steps:
        flags.append(
            Flag(
                FINANCE,
                "Uncosted process steps",
                f"{len(manufacturing.uncosted_steps)} manufacturing step(s) carry no cost "
                f"({', '.join(manufacturing.uncosted_steps)}), so the total is understated.",
            )
        )
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

    # Fields with their own dedicated flag above are not repeated here, so
    # "MOQ not specified" is one item rather than two.
    already_flagged = {flag.item.strip().lower() for flag in flags}
    for field_name in missing_fields:
        if field_name.strip().lower() in already_flagged:
            continue
        flags.append(
            Flag(SALES, field_name, f"Not present in the source document.")
        )

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
