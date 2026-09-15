"""Parsing must record what is absent rather than fill it in."""
import pytest

from quickquote.parsing.extract import infer_role
from quickquote.parsing.readers import UnsupportedFormat, read_document
from quickquote.parsing.router import parse_document

TEXT_SHEET = b"""ACME NUTRITION - QUOTE REQUEST

Customer: Acme Nutrition
Formula Name: Joint Support Complex 120ct
Dosage Form: Capsule
Capsule Size: 0
Servings Per Bottle: 60
Count Per Bottle: 120
Annual Volume: 8000

Ingredient, Part Code, Amount Per Serving, UOM
Glucosamine Sulfate, , 750, mg
MSM, , 500, mg
Vitamin D3, , 25, mcg
Vitamin A, , 3000, IU

Bottle: HDPE Bottle White 250cc
Cap: Child-Resistant Cap White 53mm
"""


@pytest.fixture
def acme():
    return parse_document(TEXT_SHEET, "acme.txt")


class TestFieldExtraction:
    def test_labelled_fields_are_read(self, acme):
        assert acme.product.customer == "Acme Nutrition"
        assert acme.product.formula_name == "Joint Support Complex 120ct"
        assert acme.product.servings_per_bottle == 60
        assert acme.product.count_per_bottle == 120
        assert acme.product.annual_volume_bottles == 8000
        assert acme.product.capsule_size == "0"

    def test_absent_fields_are_recorded_not_invented(self, acme):
        assert acme.product.moq is None
        assert acme.product.timeline is None
        assert "MOQ" in acme.missing_fields
        assert "Timeline" in acme.missing_fields

    def test_longest_label_wins_over_a_prefix(self):
        parsed = parse_document(
            b"Customer: Acme\nCustomer SKU: SKU-1\nIngredient,Amount\nMSM,100\n", "q.txt")
        assert parsed.product.customer == "Acme"
        assert parsed.product.customer_sku == "SKU-1"


class TestFormulaExtraction:
    def test_ingredient_rows_are_read(self, acme):
        names = [line.name for line in acme.formula]
        assert names[:3] == ["Glucosamine Sulfate", "MSM", "Vitamin D3"]

    def test_mcg_is_converted_to_mg(self, acme):
        vitamin_d = next(line for line in acme.formula if line.name == "Vitamin D3")
        assert vitamin_d.claimed_mg == pytest.approx(0.025)

    def test_iu_has_no_conversion_basis_so_stays_unknown(self, acme):
        vitamin_a = next(line for line in acme.formula if line.name == "Vitamin A")
        assert vitamin_a.claimed_mg is None
        assert "IU" in vitamin_a.notes

    def test_a_blank_line_ends_the_ingredient_block(self, acme):
        """The packaging declarations must not be read as ingredients."""
        assert not any("Bottle" in line.name for line in acme.formula)
        assert len(acme.formula) == 4


class TestPackagingExtraction:
    def test_label_value_declarations_are_read(self, acme):
        roles = {line.role for line in acme.packaging}
        assert roles == {"bottle", "cap"}

    def test_role_keywords_respect_word_boundaries(self):
        assert infer_role("Capsicum Extract") is None      # must not read as "cap"
        assert infer_role("CR Closure 45mm") == "cap"
        assert infer_role("Vegetable Capsule Shell") == "capsule_shell"


class TestFormats:
    def test_xlsx_round_trips(self, tmp_path):
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        for row in [["Customer", "Widget Co"], ["Servings Per Bottle", 30], [],
                    ["Ingredient", "Part Code", "Amount Per Serving", "UOM"],
                    ["Coenzyme Q10", "", 100, "mg"]]:
            sheet.append(row)
        path = tmp_path / "q.xlsx"
        book.save(path)

        parsed = parse_document(path.read_bytes(), "q.xlsx")
        assert parsed.source_format == "xlsx"
        assert parsed.product.customer == "Widget Co"
        assert parsed.formula[0].claimed_mg == 100

    def test_unsupported_extension_is_rejected(self):
        with pytest.raises(UnsupportedFormat):
            read_document(b"data", "photo.jpg")

    def test_a_document_with_no_ingredient_table_says_so(self):
        parsed = parse_document(b"Customer: Acme\n", "q.txt")
        assert parsed.formula == []
        assert "Ingredient list" in parsed.missing_fields
        assert parsed.parser_notes
