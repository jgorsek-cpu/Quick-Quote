"""Cost math, defaults and the specification's worked example."""
import pytest

from quickquote.config import ACCEPTED, LOW, NEEDS_REVIEW
from quickquote.engine.classify import classify_ingredient
from quickquote.engine.costing import cost_ingredient, cost_packaging
from quickquote.engine.identity import resolve_identity
from quickquote.engine.manufacturing import estimate_manufacturing
from quickquote.engine.matching import match_line
from quickquote.engine.pipeline import run_pipeline
from quickquote.schemas import FormulaLine, PackagingLine, ProductSpec

from .conftest import AS_OF


def cost_one(name, mg, reference, servings=30, loss=1.05, part=None):
    line = FormulaLine(name, mg, part_code=part)
    match = match_line(name, part, reference)
    resolution = resolve_identity(name, reference)
    classification = classify_ingredient(name, reference, resolution, part,
                                         match.matched_code, True)
    return cost_ingredient(line, match, classification, reference, servings, loss)


class TestRawMaterialCost:
    def test_formula_matches_the_documented_equation(self, reference):
        costed = cost_one("Coenzyme Q10", 100.0, reference)
        # 100mg / potency 1.0 x (1 + 10% overage) = 110 mg/serving
        assert costed.formula_mg_per_serving == pytest.approx(110.0)
        # 110 x 30 servings / 1e6 x 1.05 yield loss = 0.003465 kg/bottle
        assert costed.kg_per_bottle == pytest.approx(0.003465)
        assert costed.cost_per_bottle == pytest.approx(0.003465 * 372.50)

    def test_range_is_plus_minus_ten_percent(self, reference):
        costed = cost_one("Coenzyme Q10", 100.0, reference)
        assert costed.cost_low == pytest.approx(costed.cost_per_bottle * 0.9)
        assert costed.cost_high == pytest.approx(costed.cost_per_bottle * 1.1)

    def test_potency_divides_the_claim(self, reference):
        """Magnesium oxide is 60% elemental, so more material is needed."""
        costed = cost_one("Magnesium Oxide", 100.0, reference)
        assert costed.potency == pytest.approx(0.60)
        assert costed.formula_mg_per_serving == pytest.approx(100 / 0.60 * 1.03)

    def test_unmatched_line_has_no_cost(self, reference):
        costed = cost_one("Aged Garlic Extract", 200.0, reference)
        assert not costed.match.accepted
        assert costed.cost_per_bottle is None

    def test_missing_claimed_mg_produces_no_cost(self, reference):
        costed = cost_one("Coenzyme Q10", None, reference)
        assert costed.cost_per_bottle is None
        assert any("claimed mg" in note for note in costed.notes)


class TestDefaults:
    def test_unknown_potency_defaults_to_one_and_is_recorded(self, reference):
        costed = cost_one("Red Yeast Rice", 300.0, reference)
        assert costed.potency == 1.0
        assert costed.potency_source.startswith("Default")

    def test_unknown_overage_class_defaults_to_five_percent(self, reference):
        costed = cost_one("Unobtainium Complex", 100.0, reference)
        assert costed.overage_class == "default"
        assert costed.overage_pct == pytest.approx(0.05)


class TestPackagingCost:
    def test_capsule_shells_are_priced_per_thousand(self, reference):
        line = PackagingLine("capsule_shell", "Vegetable Capsule Shell Size 00")
        match = match_line(line.description, None, reference, packaging=True)
        costed = cost_packaging(line, match, reference, 90)
        assert match.uom == "M"
        assert costed.cost_per_bottle == pytest.approx(90 / 1000 * 3.65)

    def test_quote_sheet_cost_wins_over_po_history(self, reference):
        line = PackagingLine("bottle", "HDPE Bottle White 175cc", listed_unit_cost=0.99)
        match = match_line(line.description, None, reference, packaging=True)
        costed = cost_packaging(line, match, reference, 1)
        assert costed.unit_cost == 0.99
        assert costed.unit_cost_source == "Quote sheet listed cost"

    def test_uom_sanity_check_demotes_an_implausible_line(self, reference):
        """A per-bottle component priced like a case must not pass silently."""
        line = PackagingLine("shipper", "Corrugated Shipper")
        match = match_line(line.description, None, reference, packaging=True)
        assert match.status == ACCEPTED
        costed = cost_packaging(line, match, reference, 10)   # 10 x $1.20 = $12/bottle
        assert match.status == NEEDS_REVIEW
        assert "sanity ceiling" in match.reason


class TestManufacturing:
    def test_compounding_hours_follow_the_component_bands(self, reference):
        assert reference.compounding_hours(1) == 4.25
        assert reference.compounding_hours(7) == 4.75
        assert reference.compounding_hours(15) == 6.75
        assert reference.compounding_hours(30) == 11.75

    def test_no_volume_means_no_estimate(self, reference):
        estimate = estimate_manufacturing(ProductSpec(dosage_form="Capsule"), 7, reference)
        assert not estimate.estimated
        assert "Volume not provided" in estimate.reason

    def test_machine_default_is_recorded_as_assumed(self, reference):
        estimate = estimate_manufacturing(
            ProductSpec(dosage_form="Capsule", annual_volume_bottles=1000), 7, reference)
        assert estimate.machine_assumed
        assert estimate.machine == "BOSCH 705 + CVC1"


class TestSpecificationExample:
    """The Vitamin Shoppe example printed in section 7 of the specification."""

    def test_reproduces_the_published_breakdown(self, heart_health, reference):
        result = run_pipeline(heart_health, reference, as_of=AS_OF)
        summary = result.summary
        assert summary.raw_materials == pytest.approx(4.51, abs=0.02)
        assert summary.packaging == pytest.approx(0.85, abs=0.01)
        assert summary.manufacturing == pytest.approx(1.28, abs=0.01)
        assert summary.primary_per_bottle == pytest.approx(6.64, abs=0.02)

    def test_confidence_is_low_because_lines_are_outstanding(self, heart_health, reference):
        result = run_pipeline(heart_health, reference, as_of=AS_OF)
        assert result.summary.quote_confidence == LOW
        assert set(result.summary.unmatched_items) == {"Aged Garlic Extract", "Red Yeast Rice"}

    def test_only_accepted_lines_reach_the_primary_total(self, heart_health, reference):
        result = run_pipeline(heart_health, reference, as_of=AS_OF)
        accepted = sum(line.cost_per_bottle or 0 for line in result.ingredients
                       if line.match.accepted)
        assert result.summary.raw_materials == pytest.approx(accepted)

    def test_the_published_flags_are_all_present(self, heart_health, reference):
        result = run_pipeline(heart_health, reference, as_of=AS_OF)
        reasons = " ".join(f"{flag.owner}|{flag.item}|{flag.reason}" for flag in result.flags)
        assert "419 days old" in reasons                      # stale grape extract PO
        assert "$152.50-$525.00" in reasons                   # CoQ10 price variance
        assert "no po record found for identity 'aged garlic extract'" in reasons.lower()
        assert "Potency unknown" in reasons                   # red yeast rice
        assert "assumed BOSCH 705 + CVC1" in reasons          # machine default
        assert "MOQ not specified" in reasons
        assert "Timeline not specified" in reasons
        assert "understates true cost" in reasons             # Finance
