"""HTTP routes."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from ..config import MAX_UPLOAD_BYTES
from ..engine.pipeline import run_pipeline
from ..output.pdf import build_quote_pdf
from ..output.workbook import build_workbook
from ..parsing.router import UnsupportedFormat, parse_document
from ..reference.catalog import ingredient_catalog, packaging_catalog, reference_overview
from ..reference.loader import get_reference_data
from ..schemas import FormulaLine, PackagingLine, ParsedQuote, ProductSpec, QuoteResult
from ..store import get_store
from .models import QuoteIn

router = APIRouter(prefix="/api")

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/health")
def health() -> dict:
    reference = get_reference_data()
    return {
        "status": "ok",
        "reference_rows": len(reference.po_rows),
        "identities": len(reference.ingredient_identities) + len(reference.packaging_identities),
    }


# ------------------------------------------------------------ reference

@router.get("/reference/overview")
def reference_summary() -> dict:
    return reference_overview(get_reference_data())


@router.get("/reference/ingredients")
def reference_ingredients() -> list[dict]:
    return ingredient_catalog(get_reference_data())


@router.get("/reference/packaging")
def reference_packaging() -> list[dict]:
    return packaging_catalog(get_reference_data())


@router.post("/reference/reload")
def reference_reload() -> dict:
    reference = get_reference_data(refresh=True)
    return {"status": "reloaded", "po_rows": len(reference.po_rows)}


# --------------------------------------------------------------- quotes

def _finalise(parsed: ParsedQuote) -> QuoteResult:
    """Run the pipeline, store the result and persist its artifacts."""
    result = run_pipeline(parsed, get_reference_data(), as_of=date.today())
    store = get_store()
    store.add(result)
    store.write_artifact(result.quote_id, ".xlsx", build_workbook(result))
    store.write_artifact(result.quote_id, ".pdf", build_quote_pdf(result))
    return result


@router.post("/quotes/upload")
async def upload_quote(file: UploadFile = File(...)) -> dict:
    """Parse an uploaded quote document and produce a quote package."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.",
        )

    try:
        parsed = parse_document(data, file.filename or "upload")
    except UnsupportedFormat as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001 - surfaced to the user verbatim
        raise HTTPException(
            status_code=422,
            detail=f"The document could not be read: {error}",
        ) from error

    return _finalise(parsed).to_dict()


@router.post("/quotes/parse-only")
async def parse_only(file: UploadFile = File(...)) -> dict:
    """Read a document and return what was found, without costing it.

    Lets a rep confirm the parse and fill gaps before a quote is generated.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    try:
        parsed = parse_document(data, file.filename or "upload")
    except UnsupportedFormat as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"The document could not be read: {error}") from error
    return parsed.to_dict()


def _parsed_from_payload(payload: QuoteIn) -> ParsedQuote:
    """Turn a builder payload into the same ParsedQuote a document produces."""
    from ..parsing.extract import missing_fields

    product = ProductSpec(**payload.product.model_dump())
    parsed = ParsedQuote(
        product=product,
        formula=[
            FormulaLine(name=line.name, claimed_mg=line.claimed_mg, part_code=line.part_code)
            for line in payload.formula
        ],
        packaging=[
            PackagingLine(
                role=line.role,
                description=line.description,
                qty_per_bottle=line.qty_per_bottle,
                part_code=line.part_code,
                listed_unit_cost=line.listed_unit_cost,
            )
            for line in payload.packaging
        ],
        source_name="Sales rep interface",
        source_format="manual",
    )
    parsed.missing_fields = missing_fields(product)
    return parsed


@router.post("/quotes/preview")
def preview_quote(payload: QuoteIn) -> dict:
    """Cost a quote without storing it or writing any artifact.

    Backs the live figures in the builder. It runs the identical pipeline, so
    what a rep sees while editing is what the generated package will say.
    """
    if not payload.formula and not payload.packaging:
        raise HTTPException(
            status_code=400,
            detail="A quote needs at least one ingredient or packaging component.",
        )
    result = run_pipeline(
        _parsed_from_payload(payload), get_reference_data(), as_of=date.today()
    )
    return result.to_dict()


@router.post("/quotes")
def create_quote(payload: QuoteIn) -> dict:
    """Produce a quote package from a quote built in the interface."""
    if not payload.formula and not payload.packaging:
        raise HTTPException(
            status_code=400,
            detail="A quote needs at least one ingredient or packaging component.",
        )
    return _finalise(_parsed_from_payload(payload)).to_dict()


@router.get("/quotes")
def list_quotes(limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    return get_store().list(limit=limit)


@router.get("/quotes/{quote_id}")
def get_quote(quote_id: str) -> dict:
    result = get_store().get(quote_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Quote '{quote_id}' was not found.")
    return result.to_dict()


def _download(quote_id: str, suffix: str, media_type: str, filename: str) -> Response:
    store = get_store()
    data = store.read_artifact(quote_id, suffix)
    if data is None:
        result = store.get(quote_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Quote '{quote_id}' was not found.")
        data = build_workbook(result) if suffix == ".xlsx" else build_quote_pdf(result)
        store.write_artifact(quote_id, suffix, data)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/quotes/{quote_id}/workbook.xlsx")
def download_workbook(quote_id: str) -> Response:
    return _download(quote_id, ".xlsx", XLSX_MEDIA, f"quick-quote-{quote_id}.xlsx")


@router.get("/quotes/{quote_id}/quote.pdf")
def download_pdf(quote_id: str) -> Response:
    return _download(quote_id, ".pdf", "application/pdf", f"quick-quote-{quote_id}.pdf")
