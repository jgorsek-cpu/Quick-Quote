"""Selectable catalogs for the sales-rep builder.

Each canonical identity is shown with the PO records behind it, so a rep sees
before choosing whether a line will cost out cleanly, need Purchasing review,
or have no cost at all.
"""
from __future__ import annotations

from datetime import date

from ..config import ACCEPTED, NEEDS_REVIEW, UNMATCHED
from ..engine.identity import resolve_identity
from ..engine.text import extract_size
from .loader import ReferenceData


# Tokens that must not be title-cased when a canonical identity is shown.
_ACRONYMS = {
    "bp": "BP", "hcl": "HCl", "msm": "MSM", "mk7": "MK-7", "coq10": "CoQ10",
    "q10": "Q10", "egcg": "EGCG", "hdpe": "HDPE", "pet": "PET", "cr": "CR",
    "ps": "PS", "mcc": "MCC", "sio2": "SiO2", "dr": "DR", "b12": "B12",
    "d3": "D3", "k2": "K2", "cc": "cc", "mm": "mm", "hpmc": "HPMC",
    "l": "L", "usp": "USP",
}


def display_name(canonical: str) -> str:
    """Title-case a canonical identity while preserving known acronyms."""
    return " ".join(_ACRONYMS.get(word, word.title()) for word in canonical.split())


def _po_rows_for(canonical: str, reference: ReferenceData, packaging: bool) -> list:
    rows = []
    for row in reference.po_rows:
        resolution = resolve_identity(row.description, reference, packaging=packaging)
        if resolution and resolution.canonical == canonical:
            rows.append(row)
    return rows


def _status_for(count: int) -> str:
    if count == 1:
        return ACCEPTED
    if count > 1:
        return NEEDS_REVIEW
    return UNMATCHED


def ingredient_catalog(reference: ReferenceData, as_of: date | None = None) -> list[dict]:
    """Every ingredient identity a rep can select, with its PO backing."""
    as_of = as_of or date.today()
    catalog: list[dict] = []

    for entry in reference.ingredient_identities:
        rows = _po_rows_for(entry.canonical, reference, packaging=False)
        overage = reference.overage_pct(entry.overage_class, True)
        catalog.append({
            "canonical": entry.canonical,
            "display_name": display_name(entry.canonical),
            "aliases": list(entry.aliases),
            "overage_class": entry.overage_class,
            "overage_pct": overage,
            "potency": entry.default_potency,
            "potency_known": entry.default_potency is not None,
            "notes": entry.notes,
            "expected_status": _status_for(len(rows)),
            "po_options": [
                {
                    "part_number": row.part_number,
                    "description": row.description,
                    "uom": row.uom,
                    "latest_unit_cost": row.latest_unit_cost,
                    "latest_po_date": row.latest_po_date.isoformat() if row.latest_po_date else None,
                    "po_age_days": row.age_days(as_of),
                    "vendor": row.latest_vendor,
                    "unique_vendor_count": row.unique_vendor_count,
                    "po_count": row.po_count,
                    "min_unit_cost_ever": row.min_unit_cost_ever,
                    "max_unit_cost_ever": row.max_unit_cost_ever,
                }
                for row in rows
            ],
        })

    catalog.sort(key=lambda item: item["display_name"])
    return catalog


def packaging_catalog(reference: ReferenceData, as_of: date | None = None) -> list[dict]:
    """Every packaging identity a rep can select, grouped by role and size."""
    as_of = as_of or date.today()
    catalog: list[dict] = []

    for entry in reference.packaging_identities:
        rows = _po_rows_for(entry.canonical, reference, packaging=True)
        options = []
        for row in rows:
            size = extract_size(row.description, role=entry.role)
            options.append({
                "part_number": row.part_number,
                "description": row.description,
                "uom": row.uom,
                "latest_unit_cost": row.latest_unit_cost,
                "latest_po_date": row.latest_po_date.isoformat() if row.latest_po_date else None,
                "po_age_days": row.age_days(as_of),
                "vendor": row.latest_vendor,
                "unique_vendor_count": row.unique_vendor_count,
                "size": size.describe(),
                "capsule_size": size.capsule_size,
                "volume_cc": size.volume_cc,
                "neck_mm": size.neck_mm,
            })
        options.sort(key=lambda option: option["description"])
        catalog.append({
            "canonical": entry.canonical,
            "display_name": display_name(entry.canonical),
            "role": entry.role,
            "size_required": entry.size_required,
            "aliases": list(entry.aliases),
            "notes": entry.notes,
            "po_options": options,
        })

    catalog.sort(key=lambda item: (item["role"], item["display_name"]))
    return catalog


def reference_overview(reference: ReferenceData) -> dict:
    """Headline numbers and the rates every quote is costed against."""
    return {
        "source_dir": str(reference.source_dir),
        "counts": {
            "ingredient_identities": len(reference.ingredient_identities),
            "packaging_identities": len(reference.packaging_identities),
            "po_rows": len(reference.po_rows),
            "overage_classes": len(reference.overage),
            "potency_entries": len(reference.potency),
            "guard_pairs": len(reference.guard_pairs),
            "capsule_sizes": len(reference.capsule_fill),
        },
        "rates": {
            "labor_rate_per_hour": reference.labor_rate,
            "overhead_rate_per_hour": reference.overhead_rate,
            "combined_rate_per_hour": reference.combined_rate,
            "default_machine": reference.default_machine,
            "default_mfg_loss_factor": reference.mfg_loss_factor,
            "cost_range_pct": reference.range_pct * 100,
            "stale_po_days": reference.stale_po_days,
            "price_variance_flag_pct": reference.variance_flag_pct * 100,
            "uom_sanity_max_per_bottle": reference.uom_sanity_max,
            "major_spend_pct": reference.major_spend_pct * 100,
        },
        "compounding_hours": {
            "1 component": reference.compounding_hours(1),
            "2-10 components": reference.compounding_hours(5),
            "11-20 components": reference.compounding_hours(15),
            "21+ components": reference.compounding_hours(25),
        },
        "capsule_fill_mg": dict(sorted(reference.capsule_fill.items())),
        "overage_classes": {
            name: {"multi_pct": values["multi"] * 100, "single_pct": values["single"] * 100}
            for name, values in sorted(reference.overage.items())
        },
        "guard_pairs": [
            {"pair": sorted(pair), "reason": reference.guard_reasons.get(pair, "")}
            for pair in reference.guard_pairs
        ],
    }
