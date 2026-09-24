"""Build the PO-history reference table from operational exports.

The PO exports are transaction level (``Purchase Order Date``, ``Part
Number``, ``Order Qty``, ``Unit Cost``) and carry no description and no
vendor. Descriptions come from the inventory master; vendor does not exist in
either source, so it is reported as unknown rather than invented -- which also
means the single-supplier flag stays silent instead of firing on every line.

Usage::

    python -m quickquote.reference.ingest \\
        --item-master "Price Sheet.xlsx" \\
        --po Raw_PO.xlsx --po PKG_PO.xlsx \\
        --out backend/quickquote/reference/data/po_history.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

# Column headers as the operational exports spell them.
PO_DATE_HEADERS = ("purchase order date", "po date", "order date", "date")
PO_PART_HEADERS = ("part number", "part", "item", "part no")
PO_QTY_HEADERS = ("order qty", "qty", "quantity", "order quantity")
PO_COST_HEADERS = ("unit cost", "cost", "unit price", "price")
PO_DESC_HEADERS = ("description", "desc", "item description")
PO_VENDOR_HEADERS = ("vendor name", "vendor", "supplier name", "supplier")
PO_NUMBER_HEADERS = ("purchase order number", "po number", "po no", "order number")

MASTER_PART_HEADERS = ("part", "part number", "item")
MASTER_DESC_HEADERS = ("description", "desc", "itmdesc", "item description")
MASTER_UOM_HEADERS = ("um_purchasing", "um_inventory", "uom", "um")
MASTER_LINE_HEADERS = ("product_line", "product line", "line")

# Report exports carry subtotal rows; these are skipped and counted, not
# silently dropped.
SUBTOTAL_MARKERS = {"part total", "grand total", "total", "report total"}


@dataclass
class IngestReport:
    """What the ingest did, so the result can be trusted or questioned."""

    po_files: list[str] = field(default_factory=list)
    master_files: list[str] = field(default_factory=list)
    transactions_read: int = 0
    subtotal_rows_skipped: int = 0
    unparsable_rows_skipped: int = 0
    master_rows: int = 0
    parts_out: int = 0
    parts_without_description: list[str] = field(default_factory=list)
    descriptions_repaired: int = 0
    descriptions_from_po: int = 0
    vendors_present: bool = False
    warnings: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "Quick Quote reference ingest",
            f"  PO exports:            {', '.join(self.po_files) or 'none'}",
            f"  Item master:           {', '.join(self.master_files) or 'none'}",
            f"  Item master rows:      {self.master_rows:,}",
            f"  Descriptions repaired: {self.descriptions_repaired:,} "
            f"(cp1252 text decoded as cp437 on export)",
            f"  PO transactions read:  {self.transactions_read:,}",
            f"  Subtotal rows skipped: {self.subtotal_rows_skipped:,}",
            f"  Unusable rows skipped: {self.unparsable_rows_skipped:,}",
            f"  Parts written:         {self.parts_out:,}",
            f"  Vendors:               "
            f"{'read from the PO export' if self.vendors_present else 'no vendor column'}",
            f"  Descriptions from PO:  {self.descriptions_from_po:,} "
            f"(not in the item master)",
            f"  Parts still with no description: "
            f"{len(self.parts_without_description):,}",
        ]
        if self.parts_without_description[:10]:
            lines.append(
                "    e.g. " + ", ".join(self.parts_without_description[:10])
            )
        for warning in self.warnings:
            lines.append(f"  ! {warning}")
        return "\n".join(lines)


# Descriptions leave Global Shop as cp1252 bytes and arrive decoded as cp437,
# so a registered-trademark sign reads as a guillemet and an apostrophe as an
# AE ligature: "KSM-66« ASHWAGANDHA", "WOMENÆS WHITE FILM".
#
# Only the characters cp437 produces from cp1252 *punctuation* are treated as
# suspect -- quotes, dashes, and the trademark, registered and degree signs.
# A letter with a real accent is never one of them, so "Açaí" and "Café"
# survive untouched even though a naive round trip would mangle them.
_MOJIBAKE = "".join(
    bytes([code]).decode("cp437")
    for code in (0x91, 0x92, 0x93, 0x94, 0x96, 0x97, 0x99, 0xA9, 0xAE, 0xB0)
)


def repair_mojibake(text: str) -> str:
    """Undo a cp1252 description decoded as cp437, where that is what it is.

    The repair is attempted only on strings whose every non-ASCII character is
    one of the known mojibake characters, so a description that genuinely
    carries an accent is left exactly as it was found.
    """
    suspect = [ch for ch in text if ord(ch) > 127]
    if not suspect or any(ch not in _MOJIBAKE for ch in suspect):
        return text
    try:
        return text.encode("cp437").decode("cp1252")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _norm(text: object) -> str:
    return str(text or "").strip().lower()


def _find(headers: list[str], candidates: Iterable[str]) -> int | None:
    for candidate in candidates:
        for index, header in enumerate(headers):
            if header == candidate:
                return index
    for candidate in candidates:
        for index, header in enumerate(headers):
            if candidate in header:
                return index
    return None


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _sheet_rows(path: Path, sheet: str | None = None) -> Iterable[list]:
    """Yield rows from an xlsx sheet, or from a csv."""
    if path.suffix.lower() in (".csv", ".txt"):
        with path.open(newline="", encoding="utf-8-sig") as handle:
            yield from csv.reader(handle)
        return

    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        worksheet = workbook[sheet] if sheet else workbook.worksheets[0]
        for row in worksheet.iter_rows(values_only=True):
            yield list(row)
    finally:
        workbook.close()


def _pick_master_sheet(path: Path, hint: str | None) -> str | None:
    """Find the sheet that looks like an inventory master."""
    if hint:
        return hint
    if path.suffix.lower() in (".csv", ".txt"):
        return None

    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        best: tuple[int, str] | None = None
        for worksheet in workbook.worksheets:
            for row in worksheet.iter_rows(min_row=1, max_row=1, values_only=True):
                headers = [_norm(cell) for cell in row]
                has_part = _find(headers, MASTER_PART_HEADERS) is not None
                has_desc = _find(headers, MASTER_DESC_HEADERS) is not None
                if has_part and has_desc:
                    size = worksheet.max_row or 0
                    if best is None or size > best[0]:
                        best = (size, worksheet.title)
                break
        return best[1] if best else None
    finally:
        workbook.close()


def load_item_master(
    paths: list[Path], report: IngestReport, sheet: str | None = None
) -> dict[str, dict]:
    """Read part -> description and unit of measure from inventory masters."""
    master: dict[str, dict] = {}
    for path in paths:
        chosen = _pick_master_sheet(path, sheet)
        if chosen is None and path.suffix.lower() not in (".csv", ".txt"):
            report.warnings.append(
                f"{path.name}: no sheet with both a part and a description column was found."
            )
            continue
        report.master_files.append(f"{path.name}[{chosen or 'csv'}]")

        rows = _sheet_rows(path, chosen)
        try:
            headers = [_norm(cell) for cell in next(iter(rows))]
        except StopIteration:
            continue

        part_col = _find(headers, MASTER_PART_HEADERS)
        desc_col = _find(headers, MASTER_DESC_HEADERS)
        uom_col = _find(headers, MASTER_UOM_HEADERS)
        line_col = _find(headers, MASTER_LINE_HEADERS)
        if part_col is None or desc_col is None:
            report.warnings.append(f"{path.name}: part or description column missing.")
            continue

        for row in rows:
            if part_col >= len(row):
                continue
            part = str(row[part_col] or "").strip().upper()
            raw_description = str(row[desc_col] or "").strip()
            description = repair_mojibake(raw_description)
            if description != raw_description:
                report.descriptions_repaired += 1
            if not part or not description:
                continue
            uom = ""
            if uom_col is not None and uom_col < len(row):
                uom = str(row[uom_col] or "").strip().upper()
            product_line = ""
            if line_col is not None and line_col < len(row):
                product_line = str(row[line_col] or "").strip()
            # First master wins so an explicit override file can precede a dump.
            master.setdefault(
                part,
                {"description": description, "uom": uom, "product_line": product_line},
            )
        report.master_rows = len(master)
    return master


@dataclass(frozen=True)
class PoTransaction:
    """One purchase-order line, as the export gives it."""

    when: date | None
    unit_cost: float
    qty: float | None
    description: str = ""
    vendor: str = ""
    order_number: str = ""


def read_po_transactions(paths: list[Path], report: IngestReport) -> dict[str, list[PoTransaction]]:
    """Read transaction-level PO exports, grouped by part number."""
    grouped: dict[str, list[tuple]] = defaultdict(list)

    for path in paths:
        report.po_files.append(path.name)
        rows = _sheet_rows(path)
        iterator = iter(rows)
        try:
            headers = [_norm(cell) for cell in next(iterator)]
        except StopIteration:
            continue

        date_col = _find(headers, PO_DATE_HEADERS)
        part_col = _find(headers, PO_PART_HEADERS)
        qty_col = _find(headers, PO_QTY_HEADERS)
        cost_col = _find(headers, PO_COST_HEADERS)
        desc_col = _find(headers, PO_DESC_HEADERS)
        # "Vendor name" is preferred over the vendor code, so a flag names a
        # supplier a person recognises rather than three letters.
        vendor_col = _find(headers, PO_VENDOR_HEADERS)
        order_col = _find(headers, PO_NUMBER_HEADERS)
        if part_col is None or cost_col is None:
            report.warnings.append(
                f"{path.name}: needs at least a part number and a unit cost column."
            )
            continue

        for row in iterator:
            def cell(index: int | None):
                if index is None or index >= len(row):
                    return None
                return row[index]

            first = _norm(cell(date_col) if date_col is not None else None)
            if first in SUBTOTAL_MARKERS:
                report.subtotal_rows_skipped += 1
                continue

            part = str(cell(part_col) or "").strip().upper()
            cost = _to_float(cell(cost_col))
            if not part or cost is None or cost <= 0:
                if part or cost is not None:
                    report.unparsable_rows_skipped += 1
                continue
            # A part number with no letter is a subtotal or a stray number.
            if not any(character.isalpha() for character in part):
                report.subtotal_rows_skipped += 1
                continue

            description = repair_mojibake(str(cell(desc_col) or "").strip())
            vendor = str(cell(vendor_col) or "").strip()
            order = str(cell(order_col) or "").strip()
            grouped[part].append(
                PoTransaction(
                    when=_to_date(cell(date_col)),
                    unit_cost=cost,
                    qty=_to_float(cell(qty_col)),
                    description=description,
                    vendor=vendor,
                    order_number=order,
                )
            )
            report.transactions_read += 1

    return grouped


def aggregate(
    grouped: dict[str, list[PoTransaction]], master: dict[str, dict], report: IngestReport
) -> list[dict]:
    """Collapse transactions into one PO-history row per part."""
    rows: list[dict] = []
    saw_vendor = False

    for part, transactions in sorted(grouped.items()):
        costs = [line.unit_cost for line in transactions]
        total_qty = sum(line.qty for line in transactions if line.qty)
        total_spend = sum(line.unit_cost * line.qty for line in transactions if line.qty)
        dated = [line for line in transactions if line.when is not None]
        dated.sort(key=lambda line: line.when)

        latest = dated[-1] if dated else transactions[-1]
        latest_date = latest.when

        # Vendors, most recent first, so the latest is the one a buyer would
        # call. A part bought from one supplier only is worth knowing about.
        vendors = [line.vendor for line in reversed(dated) if line.vendor]
        vendors += [line.vendor for line in transactions
                    if line.vendor and line.when is None]
        unique_vendors = list(dict.fromkeys(vendors))
        if unique_vendors:
            saw_vendor = True

        info = master.get(part, {})
        description = info.get("description", "")
        if not description:
            # The full PO export carries descriptions too, so a part missing
            # from the item master is no longer nameless.
            description = next(
                (line.description for line in reversed(dated) if line.description),
                next((line.description for line in transactions if line.description), ""),
            )
            if description:
                report.descriptions_from_po += 1
        if not description:
            report.parts_without_description.append(part)

        uom = (info.get("uom") or "").upper()
        if uom not in ("KG", "EA", "M", "LB", "L", "G"):
            uom = _infer_uom(part, description)

        rows.append({
            "part_number": part,
            "description": description,
            "uom": uom,
            "latest_unit_cost": f"{latest.unit_cost:.6g}",
            "min_unit_cost_ever": f"{min(costs):.6g}",
            "max_unit_cost_ever": f"{max(costs):.6g}",
            "latest_po_date": latest_date.isoformat() if latest_date else "",
            "latest_vendor": unique_vendors[0] if unique_vendors else "",
            "unique_vendor_count": str(len(unique_vendors)) if unique_vendors else "",
            "po_count": str(len(transactions)),
            # Spend ranks the curation queue: a handful of parts carry most of
            # it, so R&D need not confirm a thousand rows to quote accurately.
            "total_qty": f"{total_qty:.6g}",
            "total_spend": f"{total_spend:.2f}",
        })

    report.parts_out = len(rows)
    report.vendors_present = saw_vendor
    if rows and not saw_vendor:
        report.warnings.append(
            "The PO exports carry no vendor column, so vendor and vendor count are "
            "left blank. Single-supplier flags stay silent rather than firing on "
            "every line; supply a vendor export to enable them."
        )
    return rows


def _infer_uom(part: str, description: str) -> str:
    text = f"{part} {description}".lower()
    if "capsule" in text and ("size" in text or part.upper().startswith("RECA")):
        return "M"
    if part.upper().startswith(("K", "PK")):
        return "EA"
    return "KG"


FIELDNAMES = [
    "part_number", "description", "uom", "latest_unit_cost", "min_unit_cost_ever",
    "max_unit_cost_ever", "latest_po_date", "latest_vendor", "unique_vendor_count",
    "po_count", "total_qty", "total_spend",
]


def write_po_history(rows: list[dict], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def build(
    po_paths: list[Path],
    master_paths: list[Path],
    destination: Path,
    master_sheet: str | None = None,
) -> IngestReport:
    """Run the whole ingest and write the PO-history reference table."""
    report = IngestReport()
    master = load_item_master(master_paths, report, master_sheet) if master_paths else {}
    grouped = read_po_transactions(po_paths, report)
    rows = aggregate(grouped, master, report)
    write_po_history(rows, destination)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m quickquote.reference.ingest",
        description="Build po_history.csv from transaction-level PO exports.",
    )
    parser.add_argument("--po", action="append", required=True, type=Path,
                        help="A PO transaction export (xlsx or csv). Repeatable.")
    parser.add_argument("--item-master", action="append", default=[], type=Path,
                        help="A workbook or csv holding part -> description. Repeatable.")
    parser.add_argument("--master-sheet", default=None,
                        help="Sheet name in the item master, if auto-detection picks wrong.")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent / "data" / "po_history.csv")
    arguments = parser.parse_args(argv)

    for path in [*arguments.po, *arguments.item_master]:
        if not path.exists():
            parser.error(f"file not found: {path}")

    report = build(arguments.po, arguments.item_master, arguments.out, arguments.master_sheet)
    print(report.render())
    print(f"\nWrote {arguments.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
