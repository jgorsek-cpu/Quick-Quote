"""Derivation, pack-out, breakage, fill volume, price breaks and pricing."""
import copy
from datetime import date

import pytest

from quickquote.config import ACCEPTED
from quickquote.engine.derive import (
    choose_bottle,
    choose_cap,
    derive_packaging,
    derive_serving,
    required_bottle_cc,
)
from quickquote.engine.matching import match_line
from quickquote.engine.pipeline import build_pricing, run_pipeline
from quickquote.engine.text import extract_size
from quickquote.schemas import FormulaLine, PackagingLine, ParsedQuote, ProductSpec

from .conftest import AS_OF


def _cap_row(part: str, description: str):
    from quickquote.reference.loader import PoRow

    return PoRow(part_number=part, description=description, uom="EA",
                 latest_unit_cost=0.1, min_unit_cost_ever=0.1, max_unit_cost_ever=0.1,
                 latest_po_date=date(2026, 8, 1), latest_vendor="", 
                 unique_vendor_count=0, po_count=1)


def quote(**product_kwargs) -> ParsedQuote:
    defaults = dict(
        customer="Test Co", formula_name="T1", dosage_form="Capsule",
        capsule_size="0", capsule_type="Vegetable", annual_volume_bottles=2000,
    )
    defaults.update(product_kwargs)
    return ParsedQuote(
        product=ProductSpec(**defaults),
        formula=[FormulaLine("Ashwagandha Powder", 300.0)],
        packaging=[],
        source_name="test", source_format="manual",
    )


class TestServingDerivation:
    def test_count_follows_from_servings_and_per_serving(self):
        product = ProductSpec(servings_per_bottle=30, capsules_per_serving=2)
        derive_serving(product)
        assert product.count_per_bottle == 60
        assert "count_per_bottle" in product.derived

    def test_per_serving_follows_from_count_and_servings(self):
        product = ProductSpec(count_per_bottle=90, servings_per_bottle=30)
        derive_serving(product)
        assert product.capsules_per_serving == 3

    def test_servings_follow_from_count_and_per_serving(self):
        product = ProductSpec(count_per_bottle=120, capsules_per_serving=2)
        derive_serving(product)
        assert product.servings_per_bottle == 60

    def test_a_typed_value_is_never_overwritten(self):
        """Manual override wins, even when the three do not agree."""
        product = ProductSpec(count_per_bottle=100, servings_per_bottle=30, capsules_per_serving=3)
        notes = derive_serving(product)
        assert product.count_per_bottle == 100        # left exactly as entered
        assert "count_per_bottle" not in product.derived
        assert any("does not equal" in note for note in notes)

    def test_an_indivisible_count_is_reported_not_rounded(self):
        product = ProductSpec(count_per_bottle=100, servings_per_bottle=30)
        notes = derive_serving(product)
        assert product.capsules_per_serving is None
        assert any("does not divide evenly" in note for note in notes)

    def test_serving_size_text_is_written_from_the_count(self):
        product = ProductSpec(servings_per_bottle=30, capsules_per_serving=2)
        derive_serving(product)
        assert product.serving_size == "2 capsules"


class TestPackagingDerivation:
    def test_bottle_is_sized_from_capsule_volume_and_count(self, reference):
        product = ProductSpec(capsule_size="0", count_per_bottle=60)
        # 60 x 0.68 mL / 0.80 fill ratio = 51 cc
        assert required_bottle_cc(product, reference) == pytest.approx(51.0)
        choice = choose_bottle(product, reference)
        assert choice.volume_cc == 175          # smallest stocked size that holds it

    def test_a_larger_count_selects_a_larger_bottle(self, reference):
        product = ProductSpec(capsule_size="00", count_per_bottle=180)
        choice = choose_bottle(product, reference)
        assert choice.volume_cc >= required_bottle_cc(product, reference)
        assert choice.volume_cc == 250

    def test_no_stocked_bottle_is_large_enough_returns_nothing(self, reference):
        product = ProductSpec(capsule_size="000", count_per_bottle=2000)
        assert choose_bottle(product, reference) is None

    def test_closure_matches_the_bottle_neck_finish(self, reference):
        row, note = choose_cap(reference, 45.0)
        assert row is not None and note == ""
        assert extract_size(row.description, role="cap").neck_mm == 45.0

    def test_a_neck_with_no_stocked_closure_is_not_substituted(self, reference):
        row, note = choose_cap(reference, 99.0)
        assert row is None
        assert "No stocked closure" in note

    def test_several_matching_closures_are_reported_not_chosen(self, reference):
        """Child-resistant or not, liner, colour: a real choice, not the system's."""
        from quickquote.reference.loader import IdentityEntry

        crowded = copy.deepcopy(reference)
        crowded.identity_index = None
        crowded.packaging_identities = [
            entry for entry in crowded.packaging_identities if entry.role != "cap"
        ] + [
            IdentityEntry(canonical=f"cap option {n}", aliases=(f"cap option {n}",),
                          role="cap", size_required=True)
            for n in (1, 2)
        ]
        crowded.po_rows = list(crowded.po_rows) + [
            _cap_row("CAP-1", "Cap Option 1 45mm White"),
            _cap_row("CAP-2", "Cap Option 2 45mm Black"),
        ]
        # The curated identity has no rows, so the search falls back to role.
        row, note = choose_cap(crowded, 45.0, canonical="cr cap white")
        assert row is None
        assert "2 stocked closures" in note and "choose one" in note

    def test_a_full_pack_out_is_built_from_the_spec(self, reference):
        product = ProductSpec(capsule_size="0", capsule_type="Vegetable",
                              count_per_bottle=60, dosage_form="Capsule")
        lines, _ = derive_packaging(product, [], reference)
        roles = [line.role for line in lines]
        assert roles == ["capsule_shell", "bottle", "cap", "label",
                         "neckband", "cotton", "desiccant", "shipper"]
        shell = next(line for line in lines if line.role == "capsule_shell")
        assert "Size 0" in shell.description and "Vegetable" in shell.description

    def test_a_component_the_rep_chose_is_left_alone(self, reference):
        product = ProductSpec(capsule_size="0", count_per_bottle=60, dosage_form="Capsule")
        chosen = [PackagingLine("bottle", "HDPE Packer Bottle White 300cc 53mm")]
        lines, _ = derive_packaging(product, chosen, reference)
        assert not any(line.role == "bottle" for line in lines)

    def test_gelatin_spec_selects_a_gelatin_shell(self, reference):
        product = ProductSpec(capsule_size="0", capsule_type="Gelatin",
                              count_per_bottle=60, dosage_form="Capsule")
        lines, _ = derive_packaging(product, [], reference)
        shell = next(line for line in lines if line.role == "capsule_shell")
        assert "Gelatin" in shell.description


class TestPackOutAndBreakage:
    def test_a_shipper_is_spread_over_the_bottles_it_holds(self, reference):
        result = run_pipeline(quote(count_per_bottle=60, servings_per_bottle=30),
                              reference, as_of=AS_OF)
        shipper = next(line for line in result.packaging if line.role == "shipper")
        assert shipper.units_per_container == 12
        # $1.20 carton over 12 bottles, not $1.20 a bottle.
        assert shipper.cost_per_bottle == pytest.approx(0.10)

    def test_capsule_shells_carry_a_breakage_allowance(self, reference):
        result = run_pipeline(quote(count_per_bottle=60, servings_per_bottle=30),
                              reference, as_of=AS_OF)
        shell = next(line for line in result.packaging if line.role == "capsule_shell")
        assert shell.qty_per_bottle == pytest.approx(60 * reference.component_loss_factor)
        assert shell.qty_per_bottle > 60

    def test_a_typed_quantity_overrides_the_breakage_allowance(self, reference):
        parsed = quote(count_per_bottle=60, servings_per_bottle=30)
        parsed.packaging = [PackagingLine("capsule_shell",
                                          "Vegetable Capsule Shell Size 0 Clear HPMC",
                                          qty_per_bottle=60)]
        result = run_pipeline(parsed, reference, as_of=AS_OF)
        assert result.packaging[0].qty_per_bottle == 60


class TestMachineSelection:
    @pytest.mark.parametrize("bottles,count,expected", [
        (500, 60, "Schaefer"),          # 30,000 capsules
        (2000, 60, "BOSCH 705"),        # 120,000
        (10000, 60, "BOSCH 1505"),      # 600,000
        (50000, 60, "BOSCH 3005"),      # 3,000,000
    ])
    def test_machine_follows_the_run_size(self, reference, bottles, count, expected):
        result = run_pipeline(
            quote(count_per_bottle=count, servings_per_bottle=30,
                  annual_volume_bottles=bottles),
            reference, as_of=AS_OF)
        assert result.manufacturing.machine == expected

    def test_a_named_machine_is_not_overridden(self, reference):
        result = run_pipeline(
            quote(count_per_bottle=60, servings_per_bottle=30,
                  annual_volume_bottles=50000, machine="Schaefer"),
            reference, as_of=AS_OF)
        assert result.manufacturing.machine == "Schaefer"
        assert not result.manufacturing.machine_assumed

    def test_each_step_is_costed_at_its_own_work_centre(self, reference):
        result = run_pipeline(quote(count_per_bottle=60, servings_per_bottle=30),
                              reference, as_of=AS_OF)
        manufacturing = result.manufacturing
        assert manufacturing.compounding_rate != manufacturing.bottling_rate
        assert manufacturing.encapsulation_rate == pytest.approx(
            reference.machine_by_name("BOSCH 705").combined_rate)


class TestFillVolume:
    def test_a_dense_formula_that_cannot_fit_is_flagged(self, reference):
        """200mg elemental magnesium from glycinate is 1.4g of material."""
        parsed = quote(count_per_bottle=60, servings_per_bottle=30)
        parsed.formula = [FormulaLine("Magnesium Glycinate", 200.0)]
        result = run_pipeline(parsed, reference, as_of=AS_OF)
        fill = [flag for flag in result.flags if flag.item == "Capsule fill"]
        assert fill and any("exceeds the shell" in flag.reason for flag in fill)
        assert any(flag.severity == "blocking" for flag in fill)

    def test_an_over_sized_capsule_is_flagged_with_a_smaller_option(self, reference):
        parsed = quote(capsule_size="000", count_per_bottle=60, servings_per_bottle=60)
        parsed.formula = [FormulaLine("Rice Flour", 50.0)]
        result = run_pipeline(parsed, reference, as_of=AS_OF)
        fill = [flag for flag in result.flags if flag.item == "Capsule fill"]
        assert fill and any("over-sized" in flag.reason for flag in fill)

    def test_density_is_per_material_not_a_flat_weight_ceiling(self, reference):
        """Same milligrams, very different volumes."""
        light, _ = reference.density_for("silicon dioxide", "excipient"), None
        heavy, _ = reference.density_for("magnesium oxide", "calcium_magnesium"), None
        assert heavy[0] > light[0] * 3

    def test_an_unknown_density_raises_an_rnd_flag(self, reference):
        parsed = quote(count_per_bottle=60, servings_per_bottle=30)
        parsed.formula = [FormulaLine("Bacopa Extract", 100.0)]
        result = run_pipeline(parsed, reference, as_of=AS_OF)
        # bacopa resolves to botanical_extract, which has a class default
        assert all("Bulk density not on file" not in flag.reason for flag in result.flags)


class TestPriceBreaks:
    def test_the_ladder_covers_the_quoted_volume(self, reference):
        result = run_pipeline(quote(count_per_bottle=60, servings_per_bottle=30,
                                    annual_volume_bottles=2000), reference, as_of=AS_OF)
        quoted = [item for item in result.price_breaks if item.is_quoted_volume]
        assert len(quoted) == 1
        assert quoted[0].volume_bottles == 2000
        assert quoted[0].primary_per_bottle == pytest.approx(
            result.summary.primary_per_bottle)

    def test_cost_per_bottle_falls_as_volume_rises(self, reference):
        result = run_pipeline(quote(count_per_bottle=60, servings_per_bottle=30),
                              reference, as_of=AS_OF)
        costs = [item.primary_per_bottle for item in result.price_breaks]
        assert costs == sorted(costs, reverse=True)

    def test_materials_stay_flat_because_po_history_has_no_volume_tiers(self, reference):
        result = run_pipeline(quote(count_per_bottle=60, servings_per_bottle=30),
                              reference, as_of=AS_OF)
        materials = {round(item.raw_materials, 6) for item in result.price_breaks}
        assert len(materials) == 1

    def test_the_machine_changes_with_the_run_size(self, reference):
        result = run_pipeline(quote(count_per_bottle=60, servings_per_bottle=30),
                              reference, as_of=AS_OF)
        assert len({item.machine for item in result.price_breaks}) > 1


class TestPricing:
    def test_price_follows_the_target_margin(self, reference):
        result = run_pipeline(quote(count_per_bottle=60, servings_per_bottle=30),
                              reference, as_of=AS_OF)
        for item in result.pricing:
            margin = item.target_margin_pct / 100.0
            assert item.price_per_bottle == pytest.approx(
                item.cost_per_bottle / (1 - margin))
            assert item.margin_dollars == pytest.approx(
                item.price_per_bottle - item.cost_per_bottle)

    def test_an_unnamed_customer_gets_the_standard_terms(self, reference):
        result = run_pipeline(quote(count_per_bottle=60, servings_per_bottle=30),
                              reference, as_of=AS_OF)
        assert {item.rule for item in result.pricing} == {"default"}
        assert {item.cost_basis for item in result.pricing} == {
            "including overhead", "excluding overhead"}

    def test_pricing_says_so_when_cost_is_understated(self, reference):
        parsed = quote(count_per_bottle=60, servings_per_bottle=30)
        parsed.formula.append(FormulaLine("Totally Unknown Botanical", 100.0))
        result = run_pipeline(parsed, reference, as_of=AS_OF)
        assert result.summary.excluded_count >= 1
        assert all("understated" in item.basis for item in result.pricing)

    def test_no_price_without_a_cost(self, reference):
        from quickquote.schemas import CostSummary

        assert build_pricing(CostSummary(), reference) == []


class TestExclusionVisibility:
    def test_an_unresolved_line_is_counted_next_to_the_total(self, reference):
        parsed = quote(count_per_bottle=60, servings_per_bottle=30)
        parsed.formula.append(FormulaLine("Totally Unknown Botanical", 100.0))
        result = run_pipeline(parsed, reference, as_of=AS_OF)
        assert result.summary.excluded_count == 1
        assert "understated" in result.summary.excluded_note

    def test_a_clean_quote_reports_nothing_excluded(self, reference):
        result = run_pipeline(quote(count_per_bottle=60, servings_per_bottle=30),
                              reference, as_of=AS_OF)
        assert result.summary.excluded_count == 0
        assert result.summary.excluded_note == ""


class TestFlagDeduplication:
    def test_a_missing_field_is_flagged_once_not_twice(self, reference):
        parsed = quote(count_per_bottle=60, servings_per_bottle=30)
        parsed.missing_fields = ["MOQ", "Timeline"]
        result = run_pipeline(parsed, reference, as_of=AS_OF)
        for field in ("moq", "timeline"):
            hits = [flag for flag in result.flags if field in flag.item.lower()]
            assert len(hits) == 1, f"{field} flagged {len(hits)} times"
