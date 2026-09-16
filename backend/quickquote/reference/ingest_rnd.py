"""Load R&D's own reference workbooks into the engine's tables.

Four sheets R&D maintains, replacing the industry-typical placeholders that
shipped with the system:

``Overage Guidelines``
    Overage by active, for multi- and single-ingredient formulas, and
    separately for gummies. A value R&D records as non-numeric -- probiotics
    are "Strain Dependent" in a gummy -- is carried through as unknown rather
    than coerced to a number.

``Potency of Material``
    Potency by part code. The asterisk in the description marks the moiety
    being claimed, so one part can carry several potencies: claiming
    ``L-Arginine* HCl`` is 0.813 of the purchased salt, claiming
    ``L-Arginine HCl*`` is 0.983. Those are kept as separate rows; the engine
    refuses to choose between them.

``RM Testing``
    Measured bulk density by lot. Lots vary two- and threefold, so the median
    is carried as the working value with the range beside it -- a formula that
    fits at the median may not fit at the lowest density on file.

``Capsule Size Calculator``
    Shell capacity by size and blend density, and the tamping model that
    densifies a blend during encapsulation.

    python -m quickquote.reference.ingest_rnd --overage X.xlsx --potency Y.xlsx \\
        --testing Z.xlsx --capsules C.xlsx --out-dir backend/quickquote/reference/data
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

# R&D's row labels mapped to the engine's class keys. Anything not listed is
# reported rather than guessed at.
OVERAGE_CLASSES: tuple[tuple[str, str], ...] = (
    ("vitamin a", "vitamin_a"),
    ("beta carotene", "beta_carotene"),
    ("vitamin b1", "thiamine"),
    ("vitamin b2", "riboflavin"),
    ("vitamin b3", "niacin"),
    ("vitamin b5", "pantothenic_acid"),
    ("vitamin b6", "vitamin_b6"),
    ("biotin", "biotin"),
    ("folate", "folate"),
    ("vitamin c", "vitamin_c"),
    ("vitamin d", "vitamin_d"),
    ("vitamin e", "vitamin_e"),
    ("vitamin k", "vitamin_k"),
    ("calcium, magnesium", "calcium_magnesium"),
    ("iodine", "iodine"),
    ("chromium, copper", "trace_minerals"),
    ("alanine, arginine", "amino_acids"),
    ("botanical powders", "botanical_powder"),
    ("botanical extracts", "botanical_extract"),
    ("alpha lipoic acid", "alpha_lipoic_acid"),
    ("coq10 (ubiquinone)", "coq10"),
    ("coq10 (ubiquinol)", "ubiquinol"),
    ("lutein", "lutein"),
    ("lycopene", "lycopene"),
    ("melatonin", "melatonin"),
    ("spore forming", "probiotic_spore"),
    ("non-spore forming", "probiotic_non_spore"),
    ("enzymes", "enzyme"),
)

# Engine classes R&D's guideline covers inside another row's label. Iron is
# priced in the "Calcium, Magnesium, Zinc, Iron, Boron" row; a probiotic whose
# strain type is not stated takes the higher of R&D's two probiotic rows,
# because under-dosing a CFU claim is the failure that matters.
COVERED_BY: tuple[tuple[str, str, str], ...] = (
    ("iron", "calcium_magnesium", "R&D's 'Calcium, Magnesium, Zinc, Iron, Boron' row"),
    ("mineral_salt", "calcium_magnesium", "R&D's 'Calcium, Magnesium, Zinc, Iron, Boron' row"),
    ("probiotic", "probiotic_non_spore",
     "R&D's 'Non-Spore Forming' row - the higher of its two probiotic rows, "
     "applied when the strain type is not stated"),
)

# Classes the engine needs that R&D's guideline does not cover at all. They
# keep a working value, marked so every quote using one says where it came from.
NOT_IN_GUIDELINE: tuple[tuple[str, str, float, float], ...] = (
    ("vitamin_b12", "Vitamin B12", 30.0, 25.0),
    ("omega_oil", "Omega oils", 10.0, 8.0),
    ("excipient", "Excipients, flow agents, fillers", 2.0, 2.0),
    ("default", "Applied when a class cannot be determined", 5.0, 5.0),
)


@dataclass
class Report:
    overage_rows: int = 0
    overage_unmapped: list[str] = field(default_factory=list)
    overage_non_numeric: list[str] = field(default_factory=list)
    potency_rows: int = 0
    potency_parts: int = 0
    potency_ambiguous: list[str] = field(default_factory=list)
    density_measurements: int = 0
    density_parts: int = 0
    density_wide_spread: list[str] = field(default_factory=list)
    capsule_sizes: int = 0
    warnings: list[str] = field(default_factory=list)


def _sheet(path: Path, name: str | None = None):
    from openpyxl import load_workbook

    book = load_workbook(path, data_only=True)
    return book, (book[name] if name else book.worksheets[0])


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _number(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "")
    try:
        return float(text)
    except ValueError:
        return None


# ------------------------------------------------------------- overage

def load_overage(path: Path, report: Report) -> list[dict]:
    book, sheet = _sheet(path, "Overages")
    rows: list[dict] = []
    seen: dict[str, dict] = {}

    for raw in sheet.iter_rows(min_row=3, values_only=True):
        label = _text(raw[0])
        if not label:
            continue
        lowered = label.lower()
        key = next((k for prefix, k in OVERAGE_CLASSES if lowered.startswith(prefix)), None)
        if key is None:
            # Section headers carry no numbers; anything else is a real gap.
            if any(_number(cell) is not None for cell in raw[1:5]):
                report.overage_unmapped.append(label)
            continue

        values = [_number(cell) for cell in raw[1:5]]
        for index, cell in enumerate(raw[1:5]):
            if cell is not None and values[index] is None:
                report.overage_non_numeric.append(f"{label}: {_text(cell)}")

        rows.append({
            "overage_class": key,
            "label": label,
            "multi_ingredient_pct": "" if values[0] is None else f"{values[0] * 100:g}",
            "single_ingredient_pct": "" if values[1] is None else f"{values[1] * 100:g}",
            "gummy_multi_pct": "" if values[2] is None else f"{values[2] * 100:g}",
            "gummy_single_pct": "" if values[3] is None else f"{values[3] * 100:g}",
            "source": "R&D Overage Guidelines",
            "notes": _text(raw[5]) if len(raw) > 5 else "",
        })
        seen[key] = rows[-1]

    for key, source_key, why in COVERED_BY:
        if key in seen:
            continue
        parent = seen.get(source_key)
        if parent is None:
            report.warnings.append(
                f"{key} takes its overage from {source_key}, which R&D's sheet did not supply"
            )
            continue
        rows.append({
            **parent, "overage_class": key, "label": parent["label"],
            "source": "R&D Overage Guidelines", "notes": f"Priced from {why}.",
        })

    for key, label, multi, single in NOT_IN_GUIDELINE:
        if key in seen:
            continue
        rows.append({
            "overage_class": key, "label": label,
            "multi_ingredient_pct": f"{multi:g}", "single_ingredient_pct": f"{single:g}",
            "gummy_multi_pct": "", "gummy_single_pct": "",
            "source": "NOT IN R&D GUIDELINE - placeholder",
            "notes": "Confirm with R&D; quotes using this class say where it came from.",
        })

    book.close()
    report.overage_rows = len(rows)
    return rows


# ------------------------------------------------------------- potency

_CLAIM = re.compile(r"\*")


def load_potency(path: Path, report: Report) -> list[dict]:
    book, sheet = _sheet(path)
    rows: list[dict] = []
    per_part: dict[str, int] = {}

    for raw in sheet.iter_rows(min_row=4, values_only=True):
        part = _text(raw[0]).upper()
        potency = _number(raw[2])
        if not part or potency is None:
            continue
        description = _text(raw[1])
        rows.append({
            "part_code": part,
            "claim_description": description,
            # The asterisk marks the moiety the label claims.
            "claims_whole_material": "1" if description.rstrip().endswith("*") else "0",
            "potency_factor": f"{potency:.6g}",
            "percent_element": _text(raw[3]),
            "element_conversion": _text(raw[4]),
            "min_purity": _text(raw[5]) if len(raw) > 5 else "",
            "remarks": _text(raw[6]) if len(raw) > 6 else "",
        })
        per_part[part] = per_part.get(part, 0) + 1

    book.close()
    report.potency_rows = len(rows)
    report.potency_parts = len(per_part)
    report.potency_ambiguous = sorted(k for k, n in per_part.items() if n > 1)
    return rows


# ------------------------------------------------------------- density

def load_density(path: Path, report: Report) -> list[dict]:
    book, sheet = _sheet(path, "Raw Material Data")
    measurements: dict[str, list[float]] = {}
    names: dict[str, str] = {}

    for raw in sheet.iter_rows(min_row=2, values_only=True):
        part = _text(raw[0]).upper()
        density = _number(raw[4])
        if not part or density is None or density <= 0:
            continue
        measurements.setdefault(part, []).append(density)
        names.setdefault(part, _text(raw[3]))

    rows: list[dict] = []
    for part, values in sorted(measurements.items()):
        low, high = min(values), max(values)
        median = statistics.median(values)
        if len(values) > 2 and high > low * 1.5:
            report.density_wide_spread.append(part)
        rows.append({
            "part_code": part,
            "material": names.get(part, ""),
            "median_g_ml": f"{median:.4g}",
            "min_g_ml": f"{low:.4g}",
            "max_g_ml": f"{high:.4g}",
            "lot_count": str(len(values)),
        })

    book.close()
    report.density_measurements = sum(len(v) for v in measurements.values())
    report.density_parts = len(rows)
    return rows


# ------------------------------------------------------------ capsules

_SIZE = re.compile(r"^\s*([0-9A-Za-z]+)\s*\(([\d.]+)\s*mL\)")


def load_capsules(path: Path, report: Report) -> tuple[list[dict], list[str]]:
    book, sheet = _sheet(path, "Capsule Specs")
    grid = list(sheet.iter_rows(values_only=True))

    header_index = next(
        (i for i, row in enumerate(grid) if _text(row[0]).lower().startswith("capsule size")),
        None,
    )
    if header_index is None:
        book.close()
        return [], []

    densities = []
    for cell in grid[header_index][1:]:
        value = _number(_text(cell).replace("At", "").replace("g/mL", ""))
        if value:
            densities.append(f"{value:g}")

    rows: list[dict] = []
    for raw in grid[header_index + 1:]:
        match = _SIZE.match(_text(raw[0]))
        if not match:
            continue
        entry = {"capsule_size": match.group(1).lower(), "volume_ml": match.group(2)}
        for index, density in enumerate(densities, start=1):
            value = _number(raw[index]) if index < len(raw) else None
            entry[density] = "" if value is None else f"{value:g}"
        rows.append(entry)

    book.close()
    report.capsule_sizes = len(rows)
    return rows, densities


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m quickquote.reference.ingest_rnd",
        description="Load R&D's reference workbooks into the engine's tables.",
    )
    parser.add_argument("--overage", type=Path)
    parser.add_argument("--potency", type=Path)
    parser.add_argument("--testing", type=Path)
    parser.add_argument("--capsules", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)

    report = Report()
    out = arguments.out_dir

    if arguments.overage:
        rows = load_overage(arguments.overage, report)
        _write(out / "overage.csv",
               ["overage_class", "label", "multi_ingredient_pct",
                "single_ingredient_pct", "gummy_multi_pct", "gummy_single_pct",
                "source", "notes"], rows)

    if arguments.potency:
        rows = load_potency(arguments.potency, report)
        _write(out / "potency.csv",
               ["part_code", "claim_description", "claims_whole_material",
                "potency_factor", "percent_element", "element_conversion",
                "min_purity", "remarks"], rows)

    if arguments.testing:
        rows = load_density(arguments.testing, report)
        _write(out / "bulk_density_measured.csv",
               ["part_code", "material", "median_g_ml", "min_g_ml",
                "max_g_ml", "lot_count"], rows)

    if arguments.capsules:
        rows, densities = load_capsules(arguments.capsules, report)
        if rows:
            _write(out / "capsule_capacity.csv", ["capsule_size", "volume_ml", *densities], rows)

    print("Quick Quote R&D reference ingest")
    print(f"  overage classes written     : {report.overage_rows}")
    if report.overage_unmapped:
        print(f"  ! overage rows not mapped   : {', '.join(report.overage_unmapped)}")
    if report.overage_non_numeric:
        print(f"  ! non-numeric overage values: {'; '.join(report.overage_non_numeric)}")
        print("      carried through as unknown, not coerced to a number")
    print(f"  potency rows                : {report.potency_rows:,} "
          f"across {report.potency_parts:,} parts")
    if report.potency_ambiguous:
        print(f"  ! parts with several claim bases: {len(report.potency_ambiguous)} "
              f"({', '.join(report.potency_ambiguous[:6])})")
        print("      the engine will not choose between them")
    print(f"  bulk density measurements   : {report.density_measurements:,} "
          f"across {report.density_parts:,} parts")
    if report.density_wide_spread:
        print(f"  ! parts whose lots vary >1.5x: {len(report.density_wide_spread)}")
    print(f"  capsule sizes               : {report.capsule_sizes}")
    print(f"\nWrote to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
