"""Domain model for quotes, lines, flags and results.

These dataclasses are the single shape that every producer (document parser,
manual sales-rep builder) feeds and every consumer (Excel workbook, PDF,
JSON API) reads.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from .config import ACCEPTED, EXCLUDED_DISPLAY


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@dataclass
class ProductSpec:
    """Everything the quote document says about the product itself."""

    customer: str | None = None
    brand: str | None = None
    formula_name: str | None = None
    customer_sku: str | None = None
    dosage_form: str | None = None
    capsule_size: str | None = None
    capsule_type: str | None = None
    serving_size: str | None = None
    servings_per_bottle: int | None = None
    count_per_bottle: int | None = None
    capsules_per_serving: int | None = None
    annual_volume_bottles: int | None = None
    moq: int | None = None
    timeline: str | None = None
    machine: str | None = None
    mfg_loss_factor: float | None = None
    claims: list[str] = field(default_factory=list)
    testing: list[str] = field(default_factory=list)
    # field name -> why the system filled it in. A rep's own value never
    # appears here, so the interface can show exactly what was assumed.
    derived: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class FormulaLine:
    """One ingredient as written on the customer's quote sheet."""

    name: str
    claimed_mg: float | None = None
    part_code: str | None = None
    source_row: int | None = None
    notes: str = ""


@dataclass
class PackagingLine:
    """One packaging component as written on the customer's quote sheet."""

    role: str
    description: str
    qty_per_bottle: float | None = None
    part_code: str | None = None
    listed_unit_cost: float | None = None
    source_row: int | None = None
    notes: str = ""


@dataclass
class ParsedQuote:
    """The full deterministic reading of an input document."""

    product: ProductSpec = field(default_factory=ProductSpec)
    formula: list[FormulaLine] = field(default_factory=list)
    packaging: list[PackagingLine] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    source_name: str = ""
    source_format: str = ""
    parser_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class MatchResult:
    """The outcome of running one input line through the matching tiers."""

    status: str
    reason: str
    method: str = ""
    matched_code: str | None = None
    po_description: str | None = None
    identity: str | None = None
    latest_unit_cost: float | None = None
    uom: str | None = None
    latest_po_date: date | None = None
    vendor: str | None = None
    unique_vendor_count: int | None = None
    po_count: int | None = None
    min_unit_cost_ever: float | None = None
    max_unit_cost_ever: float | None = None
    candidates: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.status == ACCEPTED


@dataclass
class CostedIngredient:
    """A BOM line after matching, potency, overage and costing."""

    name: str
    match: MatchResult
    identity: str | None = None
    input_part_code: str | None = None
    claimed_mg: float | None = None
    potency: float | None = None
    potency_source: str = ""
    overage_pct: float | None = None
    overage_class: str | None = None
    overage_source: str = ""
    formula_mg_per_serving: float | None = None
    kg_per_bottle: float | None = None
    cost_per_kg: float | None = None
    cost_per_bottle: float | None = None
    cost_low: float | None = None
    cost_high: float | None = None
    confidence: str = ""
    notes: list[str] = field(default_factory=list)

    def cost_display(self, value: float | None) -> str | float:
        if not self.match.accepted or value is None:
            return EXCLUDED_DISPLAY
        return round(value, 4)

    def to_dict(self) -> dict[str, Any]:
        payload = _json_safe(asdict(self))
        payload["accepted"] = self.match.accepted
        return payload


@dataclass
class CostedPackaging:
    """A packaging line after matching and costing."""

    role: str
    description: str
    match: MatchResult
    input_part_code: str | None = None
    qty_per_bottle: float | None = None
    units_per_container: float | None = None
    unit_cost: float | None = None
    unit_cost_source: str = ""
    cost_per_bottle: float | None = None
    cost_low: float | None = None
    cost_high: float | None = None
    confidence: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = _json_safe(asdict(self))
        payload["accepted"] = self.match.accepted
        return payload


@dataclass
class Flag:
    """A single review item owned by exactly one function."""

    owner: str
    item: str
    reason: str
    severity: str = "review"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ManufacturingStep:
    """One step of a process route, costed or explained."""

    step: str
    work_centre: str = ""
    basis: str = ""
    hours: float | None = None
    # Hours split out: run time scales with the order, set up is paid once per
    # batch, so the two move differently as volume changes.
    run_hours: float | None = None
    setup_hours: float | None = None
    per_batch: bool = False
    crew_size: int | None = None
    rate_per_hour: float | None = None
    cost_per_bottle: float | None = None
    # Split out because margin is quoted on two cost bases: one that carries
    # allocated overhead and one that does not.
    labor_per_bottle: float | None = None
    overhead_per_bottle: float | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ManufacturingEstimate:
    """Labor, overhead and bottling, or the reason none could be produced."""

    estimated: bool = False
    reason: str = ""
    dosage_form: str = ""
    component_count: int = 0
    steps: list["ManufacturingStep"] = field(default_factory=list)
    uncosted_steps: list[str] = field(default_factory=list)
    uncosted_critical_steps: list[str] = field(default_factory=list)
    testing_per_bottle: float | None = None
    testing_basis: str = ""
    compounding_hours: float | None = None
    compounding_per_bottle: float | None = None
    encapsulation_hours: float | None = None
    encapsulation_per_bottle: float | None = None
    bottling_per_bottle: float | None = None
    total_per_bottle: float = 0.0
    machine: str = ""
    bottling_line: str = ""
    machine_assumed: bool = False
    machine_basis: str = ""
    bottles_in_run: int | None = None
    total_capsules: float | None = None
    # Batch sizing: compounding, set up and cleaning are paid once per batch.
    batches: int = 1
    blender: str = ""
    blend_kg: float | None = None
    blend_density_g_ml: float | None = None
    blend_density_assumed: bool = True
    packaging_line: str = ""
    packaging_line_assumed: bool = True
    compounding_rate: float | None = None
    encapsulation_rate: float | None = None
    bottling_rate: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class CostDriver:
    label: str
    cost_per_bottle: float
    share_pct: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CostSummary:
    """Roll-up of every costed line, Accepted only in the primary figures."""

    raw_materials: float = 0.0
    packaging: float = 0.0
    manufacturing: float = 0.0
    # Manufacturing split into direct labor and allocated overhead, so a
    # margin quoted "excluding overhead" has a cost basis to sit on.
    manufacturing_labor: float = 0.0
    manufacturing_overhead: float = 0.0
    testing: float = 0.0
    testing_basis: str = ""
    primary_per_bottle: float = 0.0
    # Everything but allocated manufacturing overhead. Materials, packaging
    # and analytical testing are purchases, so they carry none.
    cost_excluding_overhead: float = 0.0
    low_per_bottle: float = 0.0
    high_per_bottle: float = 0.0
    quote_confidence: str = ""
    tentative_exposure: float = 0.0
    needs_review_count: int = 0
    unmatched_count: int = 0
    unmatched_items: list[str] = field(default_factory=list)
    needs_review_items: list[str] = field(default_factory=list)
    excluded_count: int = 0
    excluded_note: str = ""
    cost_drivers: list[CostDriver] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = _json_safe(asdict(self))
        return payload


@dataclass
class PriceBreak:
    """Cost per bottle at one volume on the ladder."""

    volume_bottles: int
    primary_per_bottle: float
    raw_materials: float
    packaging: float
    manufacturing: float
    machine: str
    is_quoted_volume: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PriceRecommendation:
    """The price one margin requirement asks for. Requires Finance review.

    A customer can be held to more than one requirement at once -- the default
    account terms are 20% including overhead *and* 30% excluding it -- so one
    of these is produced per test and the tightest is marked ``binding``.
    """

    channel: str
    label: str
    target_margin_pct: float
    price_per_bottle: float
    margin_dollars: float
    basis: str
    # Which cost the margin is taken against: "including overhead" or
    # "excluding overhead".
    cost_basis: str = "including overhead"
    cost_per_bottle: float = 0.0
    rule: str = ""
    binding: bool = False
    form_floor_applied: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QuoteResult:
    """The complete review-ready quote package."""

    quote_id: str
    created_at: datetime
    parsed: ParsedQuote
    ingredients: list[CostedIngredient] = field(default_factory=list)
    packaging: list[CostedPackaging] = field(default_factory=list)
    manufacturing: ManufacturingEstimate = field(default_factory=ManufacturingEstimate)
    summary: CostSummary = field(default_factory=CostSummary)
    flags: list[Flag] = field(default_factory=list)
    price_breaks: list[PriceBreak] = field(default_factory=list)
    pricing: list[PriceRecommendation] = field(default_factory=list)
    derivation_notes: list[str] = field(default_factory=list)
    reference_as_of: date | None = None
    # Which reference tables costed this quote, and whether they are real.
    dataset_label: str = ""
    dataset_is_demonstration: bool = False

    @property
    def product(self) -> ProductSpec:
        return self.parsed.product

    def flags_by_owner(self) -> dict[str, list[Flag]]:
        from .config import FLAG_OWNERS

        grouped: dict[str, list[Flag]] = {owner: [] for owner in FLAG_OWNERS}
        for flag in self.flags:
            grouped.setdefault(flag.owner, []).append(flag)
        return grouped

    def to_dict(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "created_at": self.created_at.isoformat(),
            "reference_as_of": self.reference_as_of.isoformat() if self.reference_as_of else None,
            # A stored quote keeps its own provenance: one costed in a
            # demonstration session must not read as real when reopened.
            "dataset_label": self.dataset_label,
            "dataset_is_demonstration": self.dataset_is_demonstration,
            "parsed": self.parsed.to_dict(),
            "product": self.product.to_dict(),
            "ingredients": [line.to_dict() for line in self.ingredients],
            "packaging": [line.to_dict() for line in self.packaging],
            "manufacturing": self.manufacturing.to_dict(),
            "summary": self.summary.to_dict(),
            "flags": [flag.to_dict() for flag in self.flags],
            "price_breaks": [item.to_dict() for item in self.price_breaks],
            "pricing": [item.to_dict() for item in self.pricing],
            "derivation_notes": list(self.derivation_notes),
            "flags_by_owner": {
                owner: [flag.to_dict() for flag in flags]
                for owner, flags in self.flags_by_owner().items()
            },
        }
