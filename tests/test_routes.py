"""Process routes: one per dosage form, costed step by step."""
import pytest

from quickquote.engine.manufacturing import (
    estimate_manufacturing,
    testing_cost_per_bottle as compute_testing_cost,
)
from quickquote.engine.pipeline import run_pipeline
from quickquote.schemas import FormulaLine, ParsedQuote, ProductSpec

from .conftest import AS_OF


def spec(form, **kwargs):
    defaults = dict(dosage_form=form, capsule_size="0", capsule_type="Vegetable",
                    count_per_bottle=60, annual_volume_bottles=2000)
    defaults.update(kwargs)
    return ProductSpec(**defaults)


def quote(form, **kwargs):
    return ParsedQuote(
        product=spec(form, **kwargs),
        formula=[FormulaLine("Ashwagandha Powder", 300.0)],
        packaging=[], source_name="test", source_format="manual",
    )


class TestRouteCoverage:
    def test_every_line_has_a_route(self, reference):
        assert set(reference.known_dosage_forms()) >= {
            "capsule", "tablet", "powder", "packet", "softgel", "gummy"
        }

    def test_each_route_is_ordered(self, reference):
        for form in reference.known_dosage_forms():
            steps = reference.route_for(form)
            assert [step.sequence for step in steps] == sorted(step.sequence for step in steps)

    def test_a_named_variant_still_finds_its_route(self, reference):
        """"Chewable Tablet" is a tablet; the match is on the form's name."""
        estimate, _ = estimate_manufacturing(spec("Chewable Tablet"), 7, reference)
        assert [step.step for step in estimate.steps][:1] == ["Compounding"]

    def test_an_unknown_form_is_refused_not_guessed(self, reference):
        estimate, _ = estimate_manufacturing(spec("Liquid Syrup"), 7, reference)
        assert not estimate.estimated
        assert "No process route on file" in estimate.reason

    def test_every_work_centre_named_by_a_route_has_a_rate(self, reference):
        """A typo in the route table would otherwise show up as a missing rate."""
        for form in reference.known_dosage_forms():
            for step in reference.route_for(form):
                if step.work_centre:
                    assert step.work_centre in reference.work_centres, (
                        f"{form}/{step.step} names an unknown work centre"
                    )


class TestCapsuleRoute:
    def test_it_costs_end_to_end(self, reference):
        estimate, _ = estimate_manufacturing(spec("Capsule"), 7, reference)
        assert estimate.estimated
        assert estimate.total_per_bottle > 0
        named = [step.step for step in estimate.steps if step.cost_per_bottle is not None]
        assert "Compounding" in named and "Encapsulation" in named and "Bottling" in named

    def test_each_step_uses_its_own_work_centre_rate(self, reference):
        estimate, _ = estimate_manufacturing(spec("Capsule"), 7, reference)
        rates = {step.step: step.rate_per_hour for step in estimate.steps
                 if step.rate_per_hour}
        assert rates["Compounding"] == pytest.approx(
            reference.centre_rate("Blend"))
        assert rates["Bottling"] == pytest.approx(
            reference.centre_rate("Packaging"))
        assert rates["Compounding"] != rates["Bottling"]


class TestUncostableSteps:
    @pytest.mark.parametrize("form,missing", [
        # Operations supplied the Fette press rate, so what still stops a
        # tablet is granulation; softgel encapsulation has no machine at all.
        ("Tablet", "Granulation"),
        ("Softgel", "Encapsulation"),
    ])
    def test_a_line_with_no_run_rate_is_not_estimated(self, reference, form, missing):
        """A tablet must not come back priced as though granulating were free."""
        estimate, _ = estimate_manufacturing(spec(form), 7, reference)
        assert not estimate.estimated
        assert missing in estimate.uncosted_critical_steps

    @pytest.mark.parametrize("form", ["Powder", "Packet"])
    def test_a_line_operations_have_rated_is_quotable(self, reference, form):
        estimate, _ = estimate_manufacturing(spec(form), 7, reference)
        assert estimate.estimated
        assert not estimate.uncosted_critical_steps
        assert estimate.total_per_bottle > 0

    def test_gummies_have_no_work_centre_at_all(self, reference):
        estimate, _ = estimate_manufacturing(spec("Gummy"), 7, reference)
        assert not estimate.estimated
        assert {"Compounding", "Depositing", "Curing"} <= set(estimate.uncosted_critical_steps)
        assert all("Operations to supply" in step.reason
                   for step in estimate.steps if step.cost_per_bottle is None)

    def test_an_uncostable_line_contributes_nothing_to_the_total(self, reference):
        result = run_pipeline(quote("Gummy"), reference, as_of=AS_OF)
        assert result.summary.manufacturing == 0.0

    def test_cleaning_is_costed_now_that_operations_have_supplied_it(self, reference):
        estimate, _ = estimate_manufacturing(spec("Capsule"), 7, reference)
        assert estimate.estimated
        cleaning = [step for step in estimate.steps if "cleaning" in step.step.lower()]
        assert cleaning and all(step.cost_per_bottle is not None for step in cleaning)
        assert not estimate.uncosted_steps

    def test_cleaning_stays_ancillary_when_a_figure_goes_missing(self, reference):
        """Missing cleaning hours must not invalidate an otherwise sound estimate."""
        import copy

        catalogue = copy.deepcopy(reference)
        catalogue.cleaning_hours = {}
        catalogue.machines = [
            type(machine)(**{**machine.__dict__, "cleaning_hours": None})
            for machine in catalogue.machines
        ]
        estimate, _ = estimate_manufacturing(spec("Capsule"), 7, catalogue)
        assert estimate.estimated
        assert any("cleaning" in step.lower() for step in estimate.uncosted_steps)
        assert not estimate.uncosted_critical_steps

    def test_each_missing_rate_is_named_for_operations(self, reference):
        result = run_pipeline(quote("Tablet"), reference, as_of=AS_OF)
        items = [flag.item for flag in result.flags if flag.owner == "Operations"]
        assert any("Granulation" in item for item in items)
        assert any(flag.severity == "blocking" for flag in result.flags
                   if flag.owner == "Operations")


class TestMachineSelection:
    def test_a_capsule_run_selects_an_encapsulator(self, reference):
        estimate, _ = estimate_manufacturing(spec("Capsule"), 7, reference)
        assert estimate.machine == "BOSCH 705"

    def test_a_tablet_run_selects_no_encapsulator(self, reference):
        """The machine table holds hard-capsule encapsulators only."""
        estimate, _ = estimate_manufacturing(spec("Tablet"), 7, reference)
        assert estimate.machine == ""

    def test_a_softgel_run_selects_no_hard_capsule_machine(self, reference):
        estimate, _ = estimate_manufacturing(spec("Softgel"), 7, reference)
        assert estimate.machine == ""


class TestBottlingSpeed:
    def test_speed_comes_from_the_run_rate_table(self, reference):
        assert reference.bottles_per_hour(60, "00") == pytest.approx(1890)

    def test_a_higher_count_runs_slower(self, reference):
        fast = reference.bottles_per_hour(60, "0")
        slow = reference.bottles_per_hour(500, "0")
        assert slow < fast

    def test_capsule_size_changes_the_speed(self, reference):
        small = reference.bottles_per_hour(300, "3")
        large = reference.bottles_per_hour(300, "0EL")
        assert small != large

    def test_an_unknown_count_falls_to_the_slowest_row(self, reference):
        assert reference.bottles_per_hour(99999, "0") == pytest.approx(
            reference.bottles_per_hour(1200, "0"))

    def test_bottling_cost_uses_the_packaging_line_rate(self, reference):
        """Operations' own line rate, plus the hour of set up it takes."""
        estimate, _ = estimate_manufacturing(spec("Capsule", count_per_bottle=60), 7, reference)
        bottling = next(step for step in estimate.steps if step.step == "Bottling")
        line = reference.packaging_line_row(60)
        assert bottling.run_hours == pytest.approx(2000 / line.bottles_per_hour)
        assert bottling.setup_hours == pytest.approx(line.setup_hours)
        assert bottling.hours == pytest.approx(bottling.run_hours + bottling.setup_hours)


class TestTestingCost:
    @pytest.mark.parametrize("count,batch,per_unit", [
        (2, 700, 0.35), (4, 900, 0.45), (9, 1100, 0.55), (30, 1100, 0.55),
    ])
    def test_bands_follow_the_price_sheet(self, reference, count, batch, per_unit):
        cost, basis = compute_testing_cost(reference, count, 1000)
        assert cost == pytest.approx(per_unit + batch / 1000)
        assert f"${batch:,.0f}" in basis

    def test_it_reaches_the_quote_total(self, reference):
        result = run_pipeline(quote("Capsule"), reference, as_of=AS_OF)
        assert result.summary.testing > 0
        assert result.summary.primary_per_bottle == pytest.approx(
            result.summary.raw_materials + result.summary.packaging
            + result.summary.manufacturing + result.summary.testing)

    def test_no_volume_means_no_testing_cost(self, reference):
        assert compute_testing_cost(reference, 5, None) == (None, "")
