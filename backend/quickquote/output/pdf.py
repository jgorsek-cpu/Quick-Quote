"""Customer-facing quote summary as a PDF.

Marked Internal Draft until Sales and Finance review. Only Accepted lines
appear in the cost; exposure is called out prominently.
"""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..config import FLAG_OWNERS
from ..schemas import QuoteResult

NAVY = colors.HexColor("#1F3864")
LIGHT = colors.HexColor("#D9E2F3")
RED = colors.HexColor("#C00000")
RED_BG = colors.HexColor("#FCE4E4")
GREY = colors.HexColor("#595959")

_CONFIDENCE_COLOR = {
    "High": colors.HexColor("#2E7D32"),
    "Medium": colors.HexColor("#B26A00"),
    "Low": RED,
}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("qq_title", parent=base["Title"], fontSize=20,
                                textColor=NAVY, alignment=TA_LEFT, spaceAfter=2),
        "sub": ParagraphStyle("qq_sub", parent=base["Normal"], fontSize=10,
                              textColor=GREY, spaceAfter=10),
        "h2": ParagraphStyle("qq_h2", parent=base["Heading2"], fontSize=12,
                             textColor=NAVY, spaceBefore=12, spaceAfter=6),
        "body": ParagraphStyle("qq_body", parent=base["Normal"], fontSize=9, leading=12),
        "small": ParagraphStyle("qq_small", parent=base["Normal"], fontSize=8,
                                textColor=GREY, leading=10),
        "banner": ParagraphStyle("qq_banner", parent=base["Normal"], fontSize=10,
                                 textColor=colors.white, leading=14),
        "exposure": ParagraphStyle("qq_exposure", parent=base["Normal"], fontSize=9,
                                   textColor=RED, leading=12),
    }


def _kv_table(rows: list[tuple[str, str]], widths=(1.9 * inch, 4.6 * inch)) -> Table:
    table = Table([[label, value] for label, value in rows], colWidths=list(widths))
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#DDDDDD")),
    ]))
    return table


def _grid(header: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    table = Table([header] + rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BFBFBF")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _banner(text: str, background, style) -> Table:
    table = Table([[Paragraph(text, style)]], colWidths=[6.5 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def build_quote_pdf(result: QuoteResult) -> bytes:
    """Render the customer quote summary for one quote package."""
    style = _styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title=f"Quick Quote {result.quote_id}", author="Quick Quote System",
    )

    product = result.product
    summary = result.summary
    story: list = []

    story.append(Paragraph("Quote Summary", style["title"]))
    story.append(Paragraph(
        f"Quote {result.quote_id} &nbsp;|&nbsp; prepared {result.created_at:%d %b %Y %H:%M}",
        style["sub"],
    ))
    story.append(_banner(
        "<b>NOT A QUOTE</b> &mdash; costed against <b>demonstration data</b>. "
        "Every price below is illustrative, not DrVita's."
        if result.dataset_is_demonstration else
        "<b>INTERNAL DRAFT</b> &mdash; not for release until Sales and Finance review.",
        RED, style["banner"],
    ))
    story.append(Spacer(1, 12))

    form = " ".join(part for part in [
        product.dosage_form,
        f"size {product.capsule_size}" if product.capsule_size else None,
        product.capsule_type,
        f"{product.count_per_bottle} ct" if product.count_per_bottle else None,
        f"{product.servings_per_bottle} servings" if product.servings_per_bottle else None,
    ] if part) or "Not specified"

    story.append(Paragraph("Product", style["h2"]))
    story.append(_kv_table([
        ("Customer", product.customer or "Not specified"),
        ("Product", product.formula_name or "Not specified"),
        ("Form", form),
        ("Volume", f"{product.annual_volume_bottles:,} bottles"
                   if product.annual_volume_bottles else "Not specified"),
        ("MOQ", f"{product.moq:,}" if product.moq else "Not specified"),
        ("Timeline", product.timeline or "Not specified"),
    ]))

    story.append(Paragraph("Estimated cost per bottle (Accepted lines only)", style["h2"]))
    # Inline markup is only interpreted inside a Paragraph, so the styled
    # values are built as paragraphs rather than bare strings.
    confidence_color = _CONFIDENCE_COLOR.get(summary.quote_confidence, GREY)
    story.append(_kv_table([
        ("Primary", Paragraph(f"<b>${summary.primary_per_bottle:,.2f}</b>", style["body"])),
        ("Worst case (+/-10% materials)",
         Paragraph(f"${summary.low_per_bottle:,.2f} &ndash; ${summary.high_per_bottle:,.2f}",
                   style["body"])),
        ("Confidence", Paragraph(
            f'<font color="#{confidence_color.hexval()[2:]}"><b>{summary.quote_confidence}</b></font>',
            style["body"])),
    ]))

    if summary.unmatched_count or summary.needs_review_count:
        story.append(Spacer(1, 10))
        story.append(_banner("<b>COST EXPOSURE</b>", RED, style["banner"]))
        exposure_lines = []
        if summary.unmatched_count:
            exposure_lines.append(
                f"<b>{summary.unmatched_count} unmatched line(s)</b> have no cost: "
                f"{'; '.join(summary.unmatched_items)}. Cost is unknown until Purchasing "
                "confirms vendor and pricing."
            )
        if summary.needs_review_count:
            exposure_lines.append(
                f"<b>{summary.needs_review_count} line(s)</b> await Purchasing confirmation, "
                f"carrying ${summary.tentative_exposure:,.4f}/bottle of tentative cost that is "
                "excluded from the primary total."
            )
        exposure_lines.append(
            "<b>The estimate above understates true cost until these are resolved.</b>"
        )
        body = Table(
            [[Paragraph(line, style["exposure"])] for line in exposure_lines],
            colWidths=[6.5 * inch],
        )
        body.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), RED_BG),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(body)

    story.append(Paragraph("Cost breakdown", style["h2"]))
    story.append(_grid(
        ["Component", "$/bottle", "Share"],
        [
            ["Raw materials", f"${summary.raw_materials:,.2f}",
             f"{_share(summary.raw_materials, summary.primary_per_bottle)}"],
            ["Packaging", f"${summary.packaging:,.2f}",
             f"{_share(summary.packaging, summary.primary_per_bottle)}"],
            ["Manufacturing", f"${summary.manufacturing:,.2f}",
             f"{_share(summary.manufacturing, summary.primary_per_bottle)}"],
            ["Testing", f"${summary.testing:,.2f}",
             f"{_share(summary.testing, summary.primary_per_bottle)}"],
            ["Total", f"${summary.primary_per_bottle:,.2f}", "100%"],
        ],
        [3.4 * inch, 1.6 * inch, 1.5 * inch],
    ))

    if result.price_breaks:
        story.append(Paragraph("Volume price breaks", style["h2"]))
        story.append(_grid(
            ["Bottles", "$/bottle", "Materials", "Packaging", "Manufacturing"],
            [[f"{item.volume_bottles:,}" + (" (quoted)" if item.is_quoted_volume else ""),
              f"${item.primary_per_bottle:,.2f}", f"${item.raw_materials:,.2f}",
              f"${item.packaging:,.2f}", f"${item.manufacturing:,.2f}"]
             for item in result.price_breaks],
            [1.5 * inch, 1.3 * inch, 1.3 * inch, 1.2 * inch, 1.2 * inch],
        ))
        story.append(Paragraph(
            "Material cost per bottle is flat across the ladder: purchase-order history "
            "carries no volume-tiered pricing. What moves is per-batch labour and "
            "overhead, and the machine the run size selects.", style["small"]))

    if result.pricing:
        story.append(Paragraph("Recommended price", style["h2"]))
        story.append(_grid(
            ["Requirement", "Margin", "On cost", "Price/bottle", "Margin/bottle"],
            [[item.label + (" (binding)" if item.binding else ""),
              f"{item.target_margin_pct:g}% {item.cost_basis.split()[0]} OH",
              f"${item.cost_per_bottle:,.2f}",
              f"${item.price_per_bottle:,.2f}", f"${item.margin_dollars:,.2f}"]
             for item in result.pricing],
            [2.1 * inch, 1.5 * inch, 1.0 * inch, 1.1 * inch, 1.1 * inch],
        ))
        story.append(Spacer(1, 6))
        story.append(_banner(
            "<b>These are recommendations, not prices.</b> Where an account is held to "
            "more than one requirement the price has to clear all of them, so the "
            "binding row sets it. Quick Quote does not set final pricing, margin or "
            "customer-facing terms.",
            RED, style["banner"],
        ))

    if summary.cost_drivers:
        story.append(Paragraph("Top 5 cost drivers", style["h2"]))
        story.append(_grid(
            ["#", "Driver", "$/bottle", "Share"],
            [
                [str(index), driver.label, f"${driver.cost_per_bottle:,.2f}",
                 f"{driver.share_pct:,.0f}%"]
                for index, driver in enumerate(summary.cost_drivers[:5], start=1)
            ],
            [0.4 * inch, 3.6 * inch, 1.3 * inch, 1.2 * inch],
        ))

    blocking = [flag for flag in result.flags if flag.severity == "blocking"]
    if blocking:
        story.append(PageBreak())
        story.append(Paragraph("Items requiring review", style["h2"]))
        story.append(_grid(
            ["Owner", "Item", "Reason"],
            [[flag.owner, Paragraph(flag.item, style["body"]),
              Paragraph(flag.reason, style["body"])] for flag in blocking],
            [0.9 * inch, 1.8 * inch, 3.8 * inch],
        ))

    story.append(Paragraph("Review flags by owner", style["h2"]))
    grouped = result.flags_by_owner()
    for owner in FLAG_OWNERS:
        flags = grouped.get(owner, [])
        block = [Paragraph(f"<b>{owner}</b> &mdash; {len(flags)} flag(s)", style["body"])]
        if flags:
            block.append(_grid(
                ["Item", "Reason"],
                [[Paragraph(flag.item, style["body"]), Paragraph(flag.reason, style["body"])]
                 for flag in flags],
                [2.0 * inch, 4.5 * inch],
            ))
        else:
            block.append(Paragraph("No flags for this owner.", style["small"]))
        block.append(Spacer(1, 8))
        story.append(KeepTogether(block))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Quick Quote prepares quote packages for internal review. It does not set final "
        "pricing, margin or customer-facing terms. All cost math, matching and flag "
        "generation is deterministic; only Accepted lines flow into the primary cost.",
        style["small"],
    ))

    document.build(story)
    return buffer.getvalue()


def _share(value: float, total: float) -> str:
    if not total:
        return "-"
    return f"{value / total * 100:,.0f}%"
