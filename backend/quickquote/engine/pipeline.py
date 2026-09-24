"""Orchestration: derive -> parse -> match -> cost -> flag -> confidence-score.

Every step is deterministic. Nothing in this module consults a model, and no
cost, match decision, confidence level or flag is ever produced from anything
but the reference tables and the parsed input.

This is the one cost path. The live estimate in the interface and the
generated quote package both run ``run_pipeline``; there is no second,
lighter calculation anywhere.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import date, datetime

from ..config import ACCEPTED, NEEDS_REVIEW, UNMATCHED
from ..reference.loader import ReferenceData, get_reference_data
from ..schemas import (
    CostDriver,
    CostSummary,
    CostedIngredient,
    CostedPackaging,
    PackagingLine,
    ParsedQuote,
    PriceBreak,
    PriceRecommendation,
    QuoteResult,
)
from .classify import classify_ingredient
from .confidence import line_confidence, quote_confidence
from .costing import cost_ingredient, cost_packaging
from .derive import derive_packaging, derive_serving, total_capsules
from .flags import build_flags
from .identity import resolve_identity
from .manufacturing import estimate_manufacturing, testing_cost_per_bottle
from .matching import match_line, part_code_identity_conflict
from .text import SizeSignature, extract_size, normalise_capsule_size

# Roles whose quantity per bottle is one unless the sheet says otherwise.
_UNIT_ROLES = {"bottle", "cap", "label", "neckband", "cotton", "desiccant", "seal", "scoop"}


def _packaging_qty(line: PackagingLine, parsed: ParsedQuote, reference: ReferenceData):
    """Resolve quantity per bottle for a packaging component.

    Capsule shells carry the component loss factor: a run breaks and wastes
    shells on setup, so a 60-count bottle consumes more than 60 shells.
    """
    if line.qty_per_bottle is not None:
        return line.qty_per_bottle
    role = (line.role or "").strip().lower()
    if role == "capsule_shell":
        if not parsed.product.count_per_bottle:
            return None
        return parsed.product.count_per_bottle * reference.component_loss_factor
    if role in _UNIT_ROLES:
        return 1.0
    # A shipper is one carton per pack-out, spread over the bottles it holds.
    return 1.0


def _units_per_container(line: PackagingLine, reference: ReferenceData) -> float | None:
    """How many bottles one purchased unit packs out."""
    resolution = resolve_identity(line.description, reference, packaging=True)
    if resolution is None:
        return None
    return resolution.entry.units_per_container


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
    auto_packaging: bool = True,
    with_price_breaks: bool = True,
) -> QuoteResult:
    """Turn a parsed quote into a complete review-ready quote package."""
    reference = reference or get_reference_data()
    as_of = as_of or date.today()
    product = parsed.product

    derivation_notes = derive_serving(product)

    if auto_packaging and not parsed.packaging:
        added, notes = derive_packaging(product, parsed.packaging, reference)
        parsed.packaging.extend(added)
        derivation_notes.extend(notes)

    mfg_loss = product.mfg_loss_factor or reference.mfg_loss_factor
    multi_ingredient = len(parsed.formula) > 1
    # R&D price gummy overage separately: depositing and curing cost far
    # more potency than blending and encapsulating.
    gummy = "gummy" in (product.dosage_form or "").lower()

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
            gummy=gummy,
        )
        costed = cost_ingredient(
            line, match, classification, reference, product.servings_per_bottle, mfg_loss
        )
        costed.identity = resolution.canonical if resolution else None
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
        costed = cost_packaging(
            line,
            match,
            reference,
            _packaging_qty(line, parsed, reference),
            _units_per_container(line, reference),
        )
        costed.confidence = line_confidence(match, reference, as_of)
        packaging.append(costed)

    # -- manufacturing -------------------------------------------------
    # Batch count comes from the weight of blend the run needs against the
    # blender that holds it, so the blend is weighed and measured first. Every
    # formula line contributes its mass whether or not it could be priced.
    blend_kg = sum(line.kg_per_bottle or 0.0 for line in ingredients) or None
    blend_density = blend_density_g_ml(ingredients, reference)

    manufacturing, machine_notes = estimate_manufacturing(
        product, len(parsed.formula), reference,
        blend_kg_per_bottle=blend_kg, blend_density_g_ml=blend_density,
    )
    derivation_notes.extend(machine_notes)

    testing, testing_basis = testing_cost_per_bottle(
        reference, len(parsed.formula), product.annual_volume_bottles
    )
    manufacturing.testing_per_bottle = testing
    manufacturing.testing_basis = testing_basis

    # -- roll-up -------------------------------------------------------
    summary = _summarise(ingredients, packaging, manufacturing, reference)
    summary.quote_confidence = quote_confidence(ingredients, packaging, reference)

    flags = build_flags(
        product, ingredients, packaging, manufacturing, reference, as_of, parsed.missing_fields
    )

    result = QuoteResult(
        quote_id=quote_id or uuid.uuid4().hex[:12],
        created_at=datetime.now(),
        parsed=parsed,
        ingredients=ingredients,
        packaging=packaging,
        manufacturing=manufacturing,
        summary=summary,
        flags=flags,
        derivation_notes=derivation_notes,
        reference_as_of=as_of,
        dataset_label=reference.dataset_label,
        dataset_is_demonstration=reference.is_demonstration,
    )
    result.pricing = build_pricing(
        summary, reference, product.customer, product.dosage_form
    )
    if with_price_breaks:
        result.price_breaks = build_price_breaks(parsed, reference, as_of)
    return result


def blend_density_g_ml(
    ingredients: list[CostedIngredient], reference: ReferenceData
) -> float | None:
    """The blend's own bulk density, or ``None`` when any material lacks one.

    Batch size is a volume limit, so density decides how many kilos a blender
    holds. A blend is only as measured as its least-known material: if one
    ingredient has no density on file, the whole figure is a guess, and
    Operations' 0.4 g/mL quoting assumption is the honest answer instead.
    """
    grams = 0.0
    millilitres = 0.0
    for line in ingredients:
        milligrams = line.formula_mg_per_serving
        if not milligrams:
            continue
        measured = reference.measured_density_for(
            line.input_part_code, line.match.matched_code
        )
        if measured is not None:
            density = measured.median_g_ml
        else:
            density, defaulted = reference.density_for(line.identity, line.overage_class)
            if defaulted:
                return None
        grams += milligrams / 1000.0
        millilitres += (milligrams / 1000.0) / density
    return grams / millilitres if millilitres else None


# ------------------------------------------------------- price breaks

def build_price_breaks(
    parsed: ParsedQuote, reference: ReferenceData, as_of: date
) -> list[PriceBreak]:
    """Cost the same formula across the volume ladder.

    Each rung is a full pipeline run at that volume, so per-batch costs
    amortise correctly and the machine can change with the run size. Material
    cost per bottle does not move with volume here: purchase-order history
    carries no volume-tiered pricing, so pretending it does would be invented
    data.
    """
    quoted = parsed.product.annual_volume_bottles
    volumes = sorted({*reference.volume_breaks, *( [quoted] if quoted else [] )})
    if not volumes:
        return []

    breaks: list[PriceBreak] = []
    for volume in volumes:
        scenario = deepcopy(parsed)
        scenario.product.annual_volume_bottles = volume
        # Clear a machine the system chose (not one the rep typed) so each rung
        # re-selects the work centre its own run size calls for.
        if scenario.product.derived.pop("machine", None):
            scenario.product.machine = None
        run = run_pipeline(
            scenario, reference, as_of=as_of,
            auto_packaging=False, with_price_breaks=False,
        )
        breaks.append(
            PriceBreak(
                volume_bottles=volume,
                primary_per_bottle=run.summary.primary_per_bottle,
                raw_materials=run.summary.raw_materials,
                packaging=run.summary.packaging,
                manufacturing=run.summary.manufacturing,
                machine=run.manufacturing.machine,
                is_quoted_volume=(volume == quoted),
            )
        )
    return breaks


# ---------------------------------------------------------- pricing

def build_pricing(
    summary: CostSummary,
    reference: ReferenceData,
    customer: str | None = None,
    dosage_form: str | None = None,
) -> list[PriceRecommendation]:
    """The price each margin requirement on this account asks for.

    ``margin = (price - cost) / price``, so ``price = cost / (1 - margin)``.
    An account can face two requirements at once -- the default terms are 20%
    including overhead and 30% excluding it -- and they are different tests
    against different cost bases, so one recommendation is produced per test
    and the tightest is marked binding. Finance own these numbers; the system
    reports what the rules ask for and does not set final pricing or terms.
    """
    rule = reference.margin_rule_for(customer)
    cost_incl = summary.primary_per_bottle
    if rule is None or cost_incl <= 0:
        return []

    # Tablets, powders and stick packs may be quoted down to the floor,
    # whichever account it is. The floor only ever relaxes a requirement.
    floor = reference.low_margin_floor_pct
    matched_form = reference.form_allows_floor(dosage_form)
    relaxed = bool(matched_form and floor is not None)

    cost_excl = summary.cost_excluding_overhead or cost_incl
    tests = (
        ("including overhead", rule.min_margin_incl_oh_pct, cost_incl),
        ("excluding overhead", rule.min_margin_excl_oh_pct, cost_excl),
    )

    recommendations: list[PriceRecommendation] = []
    for basis_label, required, cost in tests:
        if required is None:
            continue
        applied = required
        if relaxed and floor < required:
            applied = floor
        margin = applied / 100.0
        if margin >= 1.0:
            continue
        price = cost / (1 - margin)

        basis = (
            f"{applied:g}% margin on a ${cost:,.4f}/bottle cost "
            f"{basis_label}"
        )
        if relaxed and applied != required:
            basis += (
                f"; relaxed from {required:g}% because a {matched_form} may be "
                f"quoted down to {floor:g}%"
            )
        if summary.excluded_count:
            basis += (
                f"; cost excludes {summary.excluded_count} unresolved line(s), "
                "so this price is understated"
            )

        label = (
            f"{rule.rule.replace('_', ' ').title()} terms"
            if not rule.is_default else "Standard account terms"
        )
        recommendations.append(
            PriceRecommendation(
                channel=rule.rule,
                label=label,
                target_margin_pct=applied,
                price_per_bottle=price,
                margin_dollars=price - cost,
                basis=basis,
                cost_basis=basis_label,
                cost_per_bottle=cost,
                rule=rule.rule,
                form_floor_applied=relaxed and applied != required,
                notes=rule.notes,
            )
        )

    # Where an account faces more than one test, the price has to satisfy all
    # of them, so the highest is the one that actually binds.
    if recommendations:
        highest = max(recommendations, key=lambda item: item.price_per_bottle)
        highest.binding = True
    return recommendations


# ------------------------------------------------------------ summary

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
    if manufacturing.estimated:
        summary.manufacturing_labor = sum(
            step.labor_per_bottle or 0.0 for step in manufacturing.steps
        )
        summary.manufacturing_overhead = sum(
            step.overhead_per_bottle or 0.0 for step in manufacturing.steps
        )
    summary.testing = manufacturing.testing_per_bottle or 0.0
    summary.testing_basis = manufacturing.testing_basis
    summary.primary_per_bottle = (
        summary.raw_materials + summary.packaging + summary.manufacturing + summary.testing
    )
    # Materials, packaging and analytical testing are purchases and carry no
    # allocated overhead, so the only thing to strip is the manufacturing share.
    summary.cost_excluding_overhead = (
        summary.primary_per_bottle - summary.manufacturing_overhead
    )

    # Every cost-bearing line carries the same +/-10% band, so summing the
    # per-line lows and highs gives a worst-case envelope in which every input
    # moves the same way at once. It is not a statistical range.
    material_low = sum(
        line.cost_low or 0.0 for line in [*ingredients, *packaging] if line.match.accepted
    )
    material_high = sum(
        line.cost_high or 0.0 for line in [*ingredients, *packaging] if line.match.accepted
    )
    summary.low_per_bottle = material_low + summary.manufacturing + summary.testing
    summary.high_per_bottle = material_high + summary.manufacturing + summary.testing

    all_lines: list[CostedIngredient | CostedPackaging] = [*ingredients, *packaging]
    unmatched = [line for line in all_lines if line.match.status == UNMATCHED]
    review = [line for line in all_lines if line.match.status == NEEDS_REVIEW]
    costless = [
        line for line in all_lines
        if line.match.status == ACCEPTED and line.cost_per_bottle is None
    ]

    summary.unmatched_count = len(unmatched)
    summary.needs_review_count = len(review)
    summary.unmatched_items = [_label(line) for line in unmatched]
    summary.needs_review_items = [_label(line) for line in review]
    summary.tentative_exposure = sum(line.cost_per_bottle or 0.0 for line in review)

    summary.excluded_count = len(unmatched) + len(review) + len(costless)
    if summary.excluded_count:
        parts = []
        if unmatched:
            parts.append(f"{len(unmatched)} unmatched")
        if review:
            parts.append(f"{len(review)} needing review")
        if costless:
            parts.append(f"{len(costless)} with no cost")
        summary.excluded_note = (
            f"{summary.excluded_count} line(s) excluded ({', '.join(parts)}) - "
            "this total is understated"
        )

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
