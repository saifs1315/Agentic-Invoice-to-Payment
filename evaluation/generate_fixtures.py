from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ROOT = Path(__file__).resolve().parent / "fixtures"


def font_path(bold: bool = False) -> Path | None:
    names = (
        ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold
        else ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    return next((Path(name) for name in names if Path(name).exists()), None)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = font_path(bold)
    return ImageFont.truetype(str(path), size) if path else ImageFont.load_default(size=size)


def create_pdf(path: Path, invoice_number: str, total: str, unit_price: str) -> None:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Synthetic invoice {invoice_number}",
    )
    story = [
        Paragraph("<b>NORTHSTAR INDUSTRIAL SUPPLY</b>", styles["Title"]),
        Paragraph("Synthetic evaluation document - no real financial data", styles["Normal"]),
        Spacer(1, 8 * mm),
    ]
    details = [
        ["Vendor ID:", "VEND-001", "Invoice Number:", invoice_number],
        ["Invoice Date:", "2026-08-22", "PO Number:", "PO-1001"],
        ["Currency:", "USD", "Payment Terms:", "Net 30"],
    ]
    detail_table = Table(details, colWidths=[32 * mm, 48 * mm, 35 * mm, 45 * mm])
    detail_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#172033")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend([detail_table, Spacer(1, 10 * mm)])
    lines = [
        ["Description", "Quantity", "Unit Price", "Amount", "PO Line"],
        ["Industrial sensors", "10", unit_price, total, "1"],
    ]
    line_table = Table(lines, colWidths=[70 * mm, 23 * mm, 28 * mm, 28 * mm, 20 * mm])
    line_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#155E75")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94A3B8")),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend(
        [
            line_table,
            Spacer(1, 5 * mm),
            Paragraph(
                f"Line 1: Industrial sensors | Qty: 10 | Unit Price: {unit_price} | Amount: {total} | PO Line: 1",
                styles["Normal"],
            ),
            Spacer(1, 13 * mm),
            Paragraph(f"<b>Invoice Total: {total}</b>", styles["Heading2"]),
            Spacer(1, 7 * mm),
            Paragraph("Thank you. This document is generated solely for LedgerPilot evaluation.", styles["Normal"]),
        ]
    )
    doc.build(story)


def create_scan(path: Path) -> None:
    random.seed(42)
    canvas = Image.new("L", (1500, 1900), 246)
    draw = ImageDraw.Draw(canvas)
    body = load_font(36)
    bold = load_font(43, bold=True)
    title = load_font(58, bold=True)
    draw.rectangle((80, 70, 1420, 1830), fill=255, outline=105, width=3)
    draw.text((130, 120), "NORTHSTAR INDUSTRIAL SUPPLY", font=title, fill=15)
    draw.text((130, 205), "Synthetic evaluation document - no real financial data", font=body, fill=50)
    rows = [
        "Vendor ID: VEND-001",
        "Invoice Number: INV-2026-012",
        "Invoice Date: 2026-08-23",
        "PO Number: PO-1001",
        "Currency: USD",
    ]
    for index, row in enumerate(rows):
        draw.text((135, 350 + index * 72), row, font=body, fill=30)
    draw.line((125, 760, 1375, 760), fill=70, width=3)
    draw.text((135, 810), "LINE ITEM", font=bold, fill=20)
    draw.text((135, 900), "Line 1: Industrial sensors | Qty: 10 |", font=body, fill=25)
    draw.text((135, 965), "Unit Price: 100.00 | Amount: 1000.00 | PO Line: 1", font=body, fill=25)
    draw.line((125, 1080, 1375, 1080), fill=70, width=3)
    draw.text((135, 1160), "Invoice Total: 1000.00", font=bold, fill=15)
    draw.text((135, 1280), "Payment Terms: Net 30", font=body, fill=45)
    draw.text((135, 1370), "Evaluation use only", font=body, fill=80)
    pixels = canvas.load()
    for _ in range(9000):
        x = random.randrange(canvas.width)
        y = random.randrange(canvas.height)
        pixels[x, y] = max(0, min(255, pixels[x, y] + random.choice((-10, -6, 6, 10))))
    canvas.rotate(0.35, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=242).save(path, "PNG")


def create_remittance_pdf(path: Path) -> None:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4, title="Synthetic AR remittance")
    story = [
        Paragraph("<b>CONTOSO CUSTOMER PAYMENT ADVICE</b>", styles["Title"]),
        Paragraph("Synthetic evaluation document - no real financial data", styles["Normal"]),
        Spacer(1, 8 * mm),
        Paragraph("Customer ID: CUST-001", styles["Normal"]),
        Paragraph("Remittance Reference: REM-PDF-001", styles["Normal"]),
        Paragraph("Payment Amount: 1000.00", styles["Normal"]),
        Paragraph("Currency: USD", styles["Normal"]),
        Paragraph("Open Items: AR-9001, AR-9002", styles["Normal"]),
    ]
    doc.build(story)


def create_remittance_scan(path: Path) -> None:
    canvas = Image.new("L", (1500, 1500), 255)
    draw = ImageDraw.Draw(canvas)
    body = load_font(42)
    title = load_font(55, bold=True)
    draw.rectangle((70, 70, 1430, 1430), outline=80, width=3)
    draw.text((120, 125), "CUSTOMER PAYMENT ADVICE", font=title, fill=15)
    rows = [
        "Customer ID: CUST-001",
        "Remittance Reference: REM-SCAN-001",
        "Payment Amount: 1000.00",
        "Currency: USD",
        "Open Items: AR-9001, AR-9002",
    ]
    for index, row in enumerate(rows):
        draw.text((125, 340 + index * 115), row, font=body, fill=25)
    draw.text((125, 1050), "Synthetic evaluation document", font=body, fill=70)
    canvas.save(path, "PNG")


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    create_pdf(ROOT / "po-1001-clean.pdf", "INV-2026-010", "1000.00", "100.00")
    create_pdf(ROOT / "po-1001-price-variance.pdf", "INV-2026-011", "1100.00", "110.00")
    create_scan(ROOT / "po-1001-scan.png")
    create_remittance_pdf(ROOT / "remittance-clean.pdf")
    create_remittance_scan(ROOT / "remittance-clean.png")
