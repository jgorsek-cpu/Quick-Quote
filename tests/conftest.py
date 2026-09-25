from datetime import date

import csv
import shutil

import pytest

from quickquote.reference.loader import load_reference_data
from quickquote.schemas import FormulaLine, PackagingLine, ParsedQuote, ProductSpec

AS_OF = date(2026, 9, 15)


@pytest.fixture(scope="session")
def reference():
    """The tables that ship with the repo.

    Loaded by path rather than through get_reference_data(), which prefers
    var/reference once the operational tables are built. The suite has to
    assert the same thing on every machine, whether or not someone has
    loaded DrVita's data on it.
    """
    from quickquote.config import PACKAGE_ROOT

    return load_reference_data(PACKAGE_ROOT / "reference" / "data")


@pytest.fixture(scope="session")
def shipped_reference(reference):
    """An alias that reads clearly in tests about the demonstration data."""
    return reference


@pytest.fixture(scope="session")
def spec_reference(tmp_path_factory, reference):
    """Reference data configured the way the written specification assumes.

    The shipped defaults cost each production step at its own work centre's
    rate, add a breakage allowance to components, take bottling speed from the
    operational CVC run-rate table, and carry an analytical testing cost. The specification's worked example
    predates all three: one blended labor/OH pair, exact component counts, and
    a single flat bottling rate.

    This fixture restores those assumptions so the example can still be
    asserted to the cent, which makes it a regression test of the cost maths
    rather than of the current rate policy. The real bottling table is roughly
    five times faster than the flat rate the example implies, so the two
    genuinely cannot both hold.
    """
    source = reference.source_dir
    target = tmp_path_factory.mktemp("spec_reference")
    for path in source.glob("*.csv"):
        shutil.copy(path, target / path.name)

    # The example assumes one flat bottling speed, so the real speed tables are
    # withheld and the engine falls back to that rate. It also has no separate
    # testing line -- its breakdown is materials, packaging and manufacturing
    # only -- while the operational price sheet does carry one. Blender
    # capacity is withheld too: the example is a single batch by assumption.
    (target / "bottling_rates.csv").unlink(missing_ok=True)
    (target / "packaging_lines.csv").unlink(missing_ok=True)
    (target / "testing_costs.csv").unlink(missing_ok=True)
    (target / "blenders.csv").unlink(missing_ok=True)

    # The example also predates the speeds Operations confirmed in 2026-09 --
    # it implies 16,870 capsules an hour on the 705 where the real machine
    # runs 32,000 -- and has no set up or cleaning time at all. Those are
    # restored here so the example still reproduces to the cent.
    machines_path = target / "machines.csv"
    machines = list(csv.DictReader(machines_path.open()))
    spec_speeds = {"BOSCH 705": 16870, "BOSCH 1505": 36000, "BOSCH 3005": 72000}
    for row in machines:
        row["capsules_per_hour"] = spec_speeds.get(row["machine"], row["capsules_per_hour"])
        row["setup_hours"] = ""
        row["cleaning_hours"] = ""
    with machines_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(machines[0]))
        writer.writeheader()
        writer.writerows(machines)

    for name, keep in (("work_centres.csv", ("crew_size", "setup_hours")),
                       ("cleaning_hours.csv", ("hours_per_batch",))):
        path = target / name
        rows = list(csv.DictReader(path.open()))
        for row in rows:
            for column in keep:
                row[column] = ""
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    rates_path = target / "labor_rates.csv"
    rows = list(csv.DictReader(rates_path.open()))
    overrides = {"use_work_centre_rates": "0", "component_loss_factor": "1.0"}
    for row in rows:
        if row["key"] in overrides:
            row["value"] = overrides.pop(row["key"])
    with rates_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["key", "value", "unit", "notes"])
        writer.writeheader()
        writer.writerows(rows)

    return load_reference_data(target)


@pytest.fixture
def heart_health() -> ParsedQuote:
    """The worked example from the system specification."""
    return ParsedQuote(
        product=ProductSpec(
            customer="Vitamin Shoppe",
            formula_name="Bioactive Heart Health 90ct",
            dosage_form="Capsule",
            capsule_size="00",
            capsule_type="Vegetable",
            servings_per_bottle=30,
            count_per_bottle=90,
            annual_volume_bottles=1625,
        ),
        formula=[
            FormulaLine("Mega Natural-BP Grape Extract", 300.0),
            FormulaLine("Coenzyme Q10", 100.0),
            FormulaLine("Aged Garlic Extract", 200.0),
            FormulaLine("Red Yeast Rice", 300.0),
            FormulaLine("Rice Flour", 50.0),
            FormulaLine("Magnesium Stearate", 15.0),
            FormulaLine("Black Pepper Extract", 5.0),
        ],
        packaging=[
            PackagingLine("capsule_shell", "Vegetable Capsule Shell"),
            PackagingLine("bottle", "HDPE Bottle White 175cc"),
            PackagingLine("cap", "Child-Resistant Cap White 45mm"),
            PackagingLine("label", "Pressure Sensitive Body Label"),
            PackagingLine("neckband", "Shrink Neckband"),
            PackagingLine("cotton", "Cotton Coil"),
            PackagingLine("desiccant", "Desiccant Canister"),
            PackagingLine("shipper", "Corrugated Shipper", qty_per_bottle=0.00833),
        ],
        source_name="spec example",
        source_format="manual",
    )
