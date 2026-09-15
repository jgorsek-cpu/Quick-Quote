"""Deterministic field, formula and packaging extraction.

Absent fields are recorded as missing. Nothing is inferred to fill a blank,
and an implausible value is reported as written rather than corrected.
"""
from __future__ import annotations

import re

from ..engine.text import alias_matches, spaced
from ..schemas import FormulaLine, PackagingLine, ProductSpec
from .readers import Document, Table

# ---------------------------------------------------------------- labels

# Longest label first within each field so that "servings per bottle" is
# tested before a bare "servings".
FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "customer": ("customer name", "customer", "client", "account", "company"),
    "brand": ("brand name", "brand"),
    "formula_name": ("formula name", "product name", "formula", "product", "item name", "description of product"),
    "customer_sku": ("customer sku", "customer item number", "customer part", "sku", "item number", "item code"),
    "dosage_form": ("dosage form", "delivery form", "product form", "form"),
    "capsule_size": ("capsule size", "cap size"),
    "capsule_type": ("capsule type", "capsule material", "shell type"),
    "serving_size": ("serving size", "dose size", "daily dose", "serving"),
    "servings_per_bottle": ("servings per bottle", "servings per container", "servings per unit", "servings"),
    "count_per_bottle": ("count per bottle", "capsules per bottle", "tablets per bottle", "units per bottle", "count", "bottle count"),
    "annual_volume_bottles": ("annual volume", "annual quantity", "bottles requested", "volume bottles", "order quantity", "quantity", "volume"),
    "moq": ("minimum order quantity", "min order qty", "moq"),
    "timeline": ("timeline", "lead time", "delivery date", "need by", "required date", "target date"),
    "machine": ("machine", "equipment", "encapsulation line", "production line"),
    "mfg_loss_factor": ("mfg loss factor", "manufacturing loss", "yield loss", "loss factor"),
    "claims": ("label claims", "claims"),
    "testing": ("testing requirements", "test requirements", "testing", "qa testing"),
}

INT_FIELDS = {"servings_per_bottle", "count_per_bottle", "annual_volume_bottles", "moq"}
FLOAT_FIELDS = {"mfg_loss_factor"}
LIST_FIELDS = {"claims", "testing"}

# Every field the specification expects a quote document to carry.
EXPECTED_FIELDS: tuple[tuple[str, str], ...] = (
    ("customer", "Customer"),
    ("brand", "Brand"),
    ("formula_name", "Formula name"),
    ("customer_sku", "Customer SKU"),
    ("dosage_form", "Dosage form"),
    ("capsule_size", "Capsule size"),
    ("capsule_type", "Capsule type"),
    ("serving_size", "Serving size"),
    ("servings_per_bottle", "Servings per bottle"),
    ("count_per_bottle", "Count per bottle"),
    ("annual_volume_bottles", "Annual volume (bottles)"),
    ("moq", "MOQ"),
    ("timeline", "Timeline"),
    ("machine", "Machine specification"),
    ("claims", "Claims"),
    ("testing", "Testing"),
)

_LABEL_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /()#._-]{1,44}?)\s*[:\-–]\s*(.+?)\s*$")
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


def _to_number(text: str) -> float | None:
    match = _NUMBER.search(text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _match_label(text: str) -> str | None:
    """Return the field a label cell names, or ``None``."""
    key = spaced(text).strip()
    if not key or len(key) > 48:
        return None
    key = re.sub(r"\b(required|optional|please|enter)\b", "", key).strip()

    # The longest matching label wins across every field, so that
    # "customer sku" resolves to the SKU rather than to "customer".
    best_field: str | None = None
    best_length = -1
    for field_name, labels in FIELD_LABELS.items():
        for label in labels:
            if key == label or key == f"{label} s" or key.startswith(f"{label} "):
                if len(label) > best_length:
                    best_field, best_length = field_name, len(label)
    return best_field


def _assign(product: ProductSpec, field_name: str, raw: str) -> bool:
    """Set one field from a raw string. Returns False when nothing was set."""
    value = (raw or "").strip()
    if not value or spaced(value) in {"tbd", "n a", "na", "none", "pending", "unknown"}:
        return False
    if getattr(product, field_name, None) not in (None, "", []):
        return False

    if field_name in INT_FIELDS:
        number = _to_number(value)
        if number is None:
            return False
        setattr(product, field_name, int(number))
    elif field_name in FLOAT_FIELDS:
        number = _to_number(value)
        if number is None:
            return False
        setattr(product, field_name, number)
    elif field_name in LIST_FIELDS:
        items = [part.strip() for part in re.split(r"[;,/]|\band\b", value) if part.strip()]
        setattr(product, field_name, items)
    elif field_name == "capsule_size":
        from ..engine.text import normalise_capsule_size

        setattr(product, field_name, normalise_capsule_size(value) or value)
    else:
        setattr(product, field_name, value)
    return True


def extract_fields(document: Document) -> ProductSpec:
    """Scan tables then free text for labelled product fields."""
    product = ProductSpec()

    for table in document.tables:
        for row in table.rows:
            for index, cell in enumerate(row):
                field_name = _match_label(cell)
                if field_name is None:
                    continue
                for candidate in row[index + 1 :]:
                    if candidate.strip():
                        if _assign(product, field_name, candidate):
                            break
                        break

    for line in document.lines:
        for part in line.split(" | "):
            match = _LABEL_LINE.match(part)
            if not match:
                continue
            field_name = _match_label(match.group(1))
            if field_name:
                _assign(product, field_name, match.group(2))

    return product


def missing_fields(product: ProductSpec) -> list[str]:
    """List every expected field the document did not supply."""
    missing = []
    for attribute, label in EXPECTED_FIELDS:
        value = getattr(product, attribute, None)
        if value in (None, "", []):
            missing.append(label)
    return missing


# --------------------------------------------------------------- formula

_NAME_HEADERS = ("ingredient", "raw material", "material", "component", "item", "actives", "nutrient")
_AMOUNT_HEADERS = ("mg per serving", "mg serving", "amount per serving", "claimed mg", "label claim", "amount", "mg", "per serving", "strength", "dose")
_PART_HEADERS = ("part number", "part code", "part", "item code", "sku", "code", "material number")
_UNIT_HEADERS = ("uom", "unit", "units", "unit of measure")
_QTY_HEADERS = ("qty per bottle", "qty bottle", "quantity per bottle", "qty", "quantity", "count")
_COST_HEADERS = ("unit cost", "cost each", "cost per unit", "cost", "price", "unit price")
_DESC_HEADERS = ("description", "component", "packaging", "item", "material", "spec")
_ROLE_HEADERS = ("role", "type", "component type", "category")

_SECTION_STOP = re.compile(
    r"^\s*(packaging|commercial|terms|claims|testing|notes|total|subtotal|signature)\b", re.I
)

# Role keywords, longest and most specific first.
ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("capsule_shell", ("capsule shell", "capsule", "vcap", "shell")),
    ("shipper", ("shipper", "master case", "corrugate", "carton", "case pack")),
    ("neckband", ("neckband", "neck band", "shrink band", "tamper")),
    ("desiccant", ("desiccant", "silica gel")),
    ("cotton", ("cotton", "rayon coil")),
    ("seal", ("induction seal", "foil seal", "seal liner", "liner")),
    ("label", ("label",)),
    ("cap", ("cap", "closure", "lid")),
    ("bottle", ("bottle", "container", "jar", "packer")),
    ("scoop", ("scoop",)),
)


def _header_index(row: list[str], headers: tuple[str, ...]) -> int | None:
    """Index of the first cell whose text names one of ``headers``."""
    best_index: int | None = None
    best_length = -1
    for index, cell in enumerate(row):
        key = spaced(cell)
        if not key:
            continue
        for header in headers:
            if key == header or header in key:
                if len(header) > best_length:
                    best_index, best_length = index, len(header)
    return best_index


def _convert_to_mg(value: float, unit: str) -> float | None:
    """Convert a stated amount to mg. IU cannot be converted without a
    conversion basis, so it is left unknown rather than guessed."""
    key = spaced(unit)
    if not key or key in ("mg", "milligram", "milligrams"):
        return value
    if key in ("mcg", "ug", "microgram", "micrograms"):
        return value / 1000.0
    if key in ("g", "gram", "grams"):
        return value * 1000.0
    return None


_UNIT_IN_TEXT = re.compile(r"\b(mcg|ug|mg|g|iu)\b", re.I)


def _amount_from_cell(cell: str, header_unit: str, unit_cell: str) -> tuple[float | None, str]:
    """Parse an amount cell into mg, with a note when it cannot be converted."""
    number = _to_number(cell)
    if number is None:
        return None, ""
    unit = unit_cell or ""
    if not unit:
        found = _UNIT_IN_TEXT.search(cell)
        unit = found.group(1) if found else header_unit
    milligrams = _convert_to_mg(number, unit)
    if milligrams is None:
        return None, (
            f"Amount '{cell.strip()}' is stated in {unit.upper()}; no conversion "
            "basis is on file, so the claimed mg is recorded as missing."
        )
    return milligrams, ""


def extract_formula(document: Document) -> tuple[list[FormulaLine], list[str]]:
    """Find the ingredient table and read every line from it."""
    for table in document.tables:
        lines, notes = _formula_from_table(table)
        if lines:
            return lines, notes
    return [], []


def _formula_from_table(table: Table) -> tuple[list[FormulaLine], list[str]]:
    for header_index, row in enumerate(table.rows):
        name_col = _header_index(row, _NAME_HEADERS)
        amount_col = _header_index(row, _AMOUNT_HEADERS)
        if name_col is None or amount_col is None or name_col == amount_col:
            continue

        part_col = _header_index(row, _PART_HEADERS)
        unit_col = _header_index(row, _UNIT_HEADERS)
        header_unit = ""
        found = _UNIT_IN_TEXT.search(row[amount_col])
        if found:
            header_unit = found.group(1)

        lines: list[FormulaLine] = []
        notes: list[str] = []
        for offset, data_row in enumerate(table.rows[header_index + 1 :], start=header_index + 2):
            def cell(index: int | None) -> str:
                if index is None or index >= len(data_row):
                    return ""
                return data_row[index].strip()

            name = cell(name_col)
            if not name:
                if lines:
                    break
                continue
            if _SECTION_STOP.match(name) or spaced(name).startswith("total"):
                break
            populated = [cell for cell in data_row if cell.strip()]
            if lines and len(populated) == 1 and ":" in name:
                break  # a "Label: value" declaration, not an ingredient row

            milligrams, note = _amount_from_cell(cell(amount_col), header_unit, cell(unit_col))
            if note:
                notes.append(f"{name}: {note}")
            lines.append(
                FormulaLine(
                    name=name,
                    claimed_mg=milligrams,
                    part_code=cell(part_col) or None,
                    source_row=offset,
                    notes=note,
                )
            )
        if lines:
            return lines, notes
    return [], []


# ------------------------------------------------------------- packaging


def infer_role(text: str) -> str | None:
    """Map a component description to one of the known packaging roles."""
    if not spaced(text):
        return None
    for role, keywords in ROLE_KEYWORDS:
        for keyword in keywords:
            # alias_matches applies the short-token word-boundary rule, so
            # "cap" does not match inside "capsicum extract".
            if alias_matches(text, keyword):
                return role
    return None


def extract_packaging(document: Document) -> list[PackagingLine]:
    """Find packaging components in tables, then in labelled free text."""
    for table in document.tables:
        lines = _packaging_from_table(table)
        if lines:
            return lines
    return _packaging_from_lines(document)


def _packaging_from_table(table: Table) -> list[PackagingLine]:
    for header_index, row in enumerate(table.rows):
        desc_col = _header_index(row, _DESC_HEADERS)
        role_col = _header_index(row, _ROLE_HEADERS)
        if desc_col is None:
            continue
        if _header_index(row, _AMOUNT_HEADERS) is not None and role_col is None:
            continue  # this is the formula table, not packaging

        qty_col = _header_index(row, _QTY_HEADERS)
        cost_col = _header_index(row, _COST_HEADERS)
        part_col = _header_index(row, _PART_HEADERS)

        # A lone section title such as "PACKAGING" is not a header row. A real
        # header names a second column besides the description.
        companions = [
            index
            for index in (role_col, qty_col, cost_col, part_col)
            if index is not None and index != desc_col
        ]
        if not companions:
            continue

        lines: list[PackagingLine] = []
        for offset, data_row in enumerate(table.rows[header_index + 1 :], start=header_index + 2):
            def cell(index: int | None) -> str:
                if index is None or index >= len(data_row):
                    return ""
                return data_row[index].strip()

            description = cell(desc_col)
            role_text = cell(role_col)
            if not description and not role_text:
                if lines:
                    break
                continue
            if spaced(description).startswith("total"):
                break

            role = infer_role(role_text) or infer_role(description)
            if role is None:
                continue

            qty = _to_number(cell(qty_col)) if cell(qty_col) else None
            cost = _to_number(cell(cost_col)) if cell(cost_col) else None
            lines.append(
                PackagingLine(
                    role=role,
                    description=description or role_text,
                    qty_per_bottle=qty,
                    part_code=cell(part_col) or None,
                    listed_unit_cost=cost,
                    source_row=offset,
                )
            )
        if lines:
            return lines
    return []


def _packaging_from_lines(document: Document) -> list[PackagingLine]:
    """Read ``Bottle: 175cc white HDPE`` style declarations."""
    lines: list[PackagingLine] = []
    seen: set[tuple[str, str]] = set()
    for raw in document.lines:
        for part in raw.split(" | "):
            match = _LABEL_LINE.match(part)
            if not match:
                continue
            label, value = match.group(1), match.group(2)
            if _match_label(label) is not None:
                continue
            role = infer_role(label)
            if role is None:
                continue
            key = (role, spaced(value))
            if key in seen:
                continue
            seen.add(key)
            lines.append(PackagingLine(role=role, description=f"{label.strip()} {value}".strip()))
    return lines
