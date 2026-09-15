"""Entry point for document parsing: detect, read, extract, record gaps."""
from __future__ import annotations

from ..schemas import ParsedQuote
from .extract import (
    extract_fields,
    extract_formula,
    extract_packaging,
    missing_fields,
)
from .readers import UnsupportedFormat, read_document

__all__ = ["parse_document", "UnsupportedFormat"]


def parse_document(data: bytes, filename: str) -> ParsedQuote:
    """Read one uploaded quote document into a :class:`ParsedQuote`.

    Fields absent from the document are recorded in ``missing_fields``; the
    parser never fills a blank.
    """
    document = read_document(data, filename)

    product = extract_fields(document)
    formula, formula_notes = extract_formula(document)
    packaging = extract_packaging(document)

    parsed = ParsedQuote(
        product=product,
        formula=formula,
        packaging=packaging,
        missing_fields=missing_fields(product),
        source_name=filename,
        source_format=document.source_format,
        parser_notes=[*document.notes, *formula_notes],
    )

    if not formula:
        parsed.missing_fields.append("Ingredient list")
        parsed.parser_notes.append(
            "No ingredient table was recognised. An ingredient table needs a header "
            "row naming both the ingredient column and the amount column."
        )
    if not packaging:
        parsed.missing_fields.append("Packaging configuration")
        parsed.parser_notes.append("No packaging components were recognised in this document.")

    return parsed
