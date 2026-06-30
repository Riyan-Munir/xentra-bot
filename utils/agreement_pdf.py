"""
Xentra - Job Agreement PDF Generator (Bot Copy)
=================================================
BytesIO-based generator used by the Discord bot to generate agreement PDFs
in-memory and send them as Discord file attachments.

Usage:
    from utils.agreement_pdf import generate_agreement_bytes

    pdf_bytes = generate_agreement_bytes({
        "agreement_id":       "XEN-AGR-...",
        "job_id":             "XEN-JOB-...",
        "job_application_id": "XEN-APP-...",
        "interview_room_id":  "XEN-ROOM-...",
        "client_name":        "Client Name",
        "freelancer_name":    "Freelancer Name",
        "final_budget":       2450.00,
        "milestones": [
            {
                "milestone_id":  "XEN-MIL-...",
                "title":        "Milestone Title",
                "description":  "Description text.",
                "budget":       500.00,
                "date":         "July 02, 2026",
            },
        ],
    })

    discord_file = discord.File(io.BytesIO(pdf_bytes), filename="agreement.pdf")
"""

import io
import math
import textwrap
import base64
from datetime import datetime

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch, mm
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table,
    TableStyle, KeepTogether, FrameBreak, NextPageTemplate, PageBreak,
    HRFlowable, ListFlowable, ListItem, Flowable
)
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.pdfbase.pdfmetrics import stringWidth


# ----------------------------------------------------------------------------
# BRAND / THEME
# ----------------------------------------------------------------------------

XENTRA_NAVY      = HexColor("#0B1220")
XENTRA_GREEN     = HexColor("#1A7A4A")
XENTRA_GREEN_DARK= HexColor("#145C38")
XENTRA_GREEN_LIGHT=HexColor("#E8F5EE")
XENTRA_MINT      = HexColor("#D0EDD9")
XENTRA_GOLD      = HexColor("#C9A24B")
XENTRA_GREY      = HexColor("#5B6472")
XENTRA_LIGHT_GREY= HexColor("#E7E9EE")
XENTRA_WATERMARK = HexColor("#1A7A4A")

PAGE_W, PAGE_H = LETTER

BORDER_MARGIN    = 16 * mm
BORDER_INNER_GAP = 2.2 * mm

CONTENT_MARGIN_TOP    = 30 * mm
CONTENT_MARGIN_BOTTOM = 26 * mm
CONTENT_MARGIN_LR     = 24 * mm


# ----------------------------------------------------------------------------
# STYLES
# ----------------------------------------------------------------------------

def build_styles():
    ss = getSampleStyleSheet()
    styles = {}

    styles["H1"] = ParagraphStyle(
        "XH1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=15.5,
        textColor=XENTRA_NAVY, spaceBefore=4, spaceAfter=10, leading=19,
    )
    styles["H2"] = ParagraphStyle(
        "XH2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=11.5,
        textColor=XENTRA_GREEN_DARK, spaceBefore=10, spaceAfter=6, leading=14,
    )
    styles["H3"] = ParagraphStyle(
        "XH3", parent=ss["Heading3"], fontName="Helvetica-Bold", fontSize=10.2,
        textColor=XENTRA_NAVY, spaceBefore=8, spaceAfter=4, leading=13,
    )
    styles["Body"] = ParagraphStyle(
        "XBody", parent=ss["Normal"], fontName="Helvetica", fontSize=9.2,
        textColor=HexColor("#262B33"), leading=13.4, alignment=TA_JUSTIFY,
        spaceAfter=6,
    )
    styles["BodyLeft"] = ParagraphStyle(
        "XBodyLeft", parent=styles["Body"], alignment=TA_LEFT,
    )
    styles["Small"] = ParagraphStyle(
        "XSmall", parent=ss["Normal"], fontName="Helvetica", fontSize=7.6,
        textColor=XENTRA_GREY, leading=10.5,
    )
    styles["SmallCenter"] = ParagraphStyle(
        "XSmallCenter", parent=styles["Small"], alignment=TA_CENTER,
    )
    styles["Bullet"] = ParagraphStyle(
        "XBullet", parent=ss["Normal"], fontName="Helvetica", fontSize=9.2,
        textColor=HexColor("#262B33"), leading=13.2, spaceAfter=3,
    )
    styles["FieldLabel"] = ParagraphStyle(
        "XFieldLabel", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=7.6,
        textColor=XENTRA_GREEN_DARK, leading=9,
    )
    styles["FieldValue"] = ParagraphStyle(
        "XFieldValue", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=10.5,
        textColor=XENTRA_NAVY, leading=12.5,
    )
    styles["FieldValueWhite"] = ParagraphStyle(
        "XFieldValueW", parent=styles["FieldValue"],
        textColor=colors.white, leading=12.5,
    )
    styles["MilestoneMeta"] = ParagraphStyle(
        "XMMeta", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=8,
        textColor=colors.white, leading=11, alignment=TA_LEFT,
    )
    styles["MilestoneTitle"] = ParagraphStyle(
        "XMTitle", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=12.5,
        textColor=colors.white, leading=15, alignment=TA_LEFT,
    )
    styles["TitleMain"] = ParagraphStyle(
        "XTitleMain", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=38,
        textColor=colors.white, leading=44, alignment=TA_CENTER,
    )
    styles["TitleSub"] = ParagraphStyle(
        "XTitleSub", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=16,
        textColor=HexColor("#C9D3FF"), leading=20, alignment=TA_CENTER,
    )
    styles["SigName"] = ParagraphStyle(
        "XSigName", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=9.5,
        textColor=XENTRA_NAVY, alignment=TA_CENTER, leading=12,
    )
    styles["SigRole"] = ParagraphStyle(
        "XSigRole", parent=ss["Normal"], fontName="Helvetica", fontSize=8,
        textColor=XENTRA_GREY, alignment=TA_CENTER, leading=10,
    )
    return styles


STY = build_styles()


# ----------------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------------

def fmt_money(value):
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value) if value else ""


# ----------------------------------------------------------------------------
# PAGE DECORATION
# ----------------------------------------------------------------------------

def draw_border(c: pdfcanvas.Canvas):
    c.saveState()
    c.setStrokeColor(XENTRA_GREEN_DARK)
    c.setLineWidth(1.4)
    c.rect(BORDER_MARGIN, BORDER_MARGIN,
           PAGE_W - 2 * BORDER_MARGIN, PAGE_H - 2 * BORDER_MARGIN, stroke=1, fill=0)
    c.setStrokeColor(XENTRA_MINT)
    c.setLineWidth(0.6)
    gap = BORDER_INNER_GAP
    c.rect(BORDER_MARGIN + gap, BORDER_MARGIN + gap,
           PAGE_W - 2 * (BORDER_MARGIN + gap), PAGE_H - 2 * (BORDER_MARGIN + gap),
           stroke=1, fill=0)
    tick = 6 * mm
    c.setStrokeColor(XENTRA_GREEN_DARK)
    c.setLineWidth(1.6)
    corners = [
        (BORDER_MARGIN, BORDER_MARGIN, 1, 1),
        (PAGE_W - BORDER_MARGIN, BORDER_MARGIN, -1, 1),
        (BORDER_MARGIN, PAGE_H - BORDER_MARGIN, 1, -1),
        (PAGE_W - BORDER_MARGIN, PAGE_H - BORDER_MARGIN, -1, -1),
    ]
    for x, y, dx, dy in corners:
        c.line(x, y, x + dx * tick, y)
        c.line(x, y, x, y + dy * tick)
    c.restoreState()


def draw_watermark(c: pdfcanvas.Canvas):
    c.saveState()
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(38)
    c.setFillColor(XENTRA_WATERMARK)
    c.setFillAlpha(0.055)
    c.setFont("Helvetica-Bold", 86)
    text = "XENTRA"
    w = stringWidth(text, "Helvetica-Bold", 86)
    c.drawString(-w / 2, -20, text)
    c.setFont("Helvetica-Bold", 15)
    sub = "JOB AGREEMENT"
    w2 = stringWidth(sub, "Helvetica-Bold", 15)
    c.setFillAlpha(0.065)
    c.drawString(-w2 / 2, -55, sub)
    c.restoreState()


def draw_header_footer(c: pdfcanvas.Canvas, doc, agreement_id="", page_label=""):
    c.saveState()
    hx = BORDER_MARGIN + BORDER_INNER_GAP + 4 * mm
    hy = PAGE_H - BORDER_MARGIN - BORDER_INNER_GAP - 6 * mm
    c.setFont("Helvetica-Bold", 8.6)
    c.setFillColor(XENTRA_GREEN_DARK)
    c.drawString(hx, hy, "XENTRA")
    c.setFont("Helvetica", 8.6)
    c.setFillColor(XENTRA_GREY)
    c.drawString(hx + stringWidth("XENTRA", "Helvetica-Bold", 8.6) + 4, hy, "\u2022 Job Agreement")
    if agreement_id:
        c.setFont("Helvetica", 8)
        c.setFillColor(XENTRA_GREY)
        rx = PAGE_W - BORDER_MARGIN - BORDER_INNER_GAP - 4 * mm
        c.drawRightString(rx, hy, f"Agreement ID: {agreement_id}")
    c.setStrokeColor(XENTRA_LIGHT_GREY)
    c.setLineWidth(0.6)
    c.line(hx, hy - 5, PAGE_W - BORDER_MARGIN - BORDER_INNER_GAP - 4 * mm, hy - 5)

    fx = BORDER_MARGIN + BORDER_INNER_GAP + 4 * mm
    fy = BORDER_MARGIN + BORDER_INNER_GAP + 5 * mm
    c.setStrokeColor(XENTRA_LIGHT_GREY)
    c.line(fx, fy + 6, PAGE_W - BORDER_MARGIN - BORDER_INNER_GAP - 4 * mm, fy + 6)
    c.setFont("Helvetica", 7.4)
    c.setFillColor(XENTRA_GREY)
    c.drawString(fx, fy, "This document is generated and authenticated by Xentra \u2014 Discord Freelance Platform")
    c.drawRightString(PAGE_W - BORDER_MARGIN - BORDER_INNER_GAP - 4 * mm, fy, f"Page {c.getPageNumber()}")
    c.restoreState()


# ----------------------------------------------------------------------------
# ROUND STAMP
# ----------------------------------------------------------------------------

def draw_round_stamp(c: pdfcanvas.Canvas, cx, cy, radius=15.5 * mm, rotation=-14):
    stamp_color = HexColor("#B23A48")
    c.saveState()
    c.translate(cx, cy)
    c.rotate(rotation)
    c.setFillAlpha(0.92)
    c.setStrokeAlpha(0.92)

    text_font = "Helvetica-Bold"
    text_size = 6.2

    c.setStrokeColor(stamp_color)
    c.setLineWidth(1.8)
    c.circle(0, 0, radius, stroke=1, fill=0)

    ring_gap = 4.2 * mm
    inner_ring_r = radius - ring_gap
    c.setLineWidth(0.9)
    c.circle(0, 0, inner_ring_r, stroke=1, fill=0)

    text_arc_r = radius - 1.6 * mm
    _draw_circular_text(
        c,
        "XENTRA  \u2022  JOB AGREEMENT  \u2022",
        text_arc_r,
        start_deg=205, end_deg=-25,
        font=text_font, size=text_size, color=stamp_color,
        inward=True
    )

    c.setFillColor(stamp_color)
    c.setFont("Helvetica-Bold", 10.5)
    band_w = inner_ring_r * 1.55
    c.saveState()
    c.setLineWidth(0.8)
    c.line(-band_w / 2, 3.6 * mm, band_w / 2, 3.6 * mm)
    c.line(-band_w / 2, -5.0 * mm, band_w / 2, -5.0 * mm)
    txt = "APPROVED"
    tw = stringWidth(txt, "Helvetica-Bold", 10.5)
    c.drawString(-tw / 2, -2.0 * mm, txt)
    c.restoreState()

    _draw_star(c, 0, inner_ring_r - 4.0 * mm, 1.6 * mm, stamp_color)
    c.restoreState()


def _draw_circular_text(c, text, radius, start_deg, end_deg, font, size, color, inward=True):
    c.saveState()
    c.setFillColor(color)
    total_chars_width = stringWidth(text, font, size)
    arc_span = math.radians(start_deg - end_deg)
    angle = math.radians(start_deg)
    for ch in text:
        ch_w = stringWidth(ch, font, size)
        step = (ch_w / max(total_chars_width, 0.0001)) * arc_span
        theta = angle - step / 2
        x = radius * math.cos(theta)
        y = radius * math.sin(theta)
        c.saveState()
        c.translate(x, y)
        deg = math.degrees(theta) - 90
        if not inward:
            deg += 180
        c.rotate(deg)
        c.setFont(font, size)
        c.drawCentredString(0, 0, ch)
        c.restoreState()
        angle -= step
    c.restoreState()


def _draw_star(c, cx, cy, r, color):
    c.saveState()
    c.setFillColor(color)
    points = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.42
        points.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    p = c.beginPath()
    p.moveTo(*points[0])
    for pt in points[1:]:
        p.lineTo(*pt)
    p.close()
    c.drawPath(p, fill=1, stroke=0)
    c.restoreState()


# ----------------------------------------------------------------------------
# STAMP IMAGE RENDERING
# ----------------------------------------------------------------------------

def draw_stamp_image(c: pdfcanvas.Canvas, cx, cy, stamp_b64: str, width_mm=28, height_mm=28):
    """Draw a stamp PNG image (from base64) centered at (cx, cy).

    Falls back to the round vector stamp if no base64 data is provided.
    """
    if not stamp_b64:
        draw_round_stamp(c, cx, cy)
        return

    try:
        from PIL import Image as PILImage
        stamp_bytes = base64.b64decode(stamp_b64)
        buf = io.BytesIO(stamp_bytes)
        pil_img = PILImage.open(buf)
        img_reader = pdfcanvas.ImageReader(pil_img)
        half_w = (width_mm * mm) / 2
        half_h = (height_mm * mm) / 2
        c.saveState()
        c.drawImage(img_reader, cx - half_w, cy - half_h,
                    width=width_mm * mm, height=height_mm * mm,
                    preserveAspectRatio=True, mask='auto')
        c.restoreState()
    except Exception:
        draw_round_stamp(c, cx, cy)


def draw_watermark_from_b64(c: pdfcanvas.Canvas, watermark_b64: str):
    """Draw a watermark image from base64. Falls back to text watermark."""
    if not watermark_b64:
        draw_watermark(c)
        return

    try:
        from PIL import Image as PILImage
        wm_bytes = base64.b64decode(watermark_b64)
        buf = io.BytesIO(wm_bytes)
        pil_img = PILImage.open(buf)
        img_reader = pdfcanvas.ImageReader(pil_img)
        c.saveState()
        c.setFillAlpha(0.08)
        # Scale watermark to roughly half page size, centred
        wm_w = PAGE_W * 0.50
        wm_h = PAGE_H * 0.50
        c.drawImage(img_reader, (PAGE_W - wm_w) / 2, (PAGE_H - wm_h) / 2,
                    width=wm_w, height=wm_h,
                    preserveAspectRatio=True, mask='auto')
        c.restoreState()
    except Exception:
        draw_watermark(c)


# ----------------------------------------------------------------------------
# PAGE TEMPLATES
# ----------------------------------------------------------------------------

class PageContext:
    agreement_id = ""
    stamp_b64 = ""
    watermark_b64 = ""
    is_signed = False


def on_title_page(c: pdfcanvas.Canvas, doc):
    pass


def on_inner_page(c: pdfcanvas.Canvas, doc):
    if PageContext.is_signed:
        draw_watermark_from_b64(c, PageContext.watermark_b64)
    else:
        draw_watermark(c)
    draw_border(c)
    draw_header_footer(c, doc, agreement_id=PageContext.agreement_id)


# ----------------------------------------------------------------------------
# TITLE PAGE
# ----------------------------------------------------------------------------

def render_title_page(c: pdfcanvas.Canvas, data):
    c.saveState()
    c.setFillColor(XENTRA_NAVY)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    c.setFillColor(HexColor("#1B3FCC"))
    c.setFillAlpha(0.30)
    c.rect(0, PAGE_H * 0.60, PAGE_W, PAGE_H * 0.18, fill=1, stroke=0)
    c.setFillAlpha(1)

    inset = 14 * mm
    c.setStrokeColor(XENTRA_GOLD)
    c.setLineWidth(1.1)
    c.rect(inset, inset, PAGE_W - 2 * inset, PAGE_H - 2 * inset, stroke=1, fill=0)
    inset2 = inset + 2.4 * mm
    c.setStrokeColor(colors.white)
    c.setStrokeAlpha(0.25)
    c.setLineWidth(0.6)
    c.rect(inset2, inset2, PAGE_W - 2 * inset2, PAGE_H - 2 * inset2, stroke=1, fill=0)
    c.setStrokeAlpha(1)

    c.saveState()
    c.translate(PAGE_W / 2, PAGE_H / 2 + 8 * mm)
    c.setFillColor(colors.white)
    c.setFillAlpha(0.035)
    c.setFont("Helvetica-Bold", 150)
    c.drawCentredString(0, -50, "X")
    c.restoreState()

    logo_cy = PAGE_H - 70 * mm
    c.saveState()
    c.setStrokeColor(XENTRA_GOLD)
    c.setLineWidth(1.3)
    c.circle(PAGE_W / 2, logo_cy, 15 * mm, stroke=1, fill=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 24)
    c.drawCentredString(PAGE_W / 2, logo_cy - 8, "X")
    c.restoreState()

    c.setFillColor(XENTRA_GOLD)
    c.setFont("Helvetica-Bold", 10.5)
    eyebrow = "D I S C O R D   F R E E L A N C E   P L A T F O R M"
    c.drawCentredString(PAGE_W / 2, logo_cy - 26 * mm, eyebrow)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 32)
    c.drawCentredString(PAGE_W / 2, logo_cy - 38 * mm, "XENTRA")
    c.setFont("Helvetica-Bold", 15)
    c.setFillColor(XENTRA_GOLD)
    c.drawCentredString(PAGE_W / 2, logo_cy - 46 * mm, "\u2022  J O B   A G R E E M E N T  \u2022")

    c.setFont("Helvetica", 10.5)
    c.setFillColor(HexColor("#C9D3FF"))
    c.drawCentredString(PAGE_W / 2, logo_cy - 56 * mm,
                        "A Binding Agreement Between Client and Freelancer")

    c.setStrokeColor(XENTRA_GOLD)
    c.setLineWidth(0.8)
    dc_y = logo_cy - 64 * mm
    c.line(PAGE_W / 2 - 28 * mm, dc_y, PAGE_W / 2 + 28 * mm, dc_y)

    card_w = PAGE_W - 2 * (inset2 + 14 * mm)
    card_h = 40 * mm
    card_x = (PAGE_W - card_w) / 2
    card_y = 58 * mm
    c.setFillColor(colors.white)
    c.setFillAlpha(0.06)
    c.roundRect(card_x, card_y, card_w, card_h, 3 * mm, fill=1, stroke=0)
    c.setFillAlpha(1)
    c.setStrokeColor(XENTRA_GOLD)
    c.setStrokeAlpha(0.5)
    c.setLineWidth(0.7)
    c.roundRect(card_x, card_y, card_w, card_h, 3 * mm, fill=0, stroke=1)
    c.setStrokeAlpha(1)

    rows = [
        ("Agreement ID", data.get("agreement_id", "")),
        ("Client",       data.get("client_name", "")),
        ("Freelancer",   data.get("freelancer_name", "")),
        ("Final Budget", fmt_money(data.get("final_budget", ""))),
    ]
    col_w = card_w / 2
    pad_x = 10 * mm
    top_y = card_y + card_h - 9 * mm
    for i, (label, value) in enumerate(rows):
        col = i % 2
        row = i // 2
        x = card_x + pad_x + col * col_w
        y = top_y - row * 16 * mm
        c.setFont("Helvetica-Bold", 7.6)
        c.setFillColor(XENTRA_GOLD)
        c.drawString(x, y, label.upper())
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(colors.white)
        val = str(value)
        if len(val) > 34:
            val = val[:31] + "..."
        c.drawString(x, y - 6.5 * mm, val)

    c.setFont("Helvetica", 8)
    c.setFillColor(HexColor("#8B93A8"))
    c.drawCentredString(PAGE_W / 2, 32 * mm,
                        "This Job Agreement is generated through the Xentra Discord bot and reflects the")
    c.drawCentredString(PAGE_W / 2, 32 * mm - 10,
                        "terms agreed upon by both parties for the engagement referenced above.")

    gen_date = data.get("generated_on") or datetime.utcnow().strftime("%B %d, %Y \u2014 %I:%M %p") + " UTC"
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(HexColor("#8B93A8"))
    c.drawCentredString(PAGE_W / 2, 22 * mm, f"Generated on {gen_date}")

    c.restoreState()


# ----------------------------------------------------------------------------
# CONTENT BUILDERS
# ----------------------------------------------------------------------------

def section_heading(text):
    flow = []
    flow.append(Paragraph(text, STY["H1"]))
    flow.append(HRFlowable(width="100%", thickness=1.4, color=XENTRA_GREEN,
                            spaceBefore=0, spaceAfter=10, lineCap="round"))
    return flow


def field_grid(pairs, ncols=2, col_widths=None):
    content_w = PAGE_W - 2 * CONTENT_MARGIN_LR
    if col_widths is None:
        col_widths = [content_w / ncols] * ncols

    rows = []
    current_row = []
    for i, (label, value) in enumerate(pairs):
        cell = [
            Paragraph(label.upper(), STY["FieldLabel"]),
            Spacer(1, 2),
            Paragraph(str(value), STY["FieldValue"]),
        ]
        current_row.append(cell)
        if len(current_row) == ncols:
            rows.append(current_row)
            current_row = []
    if current_row:
        while len(current_row) < ncols:
            current_row.append("")
        rows.append(current_row)

    table = Table(rows, colWidths=col_widths, hAlign="LEFT")
    style = [
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING",   (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 9),
        ("BACKGROUND",   (0, 0), (-1, -1), XENTRA_GREEN_LIGHT),
        ("BOX",          (0, 0), (-1, -1), 0.7, XENTRA_MINT),
        ("INNERGRID",    (0, 0), (-1, -1), 0.7, colors.white),
        ("LINEBELOW",    (0, 0), (-1, -1), 0.7, XENTRA_MINT),
    ]
    table.setStyle(TableStyle(style))
    return table


def build_milestones_flow(milestones):
    flow = []
    flow += section_heading("Milestones")
    flow.append(Paragraph(
        f"This engagement is structured into <b>{len(milestones)}</b> milestone(s) as agreed by both "
        "parties. Each milestone below defines its scope, associated budget, and (where applicable) "
        "an expected delivery date.",
        STY["Body"]
    ))
    flow.append(Spacer(1, 4))

    content_w = PAGE_W - 2 * CONTENT_MARGIN_LR

    for idx, ms in enumerate(milestones, start=1):
        title       = ms.get("title",       f"Milestone {idx}")
        description = ms.get("description", "")
        budget      = ms.get("budget",      "")
        date        = ms.get("date",        "")
        milestone_id = ms.get("milestone_id", "")

        header_data = [[
            Paragraph(f"MILESTONE {idx}", STY["MilestoneMeta"]),
            Paragraph(f"ID: `{milestone_id}`",  STY["MilestoneMeta"]),
        ]]
        header_table = Table(header_data, colWidths=[content_w * 0.7, content_w * 0.3])
        header_table.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), XENTRA_GREEN_DARK),
            ("ALIGN",        (1, 0), (1, 0),   "RIGHT"),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",  (0, 0), (0, 0),   12),
            ("RIGHTPADDING", (1, 0), (1, 0),   12),
            ("TOPPADDING",   (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ]))

        title_row = [[Paragraph(title, STY["MilestoneTitle"]), ""]]
        title_table = Table(title_row, colWidths=[content_w * 0.7, content_w * 0.3])
        title_table.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), XENTRA_GREEN_DARK),
            ("LEFTPADDING",  (0, 0), (0, 0),   12),
            ("TOPPADDING",   (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 8),
        ]))

        body_rows = []
        if description:
            body_rows.append([Paragraph(f"<b>Description:</b> {description}", STY["BodyLeft"])])
        meta_bits = []
        if date:
            meta_bits.append(f"<b>Target Date:</b> {date}")
        meta_bits.append(f"<b>Milestone Budget:</b> {fmt_money(budget)}")
        body_rows.append([Paragraph("&nbsp;&nbsp;|&nbsp;&nbsp;".join(meta_bits), STY["Small"])])

        body_table = Table(body_rows, colWidths=[content_w])
        body_table.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), colors.white),
            ("BOX",          (0, 0), (-1, -1), 0.7, XENTRA_MINT),
            ("LEFTPADDING",  (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING",   (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 9),
        ]))

        block = KeepTogether([header_table, title_table, body_table, Spacer(1, 10)])
        flow.append(block)

    return flow


def lorem_paragraphs(n=2):
    base = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor "
        "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud "
        "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure "
        "dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur."
    )
    base2 = (
        "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt "
        "mollit anim id est laborum. Sed ut perspiciatis unde omnis iste natus error sit "
        "voluptatem accusantium doloremque laudantium, totam rem aperiam, eaque ipsa quae ab "
        "illo inventore veritatis et quasi architecto beatae vitae dicta sunt explicabo."
    )
    return [base, base2][:n]


def build_terms_flow(terms_sections=None):
    flow = []
    flow += section_heading("Terms and Conditions")

    if terms_sections is None:
        terms_sections = default_terms_sections()

    for i, section in enumerate(terms_sections, start=1):
        heading = section.get("heading", f"Section {i}")
        kind    = section.get("type",    "para")
        content = section.get("content")

        flow.append(Paragraph(f"{i}. {heading}", STY["H2"]))

        if kind == "para":
            paras = content if isinstance(content, list) else [content]
            for p in paras:
                flow.append(Paragraph(p, STY["Body"]))

        elif kind == "bullets":
            items = [ListItem(Paragraph(item, STY["Bullet"]), leftIndent=14,
                              bulletColor=XENTRA_GREEN_DARK)
                     for item in content]
            flow.append(ListFlowable(
                items, bulletType="bullet", start="circle",
                bulletFontName="Helvetica", bulletFontSize=6,
                leftIndent=16, spaceBefore=2, spaceAfter=8
            ))

        elif kind == "numbered":
            items = [ListItem(Paragraph(item, STY["Bullet"]), leftIndent=14)
                     for item in content]
            flow.append(ListFlowable(
                items, bulletType="1", start="1",
                bulletFontName="Helvetica-Bold", bulletFontSize=8.5,
                leftIndent=16, spaceBefore=2, spaceAfter=8
            ))

        elif kind == "mixed":
            for block in content:
                if block["type"] == "para":
                    flow.append(Paragraph(block["value"], STY["Body"]))
                elif block["type"] == "bullets":
                    items = [ListItem(Paragraph(item, STY["Bullet"]), leftIndent=14)
                             for item in block["value"]]
                    flow.append(ListFlowable(
                        items, bulletType="bullet", start="circle",
                        bulletFontName="Helvetica", bulletFontSize=6,
                        leftIndent=16, spaceBefore=2, spaceAfter=8
                    ))

        flow.append(Spacer(1, 4))

    return flow


def default_terms_sections():
    return [
        {
            "heading": "Scope of Engagement",
            "type":    "para",
            "content": lorem_paragraphs(2),
        },
        {
            "heading": "Payment Terms",
            "type":    "bullets",
            "content": [
                "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor.",
                "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip.",
                "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat.",
                "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit.",
            ],
        },
        {
            "heading": "Confidentiality & Ownership",
            "type":    "mixed",
            "content": [
                {"type": "para", "value": lorem_paragraphs(1)[0]},
                {"type": "bullets", "value": [
                    "Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium.",
                    "Totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto.",
                    "Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut fugit.",
                ]},
            ],
        },
        {
            "heading": "Termination & Dispute Resolution",
            "type":    "numbered",
            "content": [
                "Neque porro quisquam est, qui dolorem ipsum quia dolor sit amet consectetur.",
                "Adipisci velit, sed quia non numquam eius modi tempora incidunt ut labore.",
                "Ut aliquid ex ea commodi consequatur, quis autem vel eum iure reprehenderit.",
                "Quam nihil molestiae consequatur, vel illum qui dolorem eum fugiat quo voluptas.",
            ],
        },
    ]


class SignatureBox(Flowable):
    """
    A self-contained signature box flowable. Draws a bordered rectangle and,
    if stamp=True, renders the stamp image (or round vector stamp fallback)
    overlapping the box at draw-time.
    """

    def __init__(self, width, height, stamp=False, stamp_b64='', stamp_rotation=-12, stamp_radius=None):
        Flowable.__init__(self)
        self.width         = width
        self.height        = height
        self.stamp         = stamp
        self.stamp_b64     = stamp_b64
        self.stamp_rotation = stamp_rotation
        self.stamp_radius  = stamp_radius or (min(width, height) * 0.42)

    def wrap(self, availWidth, availHeight):
        return (self.width, self.height)

    def draw(self):
        c = self.canv
        c.saveState()
        c.setStrokeColor(XENTRA_MINT)
        c.setLineWidth(0.8)
        c.setFillColor(colors.white)
        c.rect(0, 0, self.width, self.height, fill=1, stroke=1)
        c.restoreState()
        if self.stamp:
            cx = self.width / 2
            cy = self.height / 2
            if self.stamp_b64:
                draw_stamp_image(c, cx, cy, self.stamp_b64, width_mm=22, height_mm=22)
            else:
                draw_round_stamp(c, cx, cy, radius=self.stamp_radius, rotation=self.stamp_rotation)


def build_signature_flow(data):
    flow = []
    flow += section_heading("Agreement Signature & Authentication")

    flow.append(Paragraph(
        "By signing below, both the Client and the Freelancer acknowledge that they have read, "
        "understood, and agreed to the full terms of this Job Agreement, including all milestones, "
        "budgets, and conditions outlined above.",
        STY["Body"]
    ))
    flow.append(Spacer(1, 6))

    now = data.get("signed_on") or datetime.utcnow().strftime("%B %d, %Y \u2014 %I:%M %p") + " UTC"
    is_signed = PageContext.is_signed
    label = "Agreement Signed" if is_signed else "Date & Time of Agreement review"
    flow.append(field_grid([
        (label, now),
        ("Witnessed By", "Xentra \u2014 Automated Escrow & Agreement System"),
    ], ncols=2))
    flow.append(Spacer(1, 16))

    content_w = PAGE_W - 2 * CONTENT_MARGIN_LR
    box_w     = content_w / 2 - 6
    sig_box_h = 30 * mm

    # Stamp image from PageContext (set by caller when both parties accepted)
    stamp_b64 = PageContext.stamp_b64

    def sig_cell(role_label, name):
        """Signature box — with stamp when signed, empty when in review phase."""
        return [
            SignatureBox(box_w - 16, sig_box_h, stamp=is_signed, stamp_b64=stamp_b64, stamp_radius=13 * mm),
            Spacer(1, 5),
            HRFlowable(width="92%", thickness=0.8, color=XENTRA_GREY, spaceBefore=0, spaceAfter=4),
            Paragraph(name or "&nbsp;", STY["SigName"]),
            Paragraph(role_label,        STY["SigRole"]),
        ]

    sig_table = Table(
        [[sig_cell("Freelancer Signature", data.get("freelancer_name", "")),
          sig_cell("Client Signature",     data.get("client_name",     ""))]],
        colWidths=[content_w / 2, content_w / 2],
    )
    sig_table.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    flow.append(KeepTogether([sig_table]))
    flow.append(Spacer(1, 10))

    flow.append(Paragraph(
        "Agreement IDs, milestone records, and signature timestamps for this document are stored "
        "and verifiable through the Xentra platform.",
        STY["Small"]
    ))

    return flow


# ----------------------------------------------------------------------------
# DOCUMENT ASSEMBLY
# ----------------------------------------------------------------------------

class StampingDocTemplate(BaseDocTemplate):
    pass


def build_pdf(data, output_path):
    """
    Build the PDF document to the given file-like object or path.
    data: dict with keys —
        agreement_id, client_name, freelancer_name, job_id, job_application_id,
        interview_room_id, final_budget, milestones (list of dicts),
        terms_sections (optional, list of dicts), generated_on (optional),
        signed_on (optional),
        stamp_b64 (optional base64-encoded stamp image),
        watermark_b64 (optional base64-encoded watermark image),
        is_signed (optional bool — True for signed PDF with stamps, False for review)
    output_path: destination (file path or BytesIO-like object)
    """
    PageContext.agreement_id = data.get("agreement_id", "")
    PageContext.stamp_b64 = data.get("stamp_b64", "")
    PageContext.watermark_b64 = data.get("watermark_b64", "")
    PageContext.is_signed = data.get("is_signed", False)

    doc = StampingDocTemplate(
        output_path,
        pagesize=LETTER,
        leftMargin=CONTENT_MARGIN_LR,
        rightMargin=CONTENT_MARGIN_LR,
        topMargin=CONTENT_MARGIN_TOP,
        bottomMargin=CONTENT_MARGIN_BOTTOM,
        title=f"Xentra Job Agreement - {data.get('agreement_id','')}",
        author="Xentra",
        subject="Job Agreement",
    )

    frame_title = Frame(0, 0, PAGE_W, PAGE_H, id="title_frame",
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    frame_inner = Frame(
        CONTENT_MARGIN_LR, CONTENT_MARGIN_BOTTOM,
        PAGE_W - 2 * CONTENT_MARGIN_LR,
        PAGE_H - CONTENT_MARGIN_TOP - CONTENT_MARGIN_BOTTOM,
        id="inner_frame", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0
    )

    def title_page_cb(c, d):
        render_title_page(c, data)

    templates = [
        PageTemplate(id="Title", frames=[frame_title], onPage=title_page_cb),
        PageTemplate(id="Inner", frames=[frame_inner], onPage=on_inner_page),
    ]
    doc.addPageTemplates(templates)

    story = []
    story.append(NextPageTemplate("Inner"))
    story.append(PageBreak())

    # Agreement Overview
    story += section_heading("Agreement Overview")
    story.append(field_grid([
        ("Agreement ID",      data.get("agreement_id",      "")),
        ("Job ID",            data.get("job_id",            "")),
        ("Client Name",       data.get("client_name",       "")),
        ("Freelancer Name",   data.get("freelancer_name",   "")),
        ("Job Application ID",data.get("job_application_id","")),
        ("Interview Room ID", data.get("interview_room_id", "")),
        ("No. of Milestones", str(len(data.get("milestones", [])))),
        ("Job's Final Budget",fmt_money(data.get("final_budget", ""))),
    ], ncols=2))
    story.append(Spacer(1, 14))

    # Milestones
    story += build_milestones_flow(data.get("milestones", []))
    story.append(Spacer(1, 6))

    # Terms & Conditions (start on new page)
    story.append(PageBreak())
    story += build_terms_flow(data.get("terms_sections"))
    story.append(Spacer(1, 6))

    # Signature Section (start on new page)
    story.append(PageBreak())
    story += build_signature_flow(data)

    doc.build(story)


def generate_agreement_bytes(data):
    """Generate the agreement PDF and return as bytes (no file written)."""
    buf = io.BytesIO()
    build_pdf(data, buf)
    return buf.getvalue()
