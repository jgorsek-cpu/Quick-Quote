"""Propose identity-table entries from real purchase-order descriptions.

The identity tables are curated reference data. This tool does not curate
them; it proposes rows so that R&D and Purchasing start from the catalogue
that actually exists rather than from a blank file.

Every proposal is conservative by construction:

* one canonical identity per distinct normalised description, so nothing is
  merged across grades and no cross-matching is invented;
* two parts whose descriptions normalise identically land on one identity and
  will therefore come out as Needs Review, which is the correct outcome;
* potency is left blank, so the engine applies its documented default and
  raises the R&D flag that says so;
* descriptions that already resolve under the curated table are skipped, so
  hand-curated entries are never displaced.

Usage::

    python -m quickquote.reference.seed \\
        --po-history backend/quickquote/reference/data/po_history.csv \\
        --out-dir /tmp/proposed
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

from ..engine.classify import classify_by_name
from ..engine.identity import resolve_identity
from ..engine.text import extract_size, spaced
from ..parsing.extract import infer_role
from .loader import PoRow, ReferenceData, load_po_history, load_reference_data

# Part-number prefixes used by the operational system, as a fallback when a
# packaging description does not name its own role.
PREFIX_ROLES: tuple[tuple[str, str], ...] = (
    ("KTT", "bottle"),
    ("KT", "bottle"),
    ("KCT", "cap"),
    ("KC", "cap"),
    ("KSB", "neckband"),
    ("KS", "neckband"),
    ("KB", "shipper"),
    ("KL", "label"),
    ("KD", "desiccant"),
    ("RECA", "capsule_shell"),
)

# Noise that carries no identity meaning.
_NOISE = re.compile(r"[^a-z0-9%./:\- ]+")


def clean_description(text: str) -> str:
    """Strip export artefacts without changing what the description says."""
    cleaned = _NOISE.sub(" ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def role_for(part: str, description: str) -> str:
    role = infer_role(description)
    if role:
        return role
    upper = part.upper()
    for prefix, mapped in PREFIX_ROLES:
        if upper.startswith(prefix):
            return mapped
    return ""


def _is_packaging(row: PoRow) -> bool:
    return row.part_number.upper().startswith(("K", "PK")) or row.uom == "EA"


def propose(
    po_rows: list[PoRow], reference: ReferenceData
) -> tuple[list[dict], list[dict], dict]:
    """Build proposed ingredient and packaging identity rows."""
    ingredients: dict[str, list[PoRow]] = defaultdict(list)
    packaging: dict[str, list[PoRow]] = defaultdict(list)
    stats = {
        "rows": len(po_rows),
        "no_description": 0,
        "already_resolved": 0,
        "proposed_ingredients": 0,
        "proposed_packaging": 0,
        "collisions": 0,
    }

    for row in po_rows:
        if not row.description.strip():
            stats["no_description"] += 1
            continue

        is_packaging = _is_packaging(row)
        if resolve_identity(row.description, reference, packaging=is_packaging):
            stats["already_resolved"] += 1
            continue

        canonical = clean_description(row.description)
        if not canonical or not spaced(canonical):
            stats["no_description"] += 1
            continue

        (packaging if is_packaging else ingredients)[canonical].append(row)

    ingredient_rows = []
    for canonical, rows in sorted(ingredients.items()):
        if len(rows) > 1:
            stats["collisions"] += 1
        overage_class = classify_by_name(canonical) or "default"
        ingredient_rows.append({
            "canonical": canonical,
            "aliases": canonical,
            "overage_class": overage_class,
            "default_potency": "",   # unknown until R&D confirms the claim basis
            "notes": _note(rows, overage_class),
        })
    stats["proposed_ingredients"] = len(ingredient_rows)

    packaging_rows = []
    for canonical, rows in sorted(packaging.items()):
        if len(rows) > 1:
            stats["collisions"] += 1
        role = role_for(rows[0].part_number, canonical)
        size = extract_size(canonical, role=role)
        packaging_rows.append({
            "canonical": canonical,
            "aliases": canonical,
            "role": role,
            "size_required": "1" if not size.empty else "0",
            "notes": _note(rows, role or "role not determined"),
        })
    stats["proposed_packaging"] = len(packaging_rows)

    return ingredient_rows, packaging_rows, stats


def _note(rows: list[PoRow], classification: str) -> str:
    parts = ", ".join(row.part_number for row in rows[:4])
    prefix = "PROPOSED - confirm before use."
    if len(rows) > 1:
        return (
            f"{prefix} {len(rows)} parts share this description ({parts}); "
            f"they will report as Needs Review until split. Class: {classification}."
        )
    return f"{prefix} From part {parts}. Class: {classification}."


INGREDIENT_FIELDS = ["canonical", "aliases", "overage_class", "default_potency", "notes"]
PACKAGING_FIELDS = ["canonical", "aliases", "role", "size_required", "notes"]


def write(rows: list[dict], fields: list[str], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m quickquote.reference.seed",
        description="Propose identity rows from a PO history table.",
    )
    parser.add_argument("--po-history", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--merge-into", type=Path, default=None,
        help="Append the proposals to the curated identity tables in this "
             "directory. Each appended row keeps its PROPOSED note, so R&D and "
             "Purchasing can see what has not been confirmed yet.",
    )
    arguments = parser.parse_args(argv)

    if not arguments.po_history.exists():
        parser.error(f"file not found: {arguments.po_history}")

    reference = load_reference_data()
    po_rows = load_po_history(arguments.po_history)
    ingredient_rows, packaging_rows, stats = propose(po_rows, reference)

    write(ingredient_rows, INGREDIENT_FIELDS,
          arguments.out_dir / "ingredient_identity_proposed.csv")
    write(packaging_rows, PACKAGING_FIELDS,
          arguments.out_dir / "packaging_identity_proposed.csv")

    print("Quick Quote identity seed")
    print(f"  PO rows read:                 {stats['rows']:,}")
    print(f"  Rows with no description:     {stats['no_description']:,}")
    print(f"  Already covered by curation:  {stats['already_resolved']:,}")
    print(f"  Proposed ingredient rows:     {stats['proposed_ingredients']:,}")
    print(f"  Proposed packaging rows:      {stats['proposed_packaging']:,}")
    print(f"  Descriptions shared by >1 part (will report Needs Review): "
          f"{stats['collisions']:,}")
    print(f"\nWrote proposals to {arguments.out_dir}")

    if arguments.merge_into:
        added = merge_into(arguments.merge_into, ingredient_rows, packaging_rows)
        print(f"\nMerged into {arguments.merge_into}:")
        print(f"  ingredient identities appended: {added['ingredients']:,}")
        print(f"  packaging identities appended:  {added['packaging']:,}")
        print("Every appended row is marked PROPOSED. Curating them - setting "
              "potency, splitting grades, adding guard pairs - is R&D's and "
              "Purchasing's work, and the engine flags the defaults until then.")
    else:
        print("Review them, then append the confirmed rows to the curated tables in "
              "reference/data/. Nothing is applied automatically.")
    return 0


def merge_into(
    data_dir: Path, ingredient_rows: list[dict], packaging_rows: list[dict]
) -> dict[str, int]:
    """Append proposals to the curated tables, never displacing a curated row."""
    added = {"ingredients": 0, "packaging": 0}

    for name, rows, fields, key in (
        ("ingredient_identity.csv", ingredient_rows, INGREDIENT_FIELDS, "ingredients"),
        ("packaging_identity.csv", packaging_rows, PACKAGING_FIELDS, "packaging"),
    ):
        path = data_dir / name
        existing = list(csv.DictReader(path.open())) if path.exists() else []
        seen = {row["canonical"] for row in existing}
        fresh = [row for row in rows if row["canonical"] not in seen]

        merged = existing + fresh
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(merged)
        added[key] = len(fresh)

    return added


if __name__ == "__main__":
    sys.exit(main())
