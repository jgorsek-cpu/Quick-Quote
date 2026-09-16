"""R&D's own reference workbooks, and what the engine does with them.

The four sheets R&D maintain replaced the industry-typical placeholders the
system shipped with. Three of the four changed answers the engine was already
giving, so the behaviour each one drives is asserted here rather than in the
sheet it came from.
"""
import pytest
from openpyxl import Workbook

from quickquote.engine.classify import classify_ingredient
from quickquote.engine.identity import resolve_identity
from quickquote.engine.matching import match_line
from quickquote.engine.pipeline import run_pipeline
from quickquote.reference.ingest_rnd import (
    Report,
    load_capsules,
    load_density,
    load_overage,
    load_potency,
)
from quickquote.reference.loader import load_reference_data
from quickquote.schemas import FormulaLine, ParsedQuote, ProductSpec

from .conftest import AS_OF


# ----------------------------------------------------------------- ingest

def _book(sheet_name, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    for row in rows:
        sheet.append(list(row))
    return book


def _write(tmp_path, name, sheet_name, rows):
    path = tmp_path / name
    _book(sheet_name, rows).save(path)
    return path


OVERAGE_HEADER = [
    ("Active", "Multis (Caps, Tabs, Powder)", "Single (Caps, Tabs, Powder)",
     "Multis (Gummies)", "Single (Gummies)", "Column 1"),
    ("Vitamins", "Recommended Overage", "Recommended Overage",
     "Recommended Overage", "Recommended Overage", None),
]


class TestOverageIngest:
    def test_gummies_are_a_separate_column(self, tmp_path):
        path = _write(tmp_path, "o.xlsx", "Overages", OVERAGE_HEADER + [
            ("Vitamin C", 0.2, 0.1, 0.4, 0.3, None),
        ])
        row = load_overage(path, Report())[0]
        assert row["multi_ingredient_pct"] == "20"
        assert row["gummy_multi_pct"] == "40"
        assert row["gummy_single_pct"] == "30"

    def test_a_non_numeric_value_is_carried_as_unknown(self, tmp_path):
        """"Strain Dependent" is an answer. Coercing it to a number is not."""
        report = Report()
        path = _write(tmp_path, "o.xlsx", "Overages", OVERAGE_HEADER + [
            ("Spore Forming", 0.2, 0.1, "Strain Dependent", "Strain Dependent", None),
        ])
        row = load_overage(path, report)[0]
        assert row["multi_ingredient_pct"] == "20"
        assert row["gummy_multi_pct"] == ""
        assert any("Strain Dependent" in note for note in report.overage_non_numeric)

    def test_a_class_inside_another_row_is_not_called_a_placeholder(self, tmp_path):
        """Iron is priced in R&D's calcium row; the engine keys on it separately."""
        path = _write(tmp_path, "o.xlsx", "Overages", OVERAGE_HEADER + [
            ("Calcium, Magnesium, Zinc, Iron, Boron", 0.02, 0.02, 0.1, 0.05, None),
        ])
        rows = {row["overage_class"]: row for row in load_overage(path, Report())}
        assert rows["iron"]["multi_ingredient_pct"] == "2"
        assert rows["iron"]["source"] == "R&D Overage Guidelines"
        assert "Iron" in rows["iron"]["notes"]

    def test_a_class_r_and_d_do_not_cover_says_so(self, tmp_path):
        path = _write(tmp_path, "o.xlsx", "Overages", OVERAGE_HEADER + [
            ("Vitamin C", 0.2, 0.1, 0.4, 0.3, None),
        ])
        rows = {row["overage_class"]: row for row in load_overage(path, Report())}
        assert "NOT IN R&D GUIDELINE" in rows["vitamin_b12"]["source"]

    def test_a_row_with_numbers_and_no_mapping_is_reported(self, tmp_path):
        report = Report()
        path = _write(tmp_path, "o.xlsx", "Overages", OVERAGE_HEADER + [
            ("Nootropics", 0.4, 0.2, 0.5, 0.3, None),
        ])
        load_overage(path, report)
        assert report.overage_unmapped == ["Nootropics"]

    def test_a_section_header_is_not(self, tmp_path):
        report = Report()
        path = _write(tmp_path, "o.xlsx", "Overages", OVERAGE_HEADER + [
            ("Minerals", None, None, None, None, None),
        ])
        load_overage(path, report)
        assert report.overage_unmapped == []


POTENCY_HEADER = [
    ("Potency of Material", None, None, None, None, None, None),
    ("Date updated : 04/14/26", None, None, None, None, None, None),
    ("Part Code", "Description (* Claimed as)", "Potency", "% Element",
     "Element Conversion", "Min. Purity", "Remarks"),
]


class TestPotencyIngest:
    def test_the_asterisk_marks_what_the_label_claims(self, tmp_path):
        path = _write(tmp_path, "p.xlsx", "Sheet1", POTENCY_HEADER + [
            ("RAARG1", "L-Arginine HCl*", 0.983, 100.0, None, 98.0, None),
            ("RAARG1", "L-Arginine* HCl", 0.813, 82.75, "174.20/210.66", 98.0, None),
        ])
        rows = load_potency(path, Report())
        assert rows[0]["claims_whole_material"] == "1"
        assert rows[1]["claims_whole_material"] == "0"

    def test_a_part_with_several_bases_keeps_every_one(self, tmp_path):
        report = Report()
        path = _write(tmp_path, "p.xlsx", "Sheet1", POTENCY_HEADER + [
            ("RAARG1", "L-Arginine HCl*", 0.983, 100.0, None, 98.0, None),
            ("RAARG1", "L-Arginine* HCl", 0.813, 82.75, "174.20/210.66", 98.0, None),
        ])
        assert len(load_potency(path, report)) == 2
        assert report.potency_parts == 1
        assert report.potency_ambiguous == ["RAARG1"]


class TestDensityIngest:
    def test_lots_collapse_to_a_median_and_a_range(self, tmp_path):
        report = Report()
        path = _write(tmp_path, "t.xlsx", "Raw Material Data", [
            ("  Part Code", "Input Date", "DV-Lot #", "Raw Material Name", "Bulk Density"),
            ("RBPVS4", None, "A", "PLANT STEROLS", 0.400),
            ("RBPVS4", None, "B", "PLANT STEROLS", 0.500),
            ("RBPVS4", None, "C", "PLANT STEROLS", 0.909),
        ])
        row = load_density(path, report)[0]
        assert row["median_g_ml"] == "0.5"
        assert row["min_g_ml"] == "0.4" and row["max_g_ml"] == "0.909"
        assert row["lot_count"] == "3"
        assert report.density_wide_spread == ["RBPVS4"]


class TestCapsuleIngest:
    def test_the_elongated_sizes_are_read(self, tmp_path):
        report = Report()
        path = _write(tmp_path, "c.xlsx", "Capsule Specs", [
            ("Approximate Recommended Capsule Fill Weights", None, None),
            ("Calculated as nominal capsule volume x density x 1,000", None, None),
            ("Capsule Size", "At 0.5 g/mL", "At 0.7 g/mL"),
            ("00E (1.02 mL)", 510, 714),
            ("00 (0.95 mL)", 475, 665),
        ])
        rows, densities = load_capsules(path, report)
        assert densities == ["0.5", "0.7"]
        assert rows[0] == {"capsule_size": "00e", "volume_ml": "1.02",
                           "0.5": "510", "0.7": "714"}


# ---------------------------------------------------------------- engine

class TestGummyOverage:
    def test_a_gummy_takes_the_gummy_column(self, reference):
        caps = reference.overage_pct("vitamin_c", multi_ingredient=True)
        gummy = reference.overage_pct("vitamin_c", multi_ingredient=True, gummy=True)
        assert gummy > caps

    def test_the_form_decides_which_column_is_used(self, reference):
        def classify(form):
            return classify_ingredient(
                name="Ascorbic Acid", reference=reference,
                resolution=resolve_identity("Ascorbic Acid", reference),
                input_part_code=None, matched_code=None, multi_ingredient=True,
                gummy=(form == "gummy"),
            )

        assert classify("gummy").overage_pct > classify("capsule").overage_pct
        assert "gummy column" in classify("gummy").overage_source

    def test_a_missing_gummy_figure_falls_back_and_says_so(self, reference):
        """R&D record probiotic gummy overage as "Strain Dependent"."""
        assert reference.overage_pct("probiotic", True, gummy=True) is None
        classification = classify_ingredient(
            name="Lactobacillus acidophilus blend", reference=reference,
            resolution=resolve_identity("Lactobacillus acidophilus blend", reference),
            input_part_code=None, matched_code=None, multi_ingredient=True, gummy=True,
        )
        assert classification.overage_class == "probiotic"
        assert classification.overage_pct == reference.overage_pct("probiotic", True)
        assert "no gummy figure" in classification.overage_source

    def test_that_fallback_reaches_the_flags(self, reference):
        parsed = ParsedQuote(
            product=ProductSpec(dosage_form="Gummy", count_per_bottle=60,
                                annual_volume_bottles=2000),
            formula=[FormulaLine("Probiotic Blend 10 Billion CFU", 100.0),
                     FormulaLine("Ascorbic Acid", 60.0)],
            packaging=[], source_name="t", source_format="manual",
        )
        reasons = [flag.reason for flag in run_pipeline(parsed, reference, as_of=AS_OF).flags]
        assert any("no gummy figure" in reason for reason in reasons)


class TestClaimBasis:
    """A part with several claim bases has no single potency to look up."""

    @pytest.fixture
    def catalogue(self, tmp_path, reference):
        import shutil

        target = tmp_path / "reference"
        target.mkdir()
        for path in reference.source_dir.glob("*.csv"):
            shutil.copy(path, target / path.name)
        (target / "potency.csv").write_text(
            "part_code,claim_description,claims_whole_material,potency_factor\n"
            "RAARG1,L-Arginine HCl*,1,0.983\n"
            "RAARG1,L-Arginine* HCl,0,0.813\n"
            "RAGLU1,L-Glutamine,1,0.995\n"
        )
        return load_reference_data(target)

    def test_one_basis_is_looked_up_as_usual(self, catalogue):
        assert catalogue.potency_for_part("RAGLU1") == pytest.approx(0.995)

    def test_several_bases_refuse_to_answer(self, catalogue):
        assert catalogue.potency_for_part("RAARG1") is None
        assert len(catalogue.potency_claims_for_part("RAARG1")) == 2

    def test_the_line_is_costed_at_1_0_and_the_bases_are_named(self, catalogue):
        classification = classify_ingredient(
            name="L-Arginine", reference=catalogue,
            resolution=resolve_identity("L-Arginine", catalogue),
            input_part_code="RAARG1", matched_code=None, multi_ingredient=True,
        )
        assert classification.potency == pytest.approx(1.0)
        assert classification.potency_defaulted
        assert "0.983" in classification.potency_source
        assert "0.813" in classification.potency_source

    def test_it_is_flagged_to_r_and_d_as_a_question_not_a_gap(self, catalogue):
        parsed = ParsedQuote(
            product=ProductSpec(dosage_form="Capsule", capsule_size="0",
                                count_per_bottle=60, annual_volume_bottles=2000),
            formula=[FormulaLine("L-Arginine", 500.0, part_code="RAARG1")],
            packaging=[], source_name="t", source_format="manual",
        )
        flags = run_pipeline(parsed, catalogue, as_of=AS_OF).flags
        claim = [flag for flag in flags if "claim bases" in flag.reason]
        assert claim and claim[0].owner == "R&D"
        assert "0.983" in claim[0].reason


class TestMeasuredDensity:
    @pytest.fixture
    def catalogue(self, tmp_path, reference):
        import shutil

        target = tmp_path / "reference"
        target.mkdir()
        for path in reference.source_dir.glob("*.csv"):
            shutil.copy(path, target / path.name)
        (target / "bulk_density_measured.csv").write_text(
            "part_code,material,median_g_ml,min_g_ml,max_g_ml,lot_count\n"
            "RBPVS4,PLANT STEROLS,0.5,0.4,0.909,7\n"
        )
        return load_reference_data(target)

    def test_lot_measurements_beat_a_class_average(self, catalogue):
        measured = catalogue.measured_density_for("RBPVS4")
        assert measured is not None and measured.median_g_ml == pytest.approx(0.5)
        assert measured.wide_spread

    def test_a_wide_spread_is_flagged_with_its_range(self, catalogue):
        parsed = ParsedQuote(
            product=ProductSpec(dosage_form="Capsule", capsule_size="0",
                                count_per_bottle=60, annual_volume_bottles=2000),
            formula=[FormulaLine("Plant Sterols", 400.0, part_code="RBPVS4")],
            packaging=[], source_name="t", source_format="manual",
        )
        flags = run_pipeline(parsed, catalogue, as_of=AS_OF).flags
        spread = [flag for flag in flags if "varies across lots" in flag.reason]
        assert spread and "0.4-0.909" in spread[0].reason
        assert "7 lots" in spread[0].reason


class TestTampingModel:
    """R&D's calculator, reproduced from the worked examples in the workbook."""

    def test_density_rounds_down_before_the_offset_is_applied(self, reference):
        base, adjusted = reference.tamped_density(0.55)
        assert base == pytest.approx(0.5)
        assert adjusted == pytest.approx(0.7)

    def test_the_default_offset_is_two_columns(self, reference):
        assert reference.tamping_steps == 2
        assert reference.tamped_density(0.5)[1] == pytest.approx(0.7)

    def test_it_never_runs_off_the_end_of_the_chart(self, reference):
        columns = reference.density_columns()
        assert reference.tamped_density(columns[-1])[1] == pytest.approx(columns[-1])

    def test_the_workbook_example_reproduces(self, reference):
        """650 mg/cap at 0.50 g/mL -> Size 00 with 665 mg of tamped capacity."""
        fit = reference.recommend_capsule_size(650, 0.5)
        assert fit.capsule_size == "00"
        assert fit.capacity_mg == pytest.approx(665)
        assert fit.adjusted_density_g_ml == pytest.approx(0.7)
        assert fit.utilisation == pytest.approx(650 / 665)

    def test_the_batch_example_reproduces(self, reference):
        """1000 mg over 2 caps at 0.65 g/mL -> Size 0 with 544 mg."""
        fit = reference.recommend_capsule_size(500, 0.65)
        assert fit.capsule_size == "0"
        assert fit.base_density_g_ml == pytest.approx(0.6)
        assert fit.adjusted_density_g_ml == pytest.approx(0.8)
        assert fit.capacity_mg == pytest.approx(544)

    def test_an_elongated_shell_is_available_to_choose(self, reference):
        assert reference.capsule_volume_ml("00e") == pytest.approx(1.02)
        assert reference.capsule_volume_ml("0e") == pytest.approx(0.78)
        fit = reference.recommend_capsule_size(700, 0.5)
        assert fit.capsule_size == "00e"

    def test_a_blend_between_columns_is_never_credited_upward(self, reference):
        """0.69 g/mL reads the 0.6 column, not the 0.7 one, before tamping."""
        assert reference.tamped_density(0.69)[0] == pytest.approx(0.6)

    def test_capacity_is_refused_without_a_density(self, reference):
        assert reference.tamped_density(None) is None
        assert reference.recommend_capsule_size(500, None) is None


class TestClassificationAgainstRndsGroups:
    """R&D's groups are finer than the placeholders', so the rules follow them."""

    @pytest.mark.parametrize("name,expected", [
        ("Thiamine Mononitrate USP", "thiamine"),
        ("Riboflavin 5-Phosphate", "riboflavin"),
        ("Niacinamide", "niacin"),
        ("D-Calcium Pantothenate", "pantothenic_acid"),
        ("Pyridoxine HCl", "vitamin_b6"),
        ("Methylcobalamin 1% on DCP", "vitamin_b12"),
    ])
    def test_each_b_vitamin_keeps_its_own_class(self, name, expected):
        from quickquote.engine.classify import classify_by_name

        assert classify_by_name(name) == expected

    def test_a_b_complex_is_flagged_rather_than_averaged(self):
        """No single overage is right for a blend of B vitamins."""
        from quickquote.engine.classify import classify_by_name

        assert classify_by_name("Vitamin B Complex") is None

    def test_beta_carotene_is_not_vitamin_a(self, reference):
        from quickquote.engine.classify import classify_by_name

        assert classify_by_name("Beta Carotene 10% CWS") == "beta_carotene"
        assert (reference.overage_pct("beta_carotene", True)
                != reference.overage_pct("vitamin_a", True))

    @pytest.mark.parametrize("name,expected", [
        # R&D group zinc, iron and boron with the macro minerals.
        ("Zinc Bisglycinate", "calcium_magnesium"),
        ("Boron Citrate", "calcium_magnesium"),
        ("Ferrous Bisglycinate", "iron"),
        ("Chromium Picolinate", "trace_minerals"),
        ("Selenomethionine", "trace_minerals"),
        ("Potassium Iodide", "iodine"),
    ])
    def test_minerals_follow_r_and_ds_grouping(self, name, expected):
        from quickquote.engine.classify import classify_by_name

        assert classify_by_name(name) == expected

    def test_lactobacillus_is_not_read_as_bacillus(self, reference):
        """"Bacillus" is a substring of "Lactobacillus" and a different class:
        spore formers take 20% overage where non-spore formers take 70%."""
        from quickquote.engine.classify import classify_by_name

        assert classify_by_name("Lactobacillus acidophilus") == "probiotic_non_spore"
        assert classify_by_name("Bacillus coagulans 15 Billion") == "probiotic_spore"
        assert (reference.overage_pct("probiotic_spore", True)
                < reference.overage_pct("probiotic_non_spore", True))

    def test_ubiquinol_and_ubiquinone_are_separated(self, reference):
        from quickquote.engine.classify import classify_by_name

        assert classify_by_name("Kaneka Ubiquinol") == "ubiquinol"
        assert classify_by_name("Coenzyme Q10 Ubiquinone") == "coq10"
        # They differ only in a gummy, which is where R&D separate them.
        assert (reference.overage_pct("ubiquinol", True, gummy=True)
                > reference.overage_pct("coq10", True, gummy=True))
