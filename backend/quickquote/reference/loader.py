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
    default_potency: float | None = None
    role: str = ""
    size_required: bool = False
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
    overage: dict[str, dict[str, float]] = field(default_factory=dict)
    capsule_fill: dict[str, float] = field(default_factory=dict)
    potency: dict[str, float] = field(default_factory=dict)
    rates: dict[str, str] = field(default_factory=dict)
    guard_pairs: set[frozenset[str]] = field(default_factory=set)
    guard_reasons: dict[frozenset[str], str] = field(default_factory=dict)
    po_rows: list[PoRow] = field(default_factory=list)
    source_dir: Path = REFERENCE_DATA_DIR

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

    def compounding_hours(self, component_count: int) -> float:
        if component_count <= 1:
            return self.rate("compounding_hours_1", 4.25)
        if component_count <= 10:
            return self.rate("compounding_hours_2_10", 4.75)
        if component_count <= 20:
            return self.rate("compounding_hours_11_20", 6.75)
        return self.rate("compounding_hours_21_plus", 11.75)

    # -- lookups ------------------------------------------------------
    def overage_pct(self, overage_class: str, multi_ingredient: bool) -> float | None:
        entry = self.overage.get((overage_class or "").strip().lower())
        if entry is None:
            return None
        key = "multi" if multi_ingredient else "single"
        return entry.get(key)

    def capsule_capacity_mg(self, capsule_size: str | None) -> float | None:
        if not capsule_size:
            return None
        return self.capsule_fill.get(str(capsule_size).strip().lower())

    def potency_for_part(self, part_code: str | None) -> float | None:
        if not part_code:
            return None
        return self.potency.get(part_code.strip().upper())

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
                default_potency=_to_float(row.get("default_potency")),
                role=(row.get("role") or "").strip().lower(),
                size_required=str(row.get("size_required", "")).strip() in {"1", "true", "yes"},
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
        data.overage[key] = {"multi": multi / 100.0, "single": (single or multi) / 100.0}

    for row in _read_csv(base / "capsule_fill.csv"):
        size = (row.get("capsule_size") or "").strip().lower()
        capacity = _to_float(row.get("mg_capacity"))
        if size and capacity is not None:
            data.capsule_fill[size] = capacity

    for row in _read_csv(base / "potency.csv"):
        key = (row.get("key") or "").strip().upper()
        factor = _to_float(row.get("potency_factor"))
        if key and factor:
            data.potency[key] = factor

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
