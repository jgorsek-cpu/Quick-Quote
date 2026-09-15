"""Ingestion of transaction-level PO exports and the identity seed generator."""
import csv

import pytest
from openpyxl import Workbook

from quickquote.reference.ingest import build
from quickquote.reference.loader import load_po_history, load_reference_data
from quickquote.reference.seed import propose


@pytest.fixture
def exports(tmp_path):
    """An export shaped like the operational one: transactions plus subtotals."""
    po = Workbook()
    sheet = po.active
    sheet.append(["Purchase Order Date", "Part Number", "Order Qty", "Unit Cost"])
    for row in [
        ["2024-01-10", "RAGLU1", 200, 20.00],
        ["2025-06-02", "RAGLU1", 150, 24.00],
        ["2026-03-14", "RAGLU1", 300, 22.50],
        ["Part Total", 650, None, None],          # report subtotal, not a purchase
        ["2026-02-01", "KB100X24C", 1000, 0.48],
        ["2026-02-01", "", None, None],           # blank row
    ]:
        sheet.append(row)
    po_path = tmp_path / "po.xlsx"
    po.save(po_path)

    master = Workbook()
    master_sheet = master.active
    master_sheet.title = "Inv Master"
    master_sheet.append(["PART", "PRODUCT_LINE", "DESCRIPTION", "UM_PURCHASING"])
    master_sheet.append(["RAGLU1", "10", "L-GLUTAMINE", "KG"])
    master_sheet.append(["KB100X24C", "85", "11-1/4 x 7-1/2 x 3-1/2 BOX", "EA"])
    master_path = tmp_path / "master.xlsx"
    master.save(master_path)

    out = tmp_path / "po_history.csv"
    report = build([po_path], [master_path], out)
    return report, out


def test_transactions_aggregate_to_one_row_per_part(exports):
    report, out = exports
    rows = {row["part_number"]: row for row in csv.DictReader(out.open())}
    assert set(rows) == {"RAGLU1", "KB100X24C"}
    glutamine = rows["RAGLU1"]
    assert glutamine["po_count"] == "3"
    assert float(glutamine["min_unit_cost_ever"]) == 20.00
    assert float(glutamine["max_unit_cost_ever"]) == 24.00


def test_latest_cost_comes_from_the_latest_date_not_the_last_row(exports):
    _, out = exports
    rows = {row["part_number"]: row for row in csv.DictReader(out.open())}
    assert float(rows["RAGLU1"]["latest_unit_cost"]) == 22.50
    assert rows["RAGLU1"]["latest_po_date"] == "2026-03-14"


def test_subtotal_rows_are_skipped_and_counted(exports):
    report, _ = exports
    assert report.subtotal_rows_skipped >= 1
    assert report.transactions_read == 4


def test_descriptions_come_from_the_item_master(exports):
    _, out = exports
    rows = {row["part_number"]: row for row in csv.DictReader(out.open())}
    assert rows["RAGLU1"]["description"] == "L-GLUTAMINE"
    assert rows["RAGLU1"]["uom"] == "KG"
    assert rows["KB100X24C"]["uom"] == "EA"


def test_absent_vendor_data_is_left_blank_not_invented(exports):
    report, out = exports
    rows = list(csv.DictReader(out.open()))
    assert all(row["latest_vendor"] == "" for row in rows)
    assert all(row["unique_vendor_count"] == "" for row in rows)
    assert any("no vendor column" in warning for warning in report.warnings)


def test_blank_vendor_count_never_fires_the_single_supplier_flag(exports):
    """unique_vendor_count of None must not read as 'one vendor'."""
    _, out = exports
    rows = load_po_history(out)
    assert all(row.unique_vendor_count != 1 for row in rows)


class TestIdentitySeed:
    def test_proposals_skip_descriptions_already_curated(self, tmp_path):
        reference = load_reference_data()
        rows = load_po_history(
            _write_history(tmp_path, [("RM-X", "Coenzyme Q10 Ubiquinone USP Powder", "KG"),
                                      ("RM-Y", "Totally Novel Botanical Blend", "KG")]))
        ingredients, _, stats = propose(rows, reference)
        names = {row["canonical"] for row in ingredients}
        assert "totally novel botanical blend" in names
        assert stats["already_resolved"] == 1

    def test_potency_is_left_unknown_for_review(self, tmp_path):
        reference = load_reference_data()
        rows = load_po_history(
            _write_history(tmp_path, [("RM-Y", "Totally Novel Botanical Blend", "KG")]))
        ingredients, _, _ = propose(rows, reference)
        assert ingredients[0]["default_potency"] == ""
        assert ingredients[0]["notes"].startswith("PROPOSED")

    def test_two_parts_sharing_a_description_are_marked(self, tmp_path):
        reference = load_reference_data()
        rows = load_po_history(
            _write_history(tmp_path, [("RM-1", "Novel Grade Material", "KG"),
                                      ("RM-2", "Novel Grade Material", "KG")]))
        ingredients, _, stats = propose(rows, reference)
        assert stats["collisions"] == 1
        assert "Needs Review" in ingredients[0]["notes"]


def _write_history(tmp_path, entries):
    path = tmp_path / f"h{len(entries)}{entries[0][0]}.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["part_number", "description", "uom", "latest_unit_cost",
                         "min_unit_cost_ever", "max_unit_cost_ever", "latest_po_date",
                         "latest_vendor", "unique_vendor_count", "po_count"])
        for part, description, uom in entries:
            writer.writerow([part, description, uom, 10, 9, 11, "2026-01-01", "", "", 3])
    return path
