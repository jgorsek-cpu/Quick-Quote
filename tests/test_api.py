"""HTTP surface."""
import pytest
from fastapi.testclient import TestClient

from quickquote.api.app import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


QUOTE = {
    "product": {
        "customer": "Test Co", "formula_name": "T1", "dosage_form": "Capsule",
        "capsule_size": "0", "capsule_type": "Vegetable", "servings_per_bottle": 30,
        "count_per_bottle": 60, "annual_volume_bottles": 5000,
    },
    "formula": [{"name": "Coenzyme Q10", "claimed_mg": 100},
                {"name": "L-Theanine", "claimed_mg": 200}],
    "packaging": [{"role": "capsule_shell", "description": "Vegetable Capsule Shell"},
                  {"role": "bottle", "description": "HDPE Bottle White 175cc"}],
}


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["reference_rows"] > 0


def test_reference_catalogs_are_served(client):
    assert client.get("/api/reference/ingredients").json()
    assert client.get("/api/reference/packaging").json()
    assert client.get("/api/reference/overview").json()["rates"]["labor_rate_per_hour"] > 0


def test_quote_carries_price_breaks_and_pricing(client):
    body = client.post("/api/quotes", json=QUOTE).json()
    assert body["price_breaks"]
    assert {item["channel"] for item in body["pricing"]} == {"contract", "b2c"}
    assert body["product"]["derived"]


def test_create_quote_returns_a_full_package(client):
    body = client.post("/api/quotes", json=QUOTE).json()
    assert body["quote_id"]
    assert body["summary"]["primary_per_bottle"] > 0
    assert len(body["ingredients"]) == 2
    assert set(body["flags_by_owner"]) == {"Purchasing", "R&D", "Operations", "Sales", "Finance"}


def test_preview_does_not_store_a_quote(client):
    before = len(client.get("/api/quotes").json())
    client.post("/api/quotes/preview", json=QUOTE)
    assert len(client.get("/api/quotes").json()) == before


class TestLivePreviewParity:
    """The live estimate and the generated package are one cost path.

    The interface shows the preview figure while a rep edits and the package
    figure once they generate. If those could ever disagree for the same
    input, the number a rep quotes from would not be the number in the
    workbook.
    """

    @pytest.mark.parametrize("body", [
        QUOTE,
        {**QUOTE, "product": {**QUOTE["product"], "annual_volume_bottles": 50000}},
        {**QUOTE, "product": {**QUOTE["product"], "annual_volume_bottles": None}},
        {**QUOTE, "packaging": []},                       # engine derives the pack-out
        {**QUOTE, "formula": [{"name": "No Such Ingredient", "claimed_mg": 100}]},
    ])
    def test_every_total_matches(self, client, body):
        preview = client.post("/api/quotes/preview", json=body).json()["summary"]
        created = client.post("/api/quotes", json=body).json()["summary"]
        for key in ("primary_per_bottle", "raw_materials", "packaging",
                    "manufacturing", "low_per_bottle", "high_per_bottle",
                    "excluded_count", "quote_confidence"):
            assert preview[key] == created[key], f"{key} differs between preview and package"

    def test_manufacturing_breakdown_matches(self, client):
        preview = client.post("/api/quotes/preview", json=QUOTE).json()["manufacturing"]
        created = client.post("/api/quotes", json=QUOTE).json()["manufacturing"]
        for key in ("compounding_per_bottle", "encapsulation_per_bottle",
                    "bottling_per_bottle", "total_per_bottle", "machine"):
            assert preview[key] == created[key], f"{key} differs"

    def test_the_total_is_the_sum_of_its_parts(self, client):
        """Guards the specific failure of a step being dropped from one path."""
        summary = client.post("/api/quotes/preview", json=QUOTE).json()
        parts = summary["summary"]
        manufacturing = summary["manufacturing"]
        assert parts["primary_per_bottle"] == pytest.approx(
            parts["raw_materials"] + parts["packaging"] + parts["manufacturing"])
        assert parts["manufacturing"] == pytest.approx(
            (manufacturing["compounding_per_bottle"] or 0)
            + (manufacturing["encapsulation_per_bottle"] or 0)
            + (manufacturing["bottling_per_bottle"] or 0))

    def test_the_quoted_total_sits_inside_its_own_range(self, client):
        parts = client.post("/api/quotes/preview", json=QUOTE).json()["summary"]
        assert parts["low_per_bottle"] <= parts["primary_per_bottle"] <= parts["high_per_bottle"]


def test_artifacts_download(client):
    quote_id = client.post("/api/quotes", json=QUOTE).json()["quote_id"]
    workbook = client.get(f"/api/quotes/{quote_id}/workbook.xlsx")
    assert workbook.status_code == 200
    assert workbook.content[:2] == b"PK"
    pdf = client.get(f"/api/quotes/{quote_id}/quote.pdf")
    assert pdf.content.startswith(b"%PDF-")


def test_upload_parses_and_costs(client):
    from openpyxl import Workbook
    import io

    book = Workbook()
    sheet = book.active
    for row in [["Customer", "Widget Co"], ["Servings Per Bottle", 30],
                ["Annual Volume", 1000], [],
                ["Ingredient", "Part Code", "Amount Per Serving", "UOM"],
                ["Coenzyme Q10", "", 100, "mg"]]:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)

    response = client.post("/api/quotes/upload",
                           files={"file": ("q.xlsx", buffer.getvalue())})
    assert response.status_code == 200
    body = response.json()
    assert body["product"]["customer"] == "Widget Co"
    assert body["summary"]["primary_per_bottle"] > 0


class TestErrors:
    def test_unsupported_format_is_rejected(self, client):
        response = client.post("/api/quotes/upload", files={"file": ("x.jpg", b"data")})
        assert response.status_code == 415

    def test_empty_file_is_rejected(self, client):
        response = client.post("/api/quotes/upload", files={"file": ("x.txt", b"")})
        assert response.status_code == 400

    def test_a_quote_with_no_lines_is_rejected(self, client):
        response = client.post("/api/quotes", json={"formula": [], "packaging": []})
        assert response.status_code == 400

    def test_unknown_quote_is_404(self, client):
        assert client.get("/api/quotes/nope").status_code == 404
