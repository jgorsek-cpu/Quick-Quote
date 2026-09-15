from datetime import date

import pytest

from quickquote.reference.loader import get_reference_data
from quickquote.schemas import FormulaLine, PackagingLine, ParsedQuote, ProductSpec

AS_OF = date(2026, 9, 15)


@pytest.fixture(scope="session")
def reference():
    return get_reference_data()


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
