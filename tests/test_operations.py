"""Operations' September 2026 data: crew, set up, cleaning, batches and lines.

Every number here came from Operations rather than from a placeholder, and
several of them changed answers the engine was already giving. The behaviour
each one drives is asserted here.
"""
import copy
from pathlib import Path

import pytest

from quickquote.engine.manufacturing import estimate_manufacturing
from quickquote.engine.pipeline import run_pipeline
from quickquote.schemas import FormulaLine, ParsedQuote, ProductSpec

from .conftest import AS_OF


def spec(form="Capsule", **kwargs):
    defaults = dict(dosage_form=form, capsule_size="0", capsule_type="Vegetable",
                    count_per_bottle=60, servings_per_bottle=30,
                    annual_volume_bottles=2000)
    defaults.update(kwargs)
    return ProductSpec(**defaults)


def quote(form="Capsule", lines=None, **kwargs):
    return ParsedQuote(
        product=spec(form, **kwargs),
        formula=lines or [FormulaLine("Ashwagandha Powder", 500.0)],
        packaging=[], source_name="test", source_format="manual",
    )


class TestCrewSize:
    """Labor is per operator. Packaging runs four of them."""

    def test_the_rate_multiplies_labor_but_not_overhead(self, reference):
        centre = reference.work_centres["Packaging"]
        assert centre.crew_size == 4
        assert centre.combined_rate == pytest.approx(
            centre.labor_rate_per_hour * 4 + centre.overhead_rate_per_hour)

    def test_a_one_operator_centre_is_unchanged(self, reference):
        centre = reference.work_centres["Tablet press"]
        assert centre.crew_size == 1
        assert centre.combined_rate == pytest.approx(centre.single_operator_rate)

    def test_the_packaging_line_overrides_the_centre_crew(self, reference):
        """PKG1 runs four operators, PKG2&3 five, on the same work centre."""
        four = reference.centre_rate("Packaging", 4)
        five = reference.centre_rate("Packaging", 5)
        labor = reference.work_centres["Packaging"].labor_rate_per_hour
        assert five - four == pytest.approx(labor)

    def test_it_reaches_the_bottling_step(self, reference):
        estimate, _ = estimate_manufacturing(spec(), 7, reference)
        bottling = next(step for step in estimate.steps if step.step == "Bottling")
        assert bottling.crew_size == 4
        assert bottling.rate_per_hour == pytest.approx(reference.centre_rate("Packaging", 4))

    def test_the_specification_mode_keeps_one_operator(self, spec_reference):
        """The published example predates crew size and must still reproduce."""
        estimate, _ = estimate_manufacturing(spec(), 7, spec_reference)
        bottling = next(step for step in estimate.steps if step.step == "Bottling")
        assert bottling.rate_per_hour == pytest.approx(spec_reference.combined_rate)


class TestSetUpTime:
    def test_set_up_is_added_to_the_run(self, reference):
        estimate, _ = estimate_manufacturing(spec(), 7, reference)
        encapsulation = next(step for step in estimate.steps if step.step == "Encapsulation")
        assert encapsulation.setup_hours == pytest.approx(2.0)
        assert encapsulation.hours == pytest.approx(
            encapsulation.run_hours + encapsulation.setup_hours)

    def test_it_is_paid_once_per_batch(self, reference):
        """Two batches means two set ups, not one spread thinner."""
        one = estimate_manufacturing(spec(), 7, reference, blend_kg_per_bottle=0.001)[0]
        many = estimate_manufacturing(spec(), 7, reference, blend_kg_per_bottle=10.0)[0]
        assert one.batches == 1 and many.batches > 1
        step_one = next(s for s in one.steps if s.step == "Compounding")
        step_many = next(s for s in many.steps if s.step == "Compounding")
        assert step_many.setup_hours == pytest.approx(step_one.setup_hours * many.batches)

    def test_a_cleaning_step_carries_no_set_up_of_its_own(self, reference):
        estimate, _ = estimate_manufacturing(spec(), 7, reference)
        cleaning = [step for step in estimate.steps if "cleaning" in step.step.lower()]
        assert cleaning and all(step.setup_hours is None for step in cleaning)

    def test_a_missing_set_up_time_understates_rather_than_blocks(self, reference):
        """Schaefer has no set up time on file, but small runs still quote."""
        estimate, _ = estimate_manufacturing(spec(annual_volume_bottles=500), 7, reference)
        assert estimate.machine == "Schaefer"
        assert estimate.estimated
        encapsulation = next(step for step in estimate.steps if step.step == "Encapsulation")
        assert encapsulation.setup_hours is None
        assert encapsulation.cost_per_bottle is not None


class TestCleaning:
    def test_encapsulation_cleaning_follows_the_machine(self, reference):
        """Two hours on the 705, four on the 1505, six on the 3005."""
        hours = {}
        for volume, machine in ((3000, "BOSCH 705"), (10000, "BOSCH 1505"),
                                (40000, "BOSCH 3005")):
            estimate, _ = estimate_manufacturing(
                spec(annual_volume_bottles=volume), 7, reference)
            assert estimate.machine == machine
            step = next(s for s in estimate.steps if s.step == "Encapsulation cleaning")
            hours[machine] = step.run_hours
        assert hours == {"BOSCH 705": 2.0, "BOSCH 1505": 4.0, "BOSCH 3005": 6.0}

    def test_cleaning_is_paid_once_per_batch(self, reference):
        many = estimate_manufacturing(spec(), 7, reference, blend_kg_per_bottle=10.0)[0]
        step = next(s for s in many.steps if s.step == "Blend cleaning")
        assert step.per_batch
        assert step.run_hours == pytest.approx(0.5 * many.batches)


class TestBatches:
    """Blender capacity is a volume limit, so density decides the weight."""

    def test_a_denser_blend_fits_in_fewer_batches(self, reference):
        loose = reference.batches_for(5000, 0.4)
        dense = reference.batches_for(5000, 0.8)
        assert loose[0] > dense[0]

    def test_a_small_run_uses_a_small_blender(self, reference):
        _, blender, _ = reference.batches_for(50, 0.4)
        assert blender.blender == "300 L"

    def test_a_run_too_large_for_any_blender_is_split(self, reference):
        batches, blender, _ = reference.batches_for(10_000, 0.4)
        assert blender.blender == "8500 L"
        assert batches == 4          # 10,000 kg / 2,720 kg

    def test_the_default_density_is_operations_quoting_assumption(self, reference):
        assert reference.default_blend_density == pytest.approx(0.4)
        assert reference.batches_for(1000)[2] == pytest.approx(0.4)

    def test_per_batch_cost_does_not_amortise_away(self, reference):
        """The flaw a single-batch model hides: at volume, compounding used to
        approach zero per bottle. It cannot, because the batches repeat."""
        small = estimate_manufacturing(
            spec(annual_volume_bottles=2000), 7, reference, blend_kg_per_bottle=0.5)[0]
        large = estimate_manufacturing(
            spec(annual_volume_bottles=2_000_000), 7, reference, blend_kg_per_bottle=0.5)[0]
        assert large.batches > small.batches
        small_cost = next(s for s in small.steps if s.step == "Compounding").cost_per_bottle
        large_cost = next(s for s in large.steps if s.step == "Compounding").cost_per_bottle
        # A thousandfold volume increase, but compounding per bottle settles
        # rather than vanishing.
        assert large_cost > small_cost / 10

    def test_the_blend_density_comes_from_the_formula_when_it_is_known(self, reference):
        result = run_pipeline(quote(), reference, as_of=AS_OF)
        assert not result.manufacturing.blend_density_assumed
        assert result.manufacturing.blend_density_g_ml > 0

    def test_one_unknown_density_falls_back_to_the_assumption(self, reference):
        result = run_pipeline(
            quote(lines=[FormulaLine("Ashwagandha Powder", 500.0),
                         FormulaLine("Wholly Unknown Material", 100.0)]),
            reference, as_of=AS_OF)
        assert result.manufacturing.blend_density_assumed
        assert result.manufacturing.blend_density_g_ml == pytest.approx(0.4)


class TestPackagingLines:
    def test_a_count_between_rows_takes_the_slower(self, reference):
        assert reference.packaging_line_row(75, "PKG1").bottles_per_hour == 1620

    def test_a_count_past_the_last_row_takes_the_last(self, reference):
        assert reference.packaging_line_row(5000, "PKG1").count_per_bottle == 200

    def test_the_assumed_line_is_named_with_what_the_other_would_cost(self, reference):
        result = run_pipeline(quote(), reference, as_of=AS_OF)
        line = [flag for flag in result.flags if flag.item == "Packaging line"]
        assert line, "the assumed line must be visible on the quote"
        assert "PKG1" in line[0].reason and "PKG2&3" in line[0].reason
        assert "operators" in line[0].reason


class TestFormsOperationsUnblocked:
    @pytest.mark.parametrize("form", ["Powder", "Packet"])
    def test_it_can_now_be_quoted(self, reference, form):
        result = run_pipeline(quote(form), reference, as_of=AS_OF)
        assert result.manufacturing.estimated
        assert result.summary.manufacturing > 0

    def test_a_line_rated_in_bottles_fills_bottles_not_capsules(self, reference):
        """The powder line runs 1,200 bottles an hour whatever goes in them."""
        estimate, _ = estimate_manufacturing(
            spec("Powder", count_per_bottle=60, annual_volume_bottles=2000), 7, reference)
        fill = next(step for step in estimate.steps if step.step == "Powder fill")
        assert fill.run_hours == pytest.approx(2000 / 1200)

    def test_a_tablet_is_still_blocked_by_granulation(self, reference):
        """The press rate arrived; the Chilsinator's did not."""
        estimate, _ = estimate_manufacturing(spec("Tablet"), 7, reference)
        assert not estimate.estimated
        assert estimate.uncosted_critical_steps == ["Granulation"]

    def test_a_gummy_is_still_blocked(self, reference):
        """Finance confirmed the gummy cost model does not exist yet."""
        estimate, _ = estimate_manufacturing(spec("Gummy"), 7, reference)
        assert not estimate.estimated


class TestConfirmedRates:
    @pytest.mark.parametrize("centre,speed", [
        ("705", 32000), ("1505", 75000), ("3005", 110000),
        ("Tablet press", 141000), ("Powder Fill", 1200), ("Pakrapid", 3500),
    ])
    def test_operations_speeds_are_on_file_and_marked_confirmed(
        self, reference, centre, speed
    ):
        rate = reference.run_rates[centre]
        assert rate.units_per_hour == speed and rate.confirmed

    @pytest.mark.parametrize("centre", ["Schaefer", "Chilsinator"])
    def test_a_rate_operations_did_not_supply_is_not_marked_confirmed(
        self, reference, centre
    ):
        assert not reference.run_rates[centre].confirmed

    def test_the_encapsulation_bands_match_what_operations_confirmed(self, reference):
        bands = {m.machine: (m.min_capsules, m.max_capsules) for m in reference.machines}
        assert bands["Schaefer"] == (0, 100_000)
        assert bands["BOSCH 705"] == (100_000, 400_000)
        assert bands["BOSCH 1505"] == (400_000, 2_000_000)
        assert bands["BOSCH 3005"] == (2_000_000, None)


class TestCompoundingDisagreement:
    def test_operations_own_timings_are_available(self, reference):
        """Five minutes an ingredient, 45 minutes of blending, 10s per 20 kg."""
        hours = reference.compounding_hours_from_operations(9, 3600)
        assert hours == pytest.approx((5 * 9 + 45) / 60 + (3600 / 20) * 10 / 3600)

    def test_the_gap_against_the_bands_is_flagged_not_resolved(self, reference):
        result = run_pipeline(quote(), reference, as_of=AS_OF)
        gap = [flag for flag in result.flags if flag.item == "Compounding time"]
        assert gap and gap[0].owner == "Operations"
        assert "component-count bands" in gap[0].reason

    def test_the_engine_still_charges_the_bands(self, reference):
        """Flagging a disagreement is not the same as picking a side."""
        estimate, _ = estimate_manufacturing(spec(), 7, reference)
        step = next(s for s in estimate.steps if s.step == "Compounding")
        assert step.run_hours == pytest.approx(reference.compounding_hours(7))


class TestUnknownDensityIsVisible:
    def test_an_undeterminable_class_does_not_borrow_the_default_row(self, reference):
        """"default" names a class that could not be worked out, not a class."""
        known, was_defaulted = reference.density_for("ashwagandha powder", "botanical_powder")
        assert not was_defaulted and known > 0
        _, unknown_defaulted = reference.density_for(None, "default")
        assert unknown_defaulted

    def test_it_reaches_r_and_d_as_a_flag(self, reference):
        result = run_pipeline(
            quote(lines=[FormulaLine("Wholly Unknown Material", 100.0)]),
            reference, as_of=AS_OF)
        assert any("Bulk density not on file" in flag.reason for flag in result.flags)


class TestDatasetProvenance:
    """A quote costed against demonstration tables is complete, confident and
    fiction. Its part codes look exactly like real ones, so it has to say so."""

    def test_the_shipped_tables_declare_themselves_a_demonstration(self, reference):
        assert reference.is_demonstration

    def test_every_quote_on_them_carries_a_blocking_flag(self, reference):
        result = run_pipeline(quote(), reference, as_of=AS_OF)
        notice = [flag for flag in result.flags if flag.item == "Reference data"]
        assert notice and notice[0].severity == "blocking"
        assert "NOT FOR QUOTING" in notice[0].reason
        assert result.dataset_is_demonstration

    def test_operational_tables_raise_no_such_flag(self, reference):
        catalogue = copy.deepcopy(reference)
        catalogue.rates = {**catalogue.rates, "dataset_is_demonstration": "0",
                           "dataset_label": "DrVita operational data loaded 2026-09-24"}
        result = run_pipeline(quote(), catalogue, as_of=AS_OF)
        assert not result.dataset_is_demonstration
        assert not [flag for flag in result.flags if flag.item == "Reference data"]
        assert result.dataset_label.startswith("DrVita operational")

    def test_the_loader_script_clears_the_marker(self):
        """Otherwise seeding the real directory would stamp it a demonstration."""
        script = (Path(__file__).resolve().parents[1]
                  / "scripts" / "load_real_data.sh").read_text()
        assert "dataset_is_demonstration" in script
        assert script.index("cp backend/quickquote/reference/data/*.csv") < \
               script.index("dataset_is_demonstration")


class TestUnconfirmedMargins:
    def test_a_placeholder_margin_is_flagged_to_finance(self, reference):
        result = run_pipeline(quote(), reference, as_of=AS_OF)
        margin = [flag for flag in result.flags if flag.item == "Margin targets"]
        assert margin and margin[0].owner == "Finance"
        assert margin[0].severity == "blocking"

    def test_a_confirmed_margin_raises_nothing(self, reference):
        catalogue = copy.deepcopy(reference)
        catalogue.pricing = [
            type(target)(**{**target.__dict__, "notes": "Confirmed by Finance 2026-09"})
            for target in catalogue.pricing
        ]
        result = run_pipeline(quote(), catalogue, as_of=AS_OF)
        assert not [flag for flag in result.flags if flag.item == "Margin targets"]

    def test_the_price_is_still_offered_with_the_caveat(self, reference):
        """Flagging it is not the same as withholding it."""
        result = run_pipeline(quote(), reference, as_of=AS_OF)
        assert result.pricing and all(item.price_per_bottle > 0 for item in result.pricing)


class TestDescriptionEncoding:
    """Global Shop exports cp1252 text that arrives decoded as cp437."""

    @pytest.mark.parametrize("broken,fixed", [
        ("KSM-66« ASHWAGANDHA EXTRACT", "KSM-66® ASHWAGANDHA EXTRACT"),
        ("3.25 x 3.5 WOMENÆS WHITE FILM", "3.25 x 3.5 WOMEN’S WHITE FILM"),
        ("5-HTP, 98% (HTPurityÖ)", "5-HTP, 98% (HTPurity™)"),
        ("32oz WHITE HDPE ôLIPö", "32oz WHITE HDPE “LIP”"),
    ])
    def test_it_is_repaired(self, broken, fixed):
        from quickquote.reference.ingest import repair_mojibake

        assert repair_mojibake(broken) == fixed

    @pytest.mark.parametrize("text", [
        "Plain ASCII Description", "Café Latte Flavour", "Açaí Berry Extract",
        "PURÉE BASE", "Jalapeño Powder",
    ])
    def test_a_real_accent_is_left_alone(self, text):
        """The repair must not mangle a description that was always correct."""
        from quickquote.reference.ingest import repair_mojibake

        assert repair_mojibake(text) == text
