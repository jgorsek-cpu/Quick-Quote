"""Deterministic cost calculation for ingredients and packaging.

Only Accepted lines produce numeric cost. Needs Review and Unmatched lines
carry their cost as exposure and are reported separately.
"""
from __future__ import annotations

from ..config import ACCEPTED, NEEDS_REVIEW
from ..reference.loader import ReferenceData
from ..schemas import CostedIngredient, CostedPackaging, FormulaLine, MatchResult, PackagingLine
from .classify import Classification


def cost_range(primary: float | None, reference: ReferenceData) -> tuple[float | None, float | None]:
    """Build the +/-10% band around a primary cost."""
    if primary is None:
        return None, None
    band = reference.range_pct
    return primary * (1 - band), primary * (1 + band)


def cost_ingredient(
    line: FormulaLine,
    match: MatchResult,
    classification: Classification,
    reference: ReferenceData,
    servings_per_bottle: int | None,
    mfg_loss_factor: float,
) -> CostedIngredient:
    """Cost one BOM line.

    ``formula mg/serving = claimed mg / potency x (1 + overage)``
    ``kg/bottle = formula mg/serving x servings per bottle / 1e6 x yield loss``
    ``cost/bottle = kg/bottle x cost per kg``
    """
    costed = CostedIngredient(
        name=line.name,
        match=match,
        input_part_code=line.part_code,
        claimed_mg=line.claimed_mg,
        potency=classification.potency,
        potency_source=classification.potency_source,
        overage_pct=classification.overage_pct,
        overage_class=classification.overage_class,
        overage_source=classification.overage_source,
    )
    if line.notes:
        costed.notes.append(line.notes)

    if line.claimed_mg is None:
        costed.notes.append("No claimed mg per serving on the quote sheet - cost not computed")
        return costed

    potency = classification.potency or 1.0
    if potency <= 0:
        costed.notes.append("Potency of zero is not usable - cost not computed")
        return costed

    formula_mg = line.claimed_mg / potency * (1 + classification.overage_pct)
    costed.formula_mg_per_serving = formula_mg

    if servings_per_bottle is None:
        costed.notes.append("Servings per bottle unknown - per-bottle cost not computed")
        return costed

    costed.kg_per_bottle = formula_mg * servings_per_bottle / 1_000_000 * mfg_loss_factor

    if match.latest_unit_cost is None:
        return costed

    if (match.uom or "KG").upper() != "KG":
        costed.notes.append(
            f"PO unit of measure is '{match.uom}', not KG - suspected unit-of-measure mismatch"
        )
        if match.status == ACCEPTED:
            match.status = NEEDS_REVIEW
            match.reason = (
                f"{match.reason}; demoted - PO unit of measure '{match.uom}' is not KG"
            )

    costed.cost_per_kg = match.latest_unit_cost
    costed.cost_per_bottle = costed.kg_per_bottle * match.latest_unit_cost
    costed.cost_low, costed.cost_high = cost_range(costed.cost_per_bottle, reference)
    return costed


def cost_packaging(
    line: PackagingLine,
    match: MatchResult,
    reference: ReferenceData,
    qty_per_bottle: float | None,
    units_per_container: float | None = None,
) -> CostedPackaging:
    """Cost one packaging line.

    Quote-sheet-listed cost wins over PO history. ``M`` is priced per thousand
    units. ``units_per_container`` spreads a purchased unit that holds many
    bottles -- a shipper, a master case, pallet materials -- across the bottles
    it packs out, so a 12-bottle carton is not charged once per bottle. A
    per-bottle component implying more than the UoM sanity ceiling from a PO
    unit cost is auto-demoted to Needs Review.
    """
    costed = CostedPackaging(
        role=line.role,
        description=line.description,
        match=match,
        input_part_code=line.part_code,
        qty_per_bottle=qty_per_bottle,
        units_per_container=units_per_container,
    )
    if line.notes:
        costed.notes.append(line.notes)

    from_quote_sheet = line.listed_unit_cost is not None
    unit_cost = line.listed_unit_cost if from_quote_sheet else match.latest_unit_cost
    uom = "EA" if from_quote_sheet else (match.uom or "EA").upper()

    if from_quote_sheet:
        costed.unit_cost_source = "Quote sheet listed cost"
    elif unit_cost is not None:
        costed.unit_cost_source = f"PO latest unit cost ({match.matched_code}, per {uom})"

    costed.unit_cost = unit_cost
    if unit_cost is None or qty_per_bottle is None:
        if qty_per_bottle is None:
            costed.notes.append("Quantity per bottle unknown - cost not computed")
        return costed

    divisor = 1000.0 if uom == "M" else 1.0
    if uom == "M":
        costed.notes.append("Priced per thousand units")

    if units_per_container and units_per_container > 1:
        divisor *= units_per_container
        costed.notes.append(
            f"One unit packs out {units_per_container:g} bottles; "
            "cost spread across them"
        )

    costed.cost_per_bottle = qty_per_bottle * unit_cost / divisor
    costed.cost_low, costed.cost_high = cost_range(costed.cost_per_bottle, reference)

    ceiling = reference.uom_sanity_max
    if (
        not from_quote_sheet
        and costed.cost_per_bottle > ceiling
        and match.status == ACCEPTED
    ):
        match.status = NEEDS_REVIEW
        match.reason = (
            f"{match.reason}; demoted - PO unit cost implies "
            f"${costed.cost_per_bottle:,.2f}/bottle, above the ${ceiling:,.2f} "
            "unit-of-measure sanity ceiling"
        )
        costed.notes.append("Auto-demoted by the unit-of-measure sanity check")

    return costed
