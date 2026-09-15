"""Orchestration: parse -> match -> cost -> flag -> confidence-score.

Every step is deterministic. Nothing in this module consults a model, and no
cost, match decision, confidence level or flag is ever produced from anything
but the reference tables and the parsed input.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from ..config import ACCEPTED, NEEDS_REVIEW, UNMATCHED
from ..reference.loader import ReferenceData, get_reference_data
from ..schemas import (
    CostDriver,
    CostSummary,
    CostedIngredient,
    CostedPackaging,
    ParsedQuote,
    PackagingLine,
    QuoteResult,
)
from .classify import classify_ingredient
from .confidence import line_confidence, quote_confidence
from .costing import cost_ingredient, cost_packaging
from .flags import build_flags
from .identity import resolve_identity
from .manufacturing import estimate_manufacturing
from .matching import match_line, part_code_identity_conflict
from .text import SizeSignature, extract_size, normalise_capsule_size

# Roles whose quantity per bottle is one unless the sheet says otherwise.
_UNIT_ROLES = {"bottle", "cap", "label", "neckband", "cotton", "desiccant", "seal", "scoop"}


def _packaging_qty(line: PackagingLine, parsed: ParsedQuote) -> float | None:
    """Resolve quantity per bottle for a packaging component."""
    if line.qty_per_bottle is not None:
        return line.qty_per_bottle
    role = (line.role or "").strip().lower()
    if role == "capsule_shell":
        return parsed.product.count_per_bottle
    if role == "shipper":
        return None
    if role in _UNIT_ROLES:
        return 1.0
    return 1.0


def _size_hint(line: PackagingLine, parsed: ParsedQuote) -> SizeSignature | None:
    """Fold the product's capsule size into a capsule shell line."""
    role = (line.role or "").strip().lower()
    stated = extract_size(line.description, role=role)
    if role != "capsule_shell":
        return stated or None
    if stated.capsule_size is not None:
        return stated
    capsule_size = normalise_capsule_size(parsed.product.capsule_size)
    if capsule_size is None:
        return stated
    return SizeSignature(
        capsule_size=capsule_size,
        volume_cc=stated.volume_cc,
        neck_mm=stated.neck_mm,
    )


def _capsule_description(line: PackagingLine, parsed: ParsedQuote) -> str:
    """Make sure a capsule shell line carries its material and size in text."""
    role = (line.role or "").strip().lower()
    description = line.description or ""
    if role != "capsule_shell":
        return description
    parts = [description]
    if not extract_size(description, role=role).capsule_size:
        capsule_size = normalise_capsule_size(parsed.product.capsule_size)
        if capsule_size:
            parts.append(f"size {capsule_size}")
    lowered = description.lower()
    if "capsule" not in lowered and parsed.product.capsule_type:
        parts.insert(0, f"{parsed.product.capsule_type} capsule")
    elif "capsule" not in lowered:
        parts.append("capsule")
    return " ".join(part for part in parts if part).strip()


def run_pipeline(
    parsed: ParsedQuote,
    reference: ReferenceData | None = None,
    as_of: date | None = None,
    quote_id: str | None = None,
) -> QuoteResult:
    """Turn a parsed quote into a complete review-ready quote package."""
    reference = reference or get_reference_data()
    as_of = as_of or date.today()
    product = parsed.product

    mfg_loss = product.mfg_loss_factor or reference.mfg_loss_factor
    multi_ingredient = len(parsed.formula) > 1

    # -- ingredients ---------------------------------------------------
    ingredients: list[CostedIngredient] = []
    for line in parsed.formula:
        match = match_line(line.name, line.part_code, reference, packaging=False)

        if match.status == ACCEPTED and match.method.startswith("Tier 1"):
            conflict = part_code_identity_conflict(
                line.name, match.po_description, reference, packaging=False
            )
        else:
            conflict = None

        resolution = resolve_identity(line.name, reference, packaging=False)
        classification = classify_ingredient(
            name=line.name,
            reference=reference,
            resolution=resolution,
            input_part_code=line.part_code,
            matched_code=match.matched_code,
            multi_ingredient=multi_ingredient,
        )
        costed = cost_ingredient(
            line, match, classification, reference, product.servings_per_bottle, mfg_loss
        )
        if conflict:
            costed.notes.append(conflict)
        costed.confidence = line_confidence(match, reference, as_of)
        ingredients.append(costed)

    # -- packaging -----------------------------------------------------
    packaging: list[CostedPackaging] = []
    for line in parsed.packaging:
        description = _capsule_description(line, parsed)
        match = match_line(
            description,
            line.part_code,
            reference,
            packaging=True,
            size_hint=_size_hint(line, parsed),
        )
        costed = cost_packaging(line, match, reference, _packaging_qty(line, parsed))
        costed.confidence = line_confidence(match, reference, as_of)
        packaging.append(costed)

    # -- manufacturing -------------------------------------------------
    manufacturing = estimate_manufacturing(product, len(parsed.formula), reference)

    # -- roll-up -------------------------------------------------------
    summary = _summarise(ingredients, packaging, manufacturing, reference)

    for line in ingredients:
        line.confidence = line_confidence(line.match, reference, as_of)
    for line in packaging:
        line.confidence = line_confidence(line.match, reference, as_of)
    summary.quote_confidence = quote_confidence(ingredients, packaging, reference)

    flags = build_flags(
        product, ingredients, packaging, manufacturing, reference, as_of, parsed.missing_fields
    )

    return QuoteResult(
        quote_id=quote_id or uuid.uuid4().hex[:12],
        created_at=datetime.now(),
        parsed=parsed,
        ingredients=ingredients,
        packaging=packaging,
        manufacturing=manufacturing,
        summary=summary,
        flags=flags,
        reference_as_of=as_of,
    )


def _summarise(
    ingredients: list[CostedIngredient],
    packaging: list[CostedPackaging],
    manufacturing,
    reference: ReferenceData,
) -> CostSummary:
    """Roll costed lines into the quote-level summary. Accepted lines only."""
    summary = CostSummary()

    summary.raw_materials = sum(
        line.cost_per_bottle or 0.0 for line in ingredients if line.match.accepted
    )
    summary.packaging = sum(
        line.cost_per_bottle or 0.0 for line in packaging if line.match.accepted
    )
    summary.manufacturing = manufacturing.total_per_bottle if manufacturing.estimated else 0.0
    summary.primary_per_bottle = summary.raw_materials + summary.packaging + summary.manufacturing

    # The +/-10% band applies to PO-derived material and packaging cost.
    # Manufacturing comes from labor and overhead rates, not PO history, so it
    # carries no band and enters both ends of the range unchanged.
    material_low = sum(
        line.cost_low or 0.0 for line in [*ingredients, *packaging] if line.match.accepted
    )
    material_high = sum(
        line.cost_high or 0.0 for line in [*ingredients, *packaging] if line.match.accepted
    )
    summary.low_per_bottle = material_low + summary.manufacturing
    summary.high_per_bottle = material_high + summary.manufacturing

    all_lines: list[CostedIngredient | CostedPackaging] = [*ingredients, *packaging]
    unmatched = [line for line in all_lines if line.match.status == UNMATCHED]
    review = [line for line in all_lines if line.match.status == NEEDS_REVIEW]

    summary.unmatched_count = len(unmatched)
    summary.needs_review_count = len(review)
    summary.unmatched_items = [_label(line) for line in unmatched]
    summary.needs_review_items = [_label(line) for line in review]
    summary.tentative_exposure = sum(line.cost_per_bottle or 0.0 for line in review)

    drivers: list[tuple[str, float]] = [
        (_label(line), line.cost_per_bottle)
        for line in all_lines
        if line.match.accepted and line.cost_per_bottle
    ]
    if manufacturing.estimated:
        if manufacturing.encapsulation_per_bottle:
            drivers.append(("Encapsulation labor + OH", manufacturing.encapsulation_per_bottle))
        if manufacturing.compounding_per_bottle:
            drivers.append(("Compounding labor + OH", manufacturing.compounding_per_bottle))
        if manufacturing.bottling_per_bottle:
            drivers.append(("Bottling labor + OH", manufacturing.bottling_per_bottle))

    drivers.sort(key=lambda item: item[1], reverse=True)
    total = summary.primary_per_bottle or 1.0
    summary.cost_drivers = [
        CostDriver(label=label, cost_per_bottle=cost, share_pct=cost / total * 100.0)
        for label, cost in drivers[:10]
    ]
    return summary


def _label(line: CostedIngredient | CostedPackaging) -> str:
    name = getattr(line, "name", None)
    if name:
        return name
    role = getattr(line, "role", "") or ""
    return f"{role.replace('_', ' ').title()} - {line.description}".strip(" -")
