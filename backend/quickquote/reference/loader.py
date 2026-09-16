"""Loading and access for system-maintained reference data.

All reference data lives in CSV files so that Purchasing, R&D and Finance can
maintain it without code changes. Everything here is deterministic lookup; no
value is ever inferred or extrapolated.
"""
from __future__ import annotations

import csv
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from ..config import REFERENCE_DATA_DIR


def _to_float(value: str | None, default: float | None = None) -> float | None:
    if value is None:
        return default
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _to_int(value: str | None, default: int | None = None) -> int | None:
    parsed = _to_float(value, None)
    return default if parsed is None else int(parsed)


def _to_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [
            {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(handle)
        ]


@dataclass(frozen=True)
class IdentityEntry:
    """A canonical identity and the aliases that resolve to it."""

    canonical: str
    aliases: tuple[str, ...]
    overage_class: str = "default"
    # A per-material overage that overrides the class. Blank means "inherit",
    # so the class table stays the single place a whole group is changed.
    overage_pct: float | None = None
    default_potency: float | None = None
    role: str = ""
    size_required: bool = False
    units_per_container: float | None = None
    notes: str = ""


@dataclass(frozen=True)
class Machine:
    """An encapsulation work centre and the run band it covers."""

    machine: str
    work_centre: str
    labor_rate_per_hour: float
    overhead_rate_per_hour: float
    capsules_per_hour: float
    min_capsules: float
    max_capsules: float | None
    notes: str = ""

    @property
    def combined_rate(self) -> float:
        return self.labor_rate_per_hour + self.overhead_rate_per_hour

    def covers(self, capsules: float) -> bool:
        if capsules < self.min_capsules:
            return False
        return self.max_capsules is None or capsules < self.max_capsules


@dataclass(frozen=True)
class WorkCentre:
    """Labor and overhead for one work centre."""

    work_centre: str
    labor_rate_per_hour: float
    overhead_rate_per_hour: float
    notes: str = ""

    @property
    def combined_rate(self) -> float:
        return self.labor_rate_per_hour + self.overhead_rate_per_hour


@dataclass(frozen=True)
class RouteStep:
    """One step of the process route for a dosage form."""

    dosage_form: str
    sequence: int
    step: str
    work_centre: str
    basis: str
    # A core step of the process. Cleaning is real cost but its absence does
    # not invalidate an estimate; an uncosted press or fill step does.
    critical: bool = True
    notes: str = ""


@dataclass(frozen=True)
class RunRate:
    """Throughput for a work centre, and whether anyone has confirmed it."""

    work_centre: str
    units_per_hour: float | None
    unit: str
    confirmed: bool
    notes: str = ""


@dataclass(frozen=True)
class TestingBand:
    """Testing cost by number of ingredients."""

    min_ingredients: int
    max_ingredients: int | None
    batch_cost: float
    per_unit_cost: float

    def covers(self, count: int) -> bool:
        if count < self.min_ingredients:
            return False
        return self.max_ingredients is None or count <= self.max_ingredients


@dataclass(frozen=True)
class OverageClass:
    """One row of R&D's overage guideline.

    R&D price four cases, not two: multi- and single-ingredient formulas, and
    the same again for gummies, where losses during depositing and curing are
    far higher. A value R&D records as non-numeric -- probiotics in a gummy
    are "Strain Dependent" -- is held as ``None`` and refused, never guessed.
    """

    key: str
    label: str = ""
    multi: float | None = None
    single: float | None = None
    gummy_multi: float | None = None
    gummy_single: float | None = None
    source: str = ""
    notes: str = ""

    def pct(self, multi_ingredient: bool, gummy: bool = False) -> float | None:
        if gummy:
            return self.gummy_multi if multi_ingredient else self.gummy_single
        return self.multi if multi_ingredient else self.single

    @property
    def from_guideline(self) -> bool:
        return "NOT IN" not in self.source.upper()


@dataclass(frozen=True)
class PotencyClaim:
    """One claim basis for one part.

    The asterisk in R&D's description marks the moiety the label claims, so a
    single part can carry several potencies: claiming ``L-Arginine* HCl`` is
    0.813 of the purchased salt, claiming ``L-Arginine HCl*`` is 0.983. Where
    a part has more than one, the engine names them and refuses to choose.
    """

    part_code: str
    claim_description: str
    potency_factor: float
    claims_whole_material: bool = False
    percent_element: str = ""
    element_conversion: str = ""
    min_purity: str = ""
    remarks: str = ""


@dataclass(frozen=True)
class MeasuredDensity:
    """Bulk density measured across received lots of one part.

    The median is the working value; the minimum is what a capsule has to fit
    in the worst lot on file. Some parts vary threefold between lots, so a
    formula that fits at the median is not necessarily one that fits.
    """

    part_code: str
    material: str
    median_g_ml: float
    min_g_ml: float
    max_g_ml: float
    lot_count: int

    @property
    def wide_spread(self) -> bool:
        return self.lot_count > 2 and self.max_g_ml > self.min_g_ml * 1.5


@dataclass(frozen=True)
class CapsuleFit:
    """A shell chosen for a fill weight under R&D's tamping model."""

    capsule_size: str
    volume_ml: float
    capacity_mg: float
    fill_mg: float
    loose_density_g_ml: float
    base_density_g_ml: float
    adjusted_density_g_ml: float
    steps: int

    @property
    def utilisation(self) -> float:
        return self.fill_mg / self.capacity_mg if self.capacity_mg else 0.0

    def basis(self) -> str:
        return (
            f"{self.fill_mg:,.0f} mg per capsule at a blend density of "
            f"{self.loose_density_g_ml:.2f} g/mL, read at "
            f"{self.adjusted_density_g_ml:g} g/mL after {self.steps} tamping "
            f"density steps from the {self.base_density_g_ml:g} column "
            f"(R&D Capsule Size Calculator)"
        )


@dataclass(frozen=True)
class PricingTarget:
    """A channel's target margin. Margin = (price - cost) / price."""

    channel: str
    target_margin_pct: float
    label: str
    notes: str = ""


@dataclass(frozen=True)
class PoRow:
    """One row of purchase-order history."""

    part_number: str
    description: str
    uom: str
    latest_unit_cost: float
    min_unit_cost_ever: float | None
    max_unit_cost_ever: float | None
    latest_po_date: date | None
    latest_vendor: str
    unique_vendor_count: int
    po_count: int
    total_spend: float = 0.0

    @property
    def price_spread(self) -> float | None:
        if self.min_unit_cost_ever is None or self.max_unit_cost_ever is None:
            return None
        return self.max_unit_cost_ever - self.min_unit_cost_ever

    def age_days(self, as_of: date) -> int | None:
        if self.latest_po_date is None:
            return None
        return (as_of - self.latest_po_date).days


@dataclass
class ReferenceData:
    """The complete set of system-maintained reference tables."""

    ingredient_identities: list[IdentityEntry] = field(default_factory=list)
    packaging_identities: list[IdentityEntry] = field(default_factory=list)
    overage: dict[str, OverageClass] = field(default_factory=dict)
    capsule_fill: dict[str, float] = field(default_factory=dict)
    capsule_volume: dict[str, float] = field(default_factory=dict)
    # Shell capacity in mg by size and blend density, from R&D's calculator.
    capsule_capacity: dict[str, dict[float, float]] = field(default_factory=dict)
    potency: dict[str, float] = field(default_factory=dict)
    potency_claims: dict[str, list[PotencyClaim]] = field(default_factory=dict)
    rates: dict[str, str] = field(default_factory=dict)
    guard_pairs: set[frozenset[str]] = field(default_factory=set)
    guard_reasons: dict[frozenset[str], str] = field(default_factory=dict)
    po_rows: list[PoRow] = field(default_factory=list)
    machines: list[Machine] = field(default_factory=list)
    work_centres: dict[str, WorkCentre] = field(default_factory=dict)
    routes: dict[str, list[RouteStep]] = field(default_factory=dict)
    run_rates: dict[str, RunRate] = field(default_factory=dict)
    cleaning_hours: dict[str, float | None] = field(default_factory=dict)
    bottling_rates: list[dict] = field(default_factory=list)
    testing_bands: list[TestingBand] = field(default_factory=list)
    bulk_density: dict[str, float] = field(default_factory=dict)
    measured_density: dict[str, MeasuredDensity] = field(default_factory=dict)
    pricing: list[PricingTarget] = field(default_factory=list)
    source_dir: Path = REFERENCE_DATA_DIR
    # Populated on first use by the matching engine: canonical identity ->
    # the PO rows that resolve to it. Resolving every row against every alias
    # for every line is what made matching quadratic.
    identity_index: dict | None = field(default=None, repr=False, compare=False)

    # -- rate helpers -------------------------------------------------
    def rate(self, key: str, default: float) -> float:
        return _to_float(self.rates.get(key), default) or default

    def text_rate(self, key: str, default: str) -> str:
        return self.rates.get(key) or default

    @property
    def labor_rate(self) -> float:
        return self.rate("labor_rate_per_hour", 29.39)

    @property
    def overhead_rate(self) -> float:
        return self.rate("overhead_rate_per_hour", 79.30)

    @property
    def combined_rate(self) -> float:
        return self.labor_rate + self.overhead_rate

    @property
    def range_pct(self) -> float:
        return self.rate("cost_range_pct", 10.0) / 100.0

    @property
    def mfg_loss_factor(self) -> float:
        return self.rate("default_mfg_loss_factor", 1.05)

    @property
    def stale_po_days(self) -> int:
        return int(self.rate("stale_po_days", 365))

    @property
    def variance_flag_pct(self) -> float:
        return self.rate("price_variance_flag_pct", 50.0) / 100.0

    @property
    def uom_sanity_max(self) -> float:
        return self.rate("uom_sanity_max_per_bottle", 5.00)

    @property
    def major_spend_pct(self) -> float:
        return self.rate("major_spend_pct", 10.0) / 100.0

    @property
    def default_machine(self) -> str:
        return self.text_rate("default_machine", "BOSCH 705 + CVC1")

    @property
    def component_loss_factor(self) -> float:
        return self.rate("component_loss_factor", 1.0)

    @property
    def stale_po_warn_days(self) -> int:
        return int(self.rate("stale_po_warn_days", 182))

    @property
    def use_work_centre_rates(self) -> bool:
        return self.rate("use_work_centre_rates", 0) >= 1

    @property
    def bottle_fill_ratio(self) -> float:
        return self.rate("bottle_fill_ratio", 0.80)

    @property
    def underfill_threshold(self) -> float:
        return self.rate("underfill_threshold", 0.55)

    @property
    def near_capacity_threshold(self) -> float:
        return self.rate("near_capacity_threshold", 0.90)

    @property
    def volume_breaks(self) -> list[int]:
        raw = self.rates.get("volume_breaks", "")
        breaks = []
        for part in str(raw).replace(",", ";").split(";"):
            value = _to_float(part)
            if value and value > 0:
                breaks.append(int(value))
        return sorted(set(breaks))

    def centre_rate(self, work_centre: str | None) -> float | None:
        """Combined labor + OH for a named work centre.

        With ``use_work_centre_rates`` off, every step is costed at the single
        blended pair the specification assumes, so a named centre still has to
        exist but its own rate is not used.
        """
        if not work_centre:
            return None
        centre = self.work_centres.get(work_centre)
        if centre is None:
            return None
        return centre.combined_rate if self.use_work_centre_rates else self.combined_rate

    def step_rate(self, step: str) -> float:
        """Combined labor + OH for a legacy named step."""
        if self.use_work_centre_rates:
            legacy = {"compounding": "Blend", "bottling": "Packaging"}
            centre = self.work_centres.get(legacy.get(step, step))
            if centre is not None:
                return centre.combined_rate
        return self.combined_rate

    def route_for(self, dosage_form: str | None) -> list[RouteStep]:
        """The process route for a dosage form, or an empty list."""
        key = (dosage_form or "capsule").strip().lower()
        for name, steps in self.routes.items():
            if key == name or name in key:
                return steps
        return []

    def known_dosage_forms(self) -> list[str]:
        return sorted(self.routes)

    def testing_for(self, ingredient_count: int) -> TestingBand | None:
        for band in self.testing_bands:
            if band.covers(ingredient_count):
                return band
        return None

    # Capsule sizes share a bottling-speed column on the CVC table.
    _BOTTLING_SIZE_GROUPS = {
        "000": "0/00", "00": "0/00", "0": "0/00",
        "00el": "0EL/00EL", "0el": "0EL/00EL",
        "1": "1", "2": "2", "3": "3", "4": "3",
    }

    def bottles_per_hour(self, count: int | None, capsule_size: str | None) -> float | None:
        """Bottling line speed for this count and capsule size.

        The line counts capsules into bottles, so a 500-count bottle runs far
        slower than a 60-count. Rows are discrete, so the first row at or above
        the requested count is used -- the slower, more conservative side.
        """
        if not self.bottling_rates or not count:
            return None
        group = self._BOTTLING_SIZE_GROUPS.get(
            str(capsule_size or "0").strip().lower(), "0/00"
        )
        rows = sorted(self.bottling_rates, key=lambda row: row["count"])
        chosen = next((row for row in rows if row["count"] >= count), rows[-1])
        value = chosen.get(group)
        return float(value) if value not in (None, "") else None

    def machine_for(self, capsules: float | None) -> Machine | None:
        """The encapsulation work centre whose band covers this run."""
        if capsules is None or not self.machines:
            return None
        for machine in self.machines:
            if machine.covers(capsules):
                return machine
        return self.machines[-1]

    def machine_by_name(self, name: str | None) -> Machine | None:
        if not name:
            return None
        wanted = str(name).strip().lower()
        for machine in self.machines:
            if machine.machine.lower() == wanted or machine.work_centre.lower() == wanted:
                return machine
        return None

    def density_for(self, identity: str | None, overage_class: str | None) -> tuple[float, bool]:
        """Bulk density in g/mL, and whether the global default was applied."""
        for key in (identity, overage_class):
            if key:
                found = self.bulk_density.get(str(key).strip().lower())
                if found:
                    return found, False
        return self.bulk_density.get("default", 0.55), True

    def capsule_volume_ml(self, capsule_size: str | None) -> float | None:
        if not capsule_size:
            return None
        return self.capsule_volume.get(str(capsule_size).strip().lower())

    def compounding_hours(self, component_count: int) -> float:
        if component_count <= 1:
            return self.rate("compounding_hours_1", 4.25)
        if component_count <= 10:
            return self.rate("compounding_hours_2_10", 4.75)
        if component_count <= 20:
            return self.rate("compounding_hours_11_20", 6.75)
        return self.rate("compounding_hours_21_plus", 11.75)

    # -- lookups ------------------------------------------------------
    def overage_class(self, overage_class: str | None) -> OverageClass | None:
        return self.overage.get((overage_class or "").strip().lower())

    def overage_pct(
        self, overage_class: str, multi_ingredient: bool, gummy: bool = False
    ) -> float | None:
        """Overage for one class, or ``None`` where R&D give no number.

        A gummy formula falls back to the caps-and-tablets column only when
        R&D leave the gummy one blank, and the caller is told which it got.
        """
        entry = self.overage.get((overage_class or "").strip().lower())
        if entry is None:
            return None
        return entry.pct(multi_ingredient, gummy)

    def capsule_capacity_mg(
        self, capsule_size: str | None, density_g_ml: float | None = None
    ) -> float | None:
        """Shell capacity in mg, for a blend of the given density if known.

        R&D's calculator tabulates capacity by size against a row of densities.
        A blend between two columns takes the lower one: overfilling a shell is
        the failure that reaches a customer.
        """
        if not capsule_size:
            return None
        size = str(capsule_size).strip().lower()
        if density_g_ml:
            grid = self.capsule_capacity.get(size)
            if grid:
                lower = [d for d in grid if d <= density_g_ml + 1e-9]
                if lower:
                    return grid[max(lower)]
                return grid[min(grid)]
        return self.capsule_fill.get(size)

    def capsule_densities(self, capsule_size: str | None) -> list[float]:
        """The density columns R&D's calculator tabulates for one size."""
        grid = self.capsule_capacity.get(str(capsule_size or "").strip().lower())
        return sorted(grid) if grid else []

    def density_columns(self) -> list[float]:
        """Every density column R&D's capacity chart carries."""
        columns: set[float] = set()
        for grid in self.capsule_capacity.values():
            columns.update(grid)
        return sorted(columns)

    @property
    def tamping_steps(self) -> int:
        """Density columns a tamping encapsulator is assumed to gain.

        R&D's calculator defaults to two: a blend measured at 0.50 g/mL is
        read at 0.70. It is a quoting heuristic, not a guaranteed fill, and
        Operations can recalibrate it from machine trials.
        """
        return int(self.rate("capsule_tamping_density_steps", 2))

    def tamped_density(
        self, density_g_ml: float | None, steps: int | None = None
    ) -> tuple[float, float] | None:
        """``(base, adjusted)`` density after R&D's tamping offset.

        The measured density is rounded *down* to a chart column first, so a
        blend between columns is never credited with the higher one.
        """
        columns = self.density_columns()
        if not columns or not density_g_ml or density_g_ml <= 0:
            return None
        lower = [value for value in columns if value <= density_g_ml + 1e-9]
        base = max(lower) if lower else columns[0]
        steps = self.tamping_steps if steps is None else steps
        index = min(columns.index(base) + max(0, steps), len(columns) - 1)
        return base, columns[index]

    def tamped_capacity_mg(
        self, capsule_size: str | None, density_g_ml: float | None, steps: int | None = None
    ) -> float | None:
        """Shell capacity in mg for a blend of this density, after tamping."""
        adjusted = self.tamped_density(density_g_ml, steps)
        if adjusted is None:
            return None
        return self.capsule_capacity_mg(capsule_size, adjusted[1])

    def recommend_capsule_size(
        self,
        fill_mg: float | None,
        density_g_ml: float | None,
        steps: int | None = None,
        sizes: Iterable[str] | None = None,
    ) -> CapsuleFit | None:
        """Smallest shell that holds this fill under R&D's tamping model.

        Only sizes with a capacity row are considered, so the system never
        recommends a shell it has no data for.
        """
        adjusted = self.tamped_density(density_g_ml, steps)
        if adjusted is None or not fill_mg or fill_mg <= 0:
            return None
        base, tamped = adjusted
        allowed = None if sizes is None else {str(s).strip().lower() for s in sizes}

        best: CapsuleFit | None = None
        for size, grid in self.capsule_capacity.items():
            if allowed is not None and size not in allowed:
                continue
            capacity = grid.get(tamped)
            volume = self.capsule_volume.get(size)
            if not capacity or not volume or capacity < fill_mg:
                continue
            if best is None or volume < best.volume_ml:
                best = CapsuleFit(
                    capsule_size=size,
                    volume_ml=volume,
                    capacity_mg=capacity,
                    fill_mg=fill_mg,
                    loose_density_g_ml=density_g_ml,
                    base_density_g_ml=base,
                    adjusted_density_g_ml=tamped,
                    steps=self.tamping_steps if steps is None else steps,
                )
        return best

    def potency_claims_for_part(self, part_code: str | None) -> list[PotencyClaim]:
        if not part_code:
            return []
        return self.potency_claims.get(part_code.strip().upper(), [])

    def potency_for_part(self, part_code: str | None) -> float | None:
        """The potency of one part, or ``None`` where R&D record more than one.

        A part with several claim bases has no single answer; the caller must
        ask which moiety is being claimed rather than pick one.
        """
        if not part_code:
            return None
        claims = self.potency_claims_for_part(part_code)
        if len(claims) == 1:
            return claims[0].potency_factor
        if claims:
            return None
        return self.potency.get(part_code.strip().upper())

    def measured_density_for(self, *part_codes: str | None) -> MeasuredDensity | None:
        for code in part_codes:
            if code:
                found = self.measured_density.get(str(code).strip().upper())
                if found:
                    return found
        return None

    def po_by_part(self, part_code: str | None) -> PoRow | None:
        if not part_code:
            return None
        wanted = part_code.strip().upper()
        for row in self.po_rows:
            if row.part_number.strip().upper() == wanted:
                return row
        return None

    def is_guarded(self, identity_a: str, identity_b: str) -> bool:
        return frozenset({identity_a, identity_b}) in self.guard_pairs

    def guard_reason(self, identity_a: str, identity_b: str) -> str:
        return self.guard_reasons.get(
            frozenset({identity_a, identity_b}), "Distinct-identity guard list"
        )

    def identity_entry(self, canonical: str, packaging: bool = False) -> IdentityEntry | None:
        table = self.packaging_identities if packaging else self.ingredient_identities
        for entry in table:
            if entry.canonical == canonical:
                return entry
        return None


def is_component_part(row: PoRow) -> bool:
    """True when a PO row is a packaging component rather than a raw material.

    Unit of measure is the reliable signal: raw materials are bought by weight,
    components each or per thousand. Capsule shells are priced per thousand and
    are components, not ingredients -- reading that from the part prefix put
    three of the highest-spend parts in the wrong queue.
    """
    if (row.uom or "").upper() in ("EA", "M"):
        return True
    return row.part_number.upper().startswith(("K", "PK"))


def _load_identities(rows: Iterable[dict[str, str]], packaging: bool) -> list[IdentityEntry]:
    entries: list[IdentityEntry] = []
    for row in rows:
        canonical = (row.get("canonical") or "").strip().lower()
        if not canonical:
            continue
        aliases = tuple(
            alias.strip().lower()
            for alias in (row.get("aliases") or "").split("|")
            if alias.strip()
        )
        if canonical not in aliases:
            aliases = (canonical,) + aliases
        entries.append(
            IdentityEntry(
                canonical=canonical,
                aliases=aliases,
                overage_class=(row.get("overage_class") or "default").strip().lower(),
                overage_pct=_to_float(row.get("overage_pct")),
                default_potency=_to_float(row.get("default_potency")),
                role=(row.get("role") or "").strip().lower(),
                size_required=str(row.get("size_required", "")).strip() in {"1", "true", "yes"},
                units_per_container=_to_float(row.get("units_per_container")),
                notes=row.get("notes", ""),
            )
        )
    return entries


def _infer_uom(part_number: str, description: str) -> str:
    text = f"{part_number} {description}".lower()
    if "capsule shell" in text or "capsule," in text:
        return "M"
    if part_number.upper().startswith(("PK-", "PKG", "CT-")):
        return "EA"
    return "KG"


def load_reference_data(directory: Path | None = None) -> ReferenceData:
    """Read every reference table from ``directory``."""
    base = Path(directory or REFERENCE_DATA_DIR)
    data = ReferenceData(source_dir=base)

    data.ingredient_identities = _load_identities(
        _read_csv(base / "ingredient_identity.csv"), packaging=False
    )
    data.packaging_identities = _load_identities(
        _read_csv(base / "packaging_identity.csv"), packaging=True
    )

    for row in _read_csv(base / "overage.csv"):
        key = (row.get("overage_class") or "").strip().lower()
        if not key:
            continue
        multi = _to_float(row.get("multi_ingredient_pct"))
        single = _to_float(row.get("single_ingredient_pct"), multi)
        if multi is None:
            continue
        # A blank gummy column means R&D give no number for that case -- for
        # probiotics it reads "Strain Dependent" -- and stays unknown.
        gummy_multi = _to_float(row.get("gummy_multi_pct"))
        gummy_single = _to_float(row.get("gummy_single_pct"), gummy_multi)
        data.overage[key] = OverageClass(
            key=key,
            label=(row.get("label") or "").strip(),
            multi=multi / 100.0,
            single=(single if single is not None else multi) / 100.0,
            gummy_multi=None if gummy_multi is None else gummy_multi / 100.0,
            gummy_single=None if gummy_single is None else gummy_single / 100.0,
            source=(row.get("source") or "").strip(),
            notes=(row.get("notes") or "").strip(),
        )

    for row in _read_csv(base / "capsule_fill.csv"):
        size = (row.get("capsule_size") or "").strip().lower()
        capacity = _to_float(row.get("mg_capacity"))
        if size and capacity is not None:
            data.capsule_fill[size] = capacity
        volume = _to_float(row.get("volume_ml"))
        if size and volume:
            data.capsule_volume[size] = volume

    for row in _read_csv(base / "machines.csv"):
        name = (row.get("machine") or "").strip()
        rate_l = _to_float(row.get("labor_rate_per_hour"))
        rate_o = _to_float(row.get("overhead_rate_per_hour"))
        speed = _to_float(row.get("capsules_per_hour"))
        if not name or rate_l is None or rate_o is None or not speed:
            continue
        data.machines.append(
            Machine(
                machine=name,
                work_centre=(row.get("work_centre") or name).strip(),
                labor_rate_per_hour=rate_l,
                overhead_rate_per_hour=rate_o,
                capsules_per_hour=speed,
                min_capsules=_to_float(row.get("min_capsules"), 0.0) or 0.0,
                max_capsules=_to_float(row.get("max_capsules")),
                notes=row.get("notes", ""),
            )
        )
    data.machines.sort(key=lambda machine: machine.min_capsules)

    for row in _read_csv(base / "work_centres.csv"):
        name = (row.get("work_centre") or "").strip()
        rate_l = _to_float(row.get("labor_rate_per_hour"))
        rate_o = _to_float(row.get("overhead_rate_per_hour"))
        if name and rate_l is not None and rate_o is not None:
            data.work_centres[name] = WorkCentre(
                work_centre=name,
                labor_rate_per_hour=rate_l,
                overhead_rate_per_hour=rate_o,
                notes=row.get("notes", ""),
            )

    for row in _read_csv(base / "process_routes.csv"):
        form = (row.get("dosage_form") or "").strip().lower()
        step = (row.get("step") or "").strip()
        if not form or not step:
            continue
        data.routes.setdefault(form, []).append(
            RouteStep(
                dosage_form=form,
                sequence=_to_int(row.get("sequence"), 0) or 0,
                step=step,
                work_centre=(row.get("work_centre") or "").strip(),
                basis=(row.get("basis") or "").strip().lower(),
                critical=str(row.get("critical", "1")).strip() not in {"0", "false", "no"},
                notes=row.get("notes", ""),
            )
        )
    for steps in data.routes.values():
        steps.sort(key=lambda item: item.sequence)

    for row in _read_csv(base / "run_rates.csv"):
        name = (row.get("work_centre") or "").strip()
        if not name:
            continue
        data.run_rates[name] = RunRate(
            work_centre=name,
            units_per_hour=_to_float(row.get("units_per_hour")),
            unit=(row.get("unit") or "units").strip(),
            confirmed=str(row.get("confirmed", "")).strip() in {"1", "true", "yes"},
            notes=row.get("notes", ""),
        )

    for row in _read_csv(base / "cleaning_hours.csv"):
        name = (row.get("work_centre") or "").strip()
        if name:
            data.cleaning_hours[name] = _to_float(row.get("hours_per_batch"))

    for row in _read_csv(base / "bottling_rates.csv"):
        count = _to_int(row.get("count"))
        if count:
            entry = {"count": count}
            entry.update({k: v for k, v in row.items() if k != "count"})
            data.bottling_rates.append(entry)

    for row in _read_csv(base / "testing_costs.csv"):
        minimum = _to_int(row.get("min_ingredients"))
        batch = _to_float(row.get("batch_cost"))
        if minimum is None or batch is None:
            continue
        data.testing_bands.append(
            TestingBand(
                min_ingredients=minimum,
                max_ingredients=_to_int(row.get("max_ingredients")),
                batch_cost=batch,
                per_unit_cost=_to_float(row.get("per_unit_cost"), 0.0) or 0.0,
            )
        )

    for row in _read_csv(base / "bulk_density.csv"):
        key = (row.get("key") or "").strip().lower()
        density = _to_float(row.get("bulk_density_g_ml"))
        if key and density and density > 0:
            data.bulk_density[key] = density

    for row in _read_csv(base / "pricing.csv"):
        channel = (row.get("channel") or "").strip().lower()
        margin = _to_float(row.get("target_margin_pct"))
        if channel and margin is not None:
            data.pricing.append(
                PricingTarget(
                    channel=channel,
                    target_margin_pct=margin,
                    label=(row.get("label") or channel.title()).strip(),
                    notes=row.get("notes", ""),
                )
            )

    for row in _read_csv(base / "potency.csv"):
        # R&D key their sheet on the part code; the table this replaced used
        # "key" for the same thing. Both are read so either shape loads.
        key = (row.get("part_code") or row.get("key") or "").strip().upper()
        factor = _to_float(row.get("potency_factor"))
        if not key or not factor:
            continue
        data.potency.setdefault(key, factor)
        description = (row.get("claim_description") or "").strip()
        data.potency_claims.setdefault(key, []).append(
            PotencyClaim(
                part_code=key,
                claim_description=description,
                potency_factor=factor,
                claims_whole_material=(row.get("claims_whole_material") or "").strip() == "1",
                percent_element=(row.get("percent_element") or "").strip(),
                element_conversion=(row.get("element_conversion") or "").strip(),
                min_purity=(row.get("min_purity") or "").strip(),
                remarks=(row.get("remarks") or "").strip(),
            )
        )

    for row in _read_csv(base / "bulk_density_measured.csv"):
        key = (row.get("part_code") or "").strip().upper()
        median = _to_float(row.get("median_g_ml"))
        if not key or not median or median <= 0:
            continue
        data.measured_density[key] = MeasuredDensity(
            part_code=key,
            material=(row.get("material") or "").strip(),
            median_g_ml=median,
            min_g_ml=_to_float(row.get("min_g_ml"), median) or median,
            max_g_ml=_to_float(row.get("max_g_ml"), median) or median,
            lot_count=_to_int(row.get("lot_count"), 1) or 1,
        )

    for row in _read_csv(base / "capsule_capacity.csv"):
        size = (row.get("capsule_size") or "").strip().lower()
        if not size:
            continue
        grid = {}
        for column, value in row.items():
            density = _to_float(column)
            capacity = _to_float(value)
            if density and capacity:
                grid[density] = capacity
        if grid:
            data.capsule_capacity[size] = grid
        volume = _to_float(row.get("volume_ml"))
        if volume:
            data.capsule_volume.setdefault(size, volume)

    for row in _read_csv(base / "labor_rates.csv"):
        key = (row.get("key") or "").strip().lower()
        if key:
            data.rates[key] = row.get("value", "")

    for row in _read_csv(base / "distinct_guard.csv"):
        a = (row.get("identity_a") or "").strip().lower()
        b = (row.get("identity_b") or "").strip().lower()
        if a and b:
            pair = frozenset({a, b})
            data.guard_pairs.add(pair)
            data.guard_reasons[pair] = row.get("reason", "Distinct-identity guard list")

    data.po_rows = load_po_history(base / "po_history.csv")
    return data


def load_po_history(path: Path) -> list[PoRow]:
    """Read a PO history CSV export into :class:`PoRow` records."""
    rows: list[PoRow] = []
    for row in _read_csv(path):
        part = (row.get("part_number") or "").strip()
        cost = _to_float(row.get("latest_unit_cost"))
        if not part or cost is None:
            continue
        description = row.get("description", "")
        rows.append(
            PoRow(
                part_number=part,
                description=description,
                uom=(row.get("uom") or _infer_uom(part, description)).strip().upper(),
                latest_unit_cost=cost,
                min_unit_cost_ever=_to_float(row.get("min_unit_cost_ever")),
                max_unit_cost_ever=_to_float(row.get("max_unit_cost_ever")),
                latest_po_date=_to_date(row.get("latest_po_date")),
                latest_vendor=row.get("latest_vendor", ""),
                unique_vendor_count=_to_int(row.get("unique_vendor_count"), 0) or 0,
                po_count=_to_int(row.get("po_count"), 0) or 0,
                total_spend=_to_float(row.get("total_spend"), 0.0) or 0.0,
            )
        )
    return rows


_CACHE: ReferenceData | None = None
_LOCK = threading.Lock()


def get_reference_data(refresh: bool = False) -> ReferenceData:
    """Return the process-wide reference data, loading it on first use."""
    global _CACHE
    with _LOCK:
        if _CACHE is None or refresh:
            _CACHE = load_reference_data()
        return _CACHE
