"""
Xentra — Chat Room Transcript PDF Generator
============================================
Generates a polished WhatsApp-style PDF transcript of a Xentra interview
room session, including all message types:

  • simple msg        — plain text from client or freelancer
  • complain          — complaint (optionally a reply to a prior msg/complain)
  • command execution — a bot command run by client or freelancer
  • bot msg           — automated message from Xentra bot

Right-side bubbles = the perspective of whoever requested the transcript
(viewer_role: "freelancer" or "client").
Left-side bubbles  = all other senders.

Usage:
    from utils.transcript_generator import generate_transcript
    generate_transcript(data, output_path)
"""

import base64
import io
import logging
import math
import textwrap
import urllib.error
import urllib.request
from datetime import datetime

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import mm, inch
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
    NextPageTemplate, PageBreak, Flowable, HRFlowable
)
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.pdfbase.pdfmetrics import stringWidth

logger = logging.getLogger("bot.transcript_generator")


# ─────────────────────────────────────────────────────────────────────────────
# BRAND PALETTE
# ─────────────────────────────────────────────────────────────────────────────

XENTRA_NAVY         = HexColor("#0B1220")
XENTRA_GREEN        = HexColor("#1A7A4A")
XENTRA_GREEN_DARK   = HexColor("#145C38")
XENTRA_GREEN_LIGHT  = HexColor("#E8F5EE")
XENTRA_MINT         = HexColor("#C8E6D4")
XENTRA_GOLD         = HexColor("#C9A24B")
XENTRA_GREY         = HexColor("#5B6472")
XENTRA_LIGHT_GREY   = HexColor("#DDE1E9")
XENTRA_WATERMARK    = HexColor("#1A7A4A")

# Chat page background — warm neutral, like a real messaging app
PAGE_BG             = HexColor("#EAEDF2")

# ── Bubble fills & borders — refined, muted, professional ────────────────────

# "You" (viewer) — clean sage green, like WhatsApp's own green tint
BUBBLE_SELF         = HexColor("#DCF2E4")

BUBBLE_SELF_BORDER  = HexColor("#9ECDB4")

# Other party — crisp white with a cool hint
BUBBLE_OTHER        = HexColor("#FFFFFF")
BUBBLE_OTHER_BORDER = HexColor("#D1D8E4")

# Complaint — warm amber/sand, noticeable but not aggressive
BUBBLE_COMPLAIN     = HexColor("#FEF3CD")
BUBBLE_COMPLAIN_BDR = HexColor("#DDB95A")

# Command execution — slate-indigo tint
BUBBLE_COMMAND      = HexColor("#EBF0FF")
BUBBLE_COMMAND_BDR  = HexColor("#8EA8E8")

# Bot message — cool light steel-blue (formerly "system" colour)
BUBBLE_BOT          = HexColor("#F0F4F8")
BUBBLE_BOT_BDR      = HexColor("#B0C4D8")

# Leave message — soft coral/salmon, reflects a departure event
BUBBLE_LEAVE        = HexColor("#FDE8E8")
BUBBLE_LEAVE_BDR    = HexColor("#E8A0A0")

# Reply preview strip
REPLY_BG            = HexColor("#F7F8FB")
REPLY_BORDER        = HexColor("#CDD3DF")

# Text colours inside bubbles
MSG_TEXT_COLOR      = HexColor("#1A1F2E")   # near-black for readability
TS_COLOR            = HexColor("#8A95A5")   # muted grey timestamp

# Date pill
DATE_PILL_BG        = HexColor("#D6DCE8")
DATE_PILL_TEXT      = HexColor("#4A5568")

# Avatar background colours per role
AV_COLOR_FREELANCER = (HexColor("#1A7A4A"), HexColor("#125C36"))  # green
AV_COLOR_CLIENT     = (HexColor("#2B52CC"), HexColor("#1E3DA0"))  # blue
AV_COLOR_BOT        = (HexColor("#5B3FA6"), HexColor("#432E7E"))  # purple


# ─────────────────────────────────────────────────────────────────────────────
# PAGE GEOMETRY
# ─────────────────────────────────────────────────────────────────────────────

PAGE_W, PAGE_H = LETTER
BORDER_MARGIN    = 14 * mm
BORDER_INNER_GAP = 2 * mm
CONTENT_ML       = 22 * mm
CONTENT_MR       = 22 * mm
CONTENT_MT       = 28 * mm
CONTENT_MB       = 24 * mm
CONTENT_W        = PAGE_W - CONTENT_ML - CONTENT_MR

# ── Avatar — small, WhatsApp proportions ─────────────────────────────────────
# Avatar radius — set to 70 % of original (5.5 → 3.85 mm)
AVATAR_R   = 5.5 * mm * 0.7   # ≈ 3.85 mm radius (≈ 7.7 mm diameter)
AVATAR_D   = AVATAR_R * 2
AVATAR_PAD = 2.0 * mm     # gap between avatar edge and bubble edge

# ── Bubble width ─────────────────────────────────────────────────────────────
MAX_BUBBLE_W = CONTENT_W * 0.70   # max 70 % of content width

# Fonts
FONT_REG  = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITAL = "Helvetica-Oblique"

# Typography
FONT_SIZE_MAIN    = 9.0
FONT_SIZE_CMD_TAG = 7.2
FONT_SIZE_TS      = 6.8
FONT_SIZE_REPLY   = 7.6
FONT_SIZE_REPLY_LBL = 6.8
LEADING           = 13.0
PAD_H             = 4.5 * mm   # horizontal inner padding per side
PAD_V             = 3.5 * mm   # vertical inner padding top/bottom
REPLY_H           = 8.5 * mm   # fixed height of reply-preview strip
REPLY_PAD         = 1.5 * mm   # gap below reply strip before body text
CMD_TAG_H         = 5.0 * mm   # height of /command badge
MSG_GAP           = 2.0 * mm   # vertical gap between consecutive messages

# Max lines per bubble.  A 40-line bubble is ~560 pt, safely under the
# 645 pt frame.  Messages exceeding this are truncated at the data level
# so no flowable ever triggers ReportLab's split() path.
MAX_BODY_LINES = 40

# ─────────────────────────────────────────────────────────────────────────────
# PAGE DECORATION
# ─────────────────────────────────────────────────────────────────────────────

def draw_border(c):
    c.saveState()
    c.setStrokeColor(XENTRA_GREEN_DARK)
    c.setLineWidth(1.3)
    c.rect(BORDER_MARGIN, BORDER_MARGIN,
           PAGE_W - 2*BORDER_MARGIN, PAGE_H - 2*BORDER_MARGIN, stroke=1, fill=0)
    c.setStrokeColor(XENTRA_MINT)
    c.setLineWidth(0.5)
    g = BORDER_INNER_GAP
    c.rect(BORDER_MARGIN+g, BORDER_MARGIN+g,
           PAGE_W-2*(BORDER_MARGIN+g), PAGE_H-2*(BORDER_MARGIN+g), stroke=1, fill=0)
    tick = 5*mm
    c.setStrokeColor(XENTRA_GREEN_DARK)
    c.setLineWidth(1.5)
    for x, y, dx, dy in [
        (BORDER_MARGIN, BORDER_MARGIN, 1, 1),
        (PAGE_W-BORDER_MARGIN, BORDER_MARGIN, -1, 1),
        (BORDER_MARGIN, PAGE_H-BORDER_MARGIN, 1, -1),
        (PAGE_W-BORDER_MARGIN, PAGE_H-BORDER_MARGIN, -1, -1),
    ]:
        c.line(x, y, x+dx*tick, y)
        c.line(x, y, x, y+dy*tick)
    c.restoreState()


def draw_watermark(c):
    c.saveState()
    c.translate(PAGE_W/2, PAGE_H/2)
    c.rotate(38)
    c.setFillColor(XENTRA_WATERMARK)
    c.setFillAlpha(0.04)
    c.setFont(FONT_BOLD, 72)
    c.drawCentredString(0, 0, "XENTRA")
    c.setFont(FONT_BOLD, 12)
    c.setFillAlpha(0.048)
    c.drawCentredString(0, -40, "CHAT TRANSCRIPT")
    c.restoreState()


def draw_watermark_from_b64(c, watermark_b64):
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
        wm_w = PAGE_W * 0.50
        wm_h = PAGE_H * 0.50
        c.drawImage(img_reader, (PAGE_W - wm_w) / 2, (PAGE_H - wm_h) / 2,
                    width=wm_w, height=wm_h,
                    preserveAspectRatio=True, mask='auto')
        c.restoreState()
    except Exception:
        draw_watermark(c)


def draw_page_bg(c):
    c.saveState()
    c.setFillColor(PAGE_BG)
    g = BORDER_MARGIN + BORDER_INNER_GAP + 0.4
    c.rect(g, g, PAGE_W-2*g, PAGE_H-2*g, fill=1, stroke=0)
    c.restoreState()


class PageCtx:
    room_id       = ""
    transcript_id = ""
    watermark_b64 = ""


def draw_header_footer(c, doc):
    c.saveState()
    hx = BORDER_MARGIN + BORDER_INNER_GAP + 4*mm
    hy = PAGE_H - BORDER_MARGIN - BORDER_INNER_GAP - 5.5*mm
    c.setFont(FONT_BOLD, 8.2)
    c.setFillColor(XENTRA_GREEN_DARK)
    c.drawString(hx, hy, "XENTRA")
    c.setFont(FONT_REG, 8.2)
    c.setFillColor(XENTRA_GREY)
    off = stringWidth("XENTRA", FONT_BOLD, 8.2) + 3.5
    c.drawString(hx + off, hy, "\u2022 Chat Transcript")
    if PageCtx.room_id:
        c.setFont(FONT_REG, 7.6)
        rx = PAGE_W - BORDER_MARGIN - BORDER_INNER_GAP - 4*mm
        c.drawRightString(rx, hy, f"Room: {PageCtx.room_id}")
    c.setStrokeColor(XENTRA_LIGHT_GREY)
    c.setLineWidth(0.5)
    c.line(hx, hy - 4, PAGE_W-BORDER_MARGIN-BORDER_INNER_GAP-4*mm, hy - 4)

    fx = hx
    fy = BORDER_MARGIN + BORDER_INNER_GAP + 4*mm
    c.line(fx, fy+6, PAGE_W-BORDER_MARGIN-BORDER_INNER_GAP-4*mm, fy+6)
    c.setFont(FONT_REG, 7.0)
    c.setFillColor(XENTRA_GREY)
    c.drawString(fx, fy, "Generated by Xentra \u2014 Discord Freelance Platform")
    c.drawRightString(PAGE_W-BORDER_MARGIN-BORDER_INNER_GAP-4*mm, fy,
                      f"Page {c.getPageNumber()}")
    c.restoreState()


def on_inner_page(c, doc):
    draw_page_bg(c)
    draw_watermark_from_b64(c, PageCtx.watermark_b64)
    draw_border(c)
    draw_header_footer(c, doc)


# ─────────────────────────────────────────────────────────────────────────────
# TITLE PAGE
# ─────────────────────────────────────────────────────────────────────────────

def render_title_page(c, data):
    c.saveState()
    c.setFillColor(XENTRA_NAVY)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    c.setFillColor(HexColor("#1B3FCC"))
    c.setFillAlpha(0.26)
    c.rect(0, PAGE_H*0.60, PAGE_W, PAGE_H*0.18, fill=1, stroke=0)
    c.setFillAlpha(1)

    inset = 14*mm
    c.setStrokeColor(XENTRA_GOLD)
    c.setLineWidth(1.0)
    c.rect(inset, inset, PAGE_W-2*inset, PAGE_H-2*inset, stroke=1, fill=0)
    inset2 = inset + 2.2*mm
    c.setStrokeColor(colors.white)
    c.setStrokeAlpha(0.20)
    c.setLineWidth(0.5)
    c.rect(inset2, inset2, PAGE_W-2*inset2, PAGE_H-2*inset2, stroke=1, fill=0)
    c.setStrokeAlpha(1)

    c.saveState()
    c.translate(PAGE_W/2, PAGE_H/2 + 8*mm)
    c.setFillColor(colors.white)
    c.setFillAlpha(0.025)
    c.setFont(FONT_BOLD, 130)
    c.drawCentredString(0, -50, "X")
    c.restoreState()

    logo_cy = PAGE_H - 66*mm
    c.setStrokeColor(XENTRA_GOLD)
    c.setLineWidth(1.1)
    c.circle(PAGE_W/2, logo_cy, 13*mm, stroke=1, fill=0)
    c.setFillColor(colors.white)
    c.setFont(FONT_BOLD, 20)
    c.drawCentredString(PAGE_W/2, logo_cy - 7, "X")

    c.setFillColor(XENTRA_GOLD)
    c.setFont(FONT_BOLD, 9.5)
    c.drawCentredString(PAGE_W/2, logo_cy - 24*mm,
                        "D I S C O R D   F R E E L A N C E   P L A T F O R M")

    c.setFillColor(colors.white)
    c.setFont(FONT_BOLD, 28)
    c.drawCentredString(PAGE_W/2, logo_cy - 36*mm, "XENTRA")

    c.setFillColor(XENTRA_GOLD)
    c.setFont(FONT_BOLD, 12.5)
    c.drawCentredString(PAGE_W/2, logo_cy - 44*mm,
                        "\u2022  C H A T   T R A N S C R I P T  \u2022")

    c.setFillColor(HexColor("#C9D3FF"))
    c.setFont(FONT_REG, 9.5)
    c.drawCentredString(PAGE_W/2, logo_cy - 52*mm,
                        "Official Interview Room Conversation Record")

    c.setStrokeColor(XENTRA_GOLD)
    c.setLineWidth(0.65)
    dc_y = logo_cy - 60*mm
    c.line(PAGE_W/2 - 25*mm, dc_y, PAGE_W/2 + 25*mm, dc_y)

    card_w = PAGE_W - 2*(inset2 + 11*mm)
    card_h = 50*mm
    card_x = (PAGE_W - card_w) / 2
    card_y = 52*mm
    c.setFillColor(colors.white)
    c.setFillAlpha(0.06)
    c.roundRect(card_x, card_y, card_w, card_h, 3*mm, fill=1, stroke=0)
    c.setFillAlpha(1)
    c.setStrokeColor(XENTRA_GOLD)
    c.setStrokeAlpha(0.45)
    c.setLineWidth(0.6)
    c.roundRect(card_x, card_y, card_w, card_h, 3*mm, fill=0, stroke=1)
    c.setStrokeAlpha(1)

    col_w = card_w / 2
    pad   = 9*mm
    rows  = [
        ("Room ID",        data.get("room_id", "")),
        ("Client",         data.get("client_name", "")),
        ("Freelancer",     data.get("freelancer_name", "")),
        ("Total Messages", str(data.get("_msg_count", 0))),
        ("Generated On",   data.get("generated_on",
                                    datetime.utcnow().strftime("%B %d, %Y") + " UTC")),
    ]
    top_y = card_y + card_h - 9*mm
    for i, (label, value) in enumerate(rows):
        col = i % 2
        row = i // 2
        x = card_x + pad + col*col_w
        y = top_y - row*14.5*mm
        c.setFont(FONT_BOLD, 6.8)
        c.setFillColor(XENTRA_GOLD)
        c.drawString(x, y, label)
        c.setFont(FONT_BOLD, 9.5)
        c.setFillColor(colors.white)
        val = str(value)
        if len(val) > 32:
            val = val[:29] + "..."
        c.drawString(x, y - 5*mm, val)

    c.setFont(FONT_REG, 7.6)
    c.setFillColor(HexColor("#8B93A8"))
    c.drawCentredString(PAGE_W/2, 29*mm,
        "This transcript is an official record generated by Xentra and reflects")
    c.drawCentredString(PAGE_W/2, 29*mm - 9.5,
        "the conversation held between the parties in the room referenced above.")
    gen = data.get("generated_on",
                   datetime.utcnow().strftime("%B %d, %Y \u2014 %I:%M %p") + " UTC")
    c.setFont(FONT_ITAL, 7.6)
    c.drawCentredString(PAGE_W/2, 19*mm, f"Generated on {gen}")
    c.restoreState()


# ─────────────────────────────────────────────────────────────────────────────
# AVATAR
# ─────────────────────────────────────────────────────────────────────────────

def _load_image_bytes(url_or_path):
    """
    Download / read image bytes.
    Adds a common User-Agent so Discord CDN doesn't reject the request,
    and optionally strips SSL verification for environments where certs
    are not available (Windows).
    """
    try:
        if url_or_path.startswith(("http://", "https://")):
            req = urllib.request.Request(
                url_or_path,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.read()
            except urllib.error.URLError:
                # Retry without SSL verification (common on Windows)
                import ssl
                ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                    return r.read()
        else:
            with open(url_or_path, "rb") as f:
                return f.read()
    except Exception:
        logger.debug("Failed to load image: %s", url_or_path, exc_info=True)
        return None


def draw_avatar(c, cx, cy, radius, label_char,
                img_url_or_path=None, bg_color=None, border_color=None):
    """
    Circle avatar at (cx, cy).  cy is the CENTRE of the circle.
    Falls back to coloured circle + initial letter if image unavailable.
    """
    bg_color     = bg_color     or XENTRA_GREEN
    border_color = border_color or XENTRA_GREEN_DARK

    c.saveState()

    img_bytes = _load_image_bytes(img_url_or_path) if img_url_or_path else None
    drawn_img = False
    if img_bytes:
        try:
            from PIL import Image as PILImage
            img = PILImage.open(io.BytesIO(img_bytes)).convert("RGBA")
            side = int(radius * 2 * 4)
            img  = img.resize((side, side), PILImage.LANCZOS)
            p = c.beginPath()
            p.circle(cx, cy, radius)
            c.clipPath(p, stroke=0, fill=0)
            # drawInlineImage accepts PIL Image objects directly;
            # drawImage / PDFImageXObject in ReportLab 5.0.0 cannot
            # handle BytesIO (tries os.path.splitext on it).
            c.drawInlineImage(img,
                              cx - radius, cy - radius,
                              width=radius*2, height=radius*2)
            drawn_img = True
        except Exception:
            logger.debug("Failed to draw avatar image", exc_info=True)

    if not drawn_img:
        c.setFillColor(bg_color)
        c.circle(cx, cy, radius, fill=1, stroke=0)
        fs = radius * 0.85
        c.setFillColor(colors.white)
        c.setFont(FONT_BOLD, fs)
        c.drawCentredString(cx, cy - fs*0.33, label_char.upper()[:1])

    c.setStrokeColor(border_color)
    c.setLineWidth(0.75)
    c.circle(cx, cy, radius, stroke=1, fill=0)
    c.restoreState()


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def rrect(c, x, y, w, h, r, fill_color=None, stroke_color=None, lw=0.75):
    c.saveState()
    if fill_color:
        c.setFillColor(fill_color)
    if stroke_color:
        c.setStrokeColor(stroke_color)
        c.setLineWidth(lw)
    c.roundRect(x, y, w, h, r,
                fill=1 if fill_color else 0,
                stroke=1 if stroke_color else 0)
    c.restoreState()


def _wrap_lines(text, inner_w, font=FONT_REG, size=FONT_SIZE_MAIN):
    chars_per_line = max(1, int(inner_w / (size * 0.545)))
    lines = []
    for raw in str(text).split("\n"):
        if raw == "":
            lines.append("")
        else:
            lines.extend(textwrap.wrap(raw, chars_per_line) or [""])
    return lines


def _extract_time(ts_str):
    """Return just 'HH:MM AM/PM UTC' from timestamp string."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            length = len(datetime.utcnow().strftime(fmt))
            dt = datetime.strptime(ts_str[:length], fmt)
            return dt.strftime("%I:%M %p").lstrip("0") + " UTC"
        except Exception:
            pass
    # last-resort: grab last HH:MM-like token
    parts = ts_str.strip().split()
    for p in reversed(parts):
        if len(p) == 5 and p[2] == ":":
            return p + " UTC"
    return ts_str + " UTC" if ts_str else ts_str


def _extract_date(ts_str):
    for fmt, length in [
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d", 10),
        ("%d/%m/%Y %H:%M", 16),
    ]:
        try:
            dt = datetime.strptime(ts_str[:length], fmt)
            return dt.strftime("%B %d, %Y")
        except Exception:
            pass
    if len(ts_str) >= 10 and ts_str[4] == "-":
        return ts_str[:10]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# BUBBLE DIMENSIONS
# ─────────────────────────────────────────────────────────────────────────────

def bubble_dimensions(msg, bubble_w):
    """
    Returns (total_height, inner_w, has_reply, has_cmd_tag).
    """
    inner_w     = bubble_w - 2*PAD_H
    has_reply   = bool(msg.get("reply_preview"))
    has_cmd_tag = (msg.get("type") == "msg" and msg.get("is_command"))

    h = PAD_V

    if has_reply:
        h += REPLY_H + REPLY_PAD

    if has_cmd_tag:
        h += CMD_TAG_H + 2*mm

    lines = _wrap_lines(msg.get("data", ""), inner_w)
    h += len(lines) * LEADING

    # timestamp row
    h += FONT_SIZE_TS + 2.5
    h += PAD_V

    return h, inner_w, has_reply, has_cmd_tag


# ─────────────────────────────────────────────────────────────────────────────
# BUBBLE RENDERER
# ─────────────────────────────────────────────────────────────────────────────

def _bubble_colors(msg, is_self):
    sender = msg.get("sender", "")
    is_cmd = msg.get("is_command", False)
    msg_type = msg.get("type", "msg")

    # Leave messages always use leave colour (regardless of sender)
    if msg_type == "leave":
        return BUBBLE_LEAVE, BUBBLE_LEAVE_BDR
    # Bot / system messages use the same steel-blue colour
    if sender in ("bot", "system"):
        return BUBBLE_BOT, BUBBLE_BOT_BDR
    if msg_type == "complain":
        return BUBBLE_COMPLAIN, BUBBLE_COMPLAIN_BDR
    if is_cmd:
        return BUBBLE_COMMAND, BUBBLE_COMMAND_BDR
    if is_self:
        return BUBBLE_SELF, BUBBLE_SELF_BORDER
    return BUBBLE_OTHER, BUBBLE_OTHER_BORDER


def draw_bubble(c, msg, x, y, bubble_w, is_self):
    """
    Draw bubble with bottom-left corner at (x, y).
    Returns bubble height.
    """
    bh, inner_w, has_reply, has_cmd_tag = bubble_dimensions(msg, bubble_w)
    fill, bdr = _bubble_colors(msg, is_self)

    # Subtle drop shadow
    c.saveState()
    c.setFillColor(HexColor("#000000"))
    c.setFillAlpha(0.07)
    c.roundRect(x + 0.8, y - 0.8, bubble_w, bh, 3.5*mm, fill=1, stroke=0)
    c.restoreState()

    # Main bubble
    rrect(c, x, y, bubble_w, bh, 3.5*mm, fill_color=fill, stroke_color=bdr, lw=0.65)

    cur_y = y + bh - PAD_V   # descending write cursor

    # ── 1. Reply strip ────────────────────────────────────────────────────────
    if has_reply:
        rp      = msg["reply_preview"]
        sx      = x + PAD_H
        sy      = cur_y - REPLY_H
        sw      = inner_w
        rrect(c, sx, sy, sw, REPLY_H, 2*mm,
              fill_color=REPLY_BG, stroke_color=REPLY_BORDER, lw=0.55)
        # Accent bar
        c.saveState()
        c.setFillColor(XENTRA_GREEN_DARK)
        c.roundRect(sx, sy, 1.8*mm, REPLY_H, 0.9*mm, fill=1, stroke=0)
        c.restoreState()
        # Reply sender + snippet
        sender_label = str(rp.get("sender", "")).capitalize()
        snippet      = str(rp.get("data", ""))
        snippet      = snippet[:58] + ("\u2026" if len(snippet) > 58 else "")
        c.saveState()
        c.setFont(FONT_BOLD, FONT_SIZE_REPLY_LBL)
        c.setFillColor(XENTRA_GREEN_DARK)
        c.drawString(sx + 3*mm, sy + REPLY_H - 3.4*mm, sender_label)
        c.setFont(FONT_REG, FONT_SIZE_REPLY)
        c.setFillColor(XENTRA_GREY)
        c.drawString(sx + 3*mm, sy + 1.6*mm, snippet)
        c.restoreState()
        cur_y = sy - REPLY_PAD

    # ── 2. Command badge ──────────────────────────────────────────────────────
    if has_cmd_tag:
        cmd_text = f"/{msg.get('command_name', 'command')}"
        tw       = stringWidth(cmd_text, FONT_BOLD, FONT_SIZE_CMD_TAG) + 4.5*mm
        tx       = x + PAD_H
        ty       = cur_y - CMD_TAG_H
        rrect(c, tx, ty, tw, CMD_TAG_H, 1.4*mm,
              fill_color=HexColor("#6B84CC"), stroke_color=None)
        c.saveState()
        c.setFont(FONT_BOLD, FONT_SIZE_CMD_TAG)
        c.setFillColor(colors.white)
        c.drawString(tx + 2.2*mm, ty + 1.5*mm, cmd_text)
        c.restoreState()
        cur_y = ty - 2*mm

    # ── 3. Body text ──────────────────────────────────────────────────────────
    lines = _wrap_lines(msg.get("data", ""), inner_w)
    c.saveState()
    c.setFont(FONT_REG, FONT_SIZE_MAIN)
    c.setFillColor(MSG_TEXT_COLOR)
    for line in lines:
        cur_y -= LEADING
        c.drawString(x + PAD_H, cur_y, line)
    c.restoreState()

    # ── 4. Timestamp (bottom-right) ───────────────────────────────────────────
    ts_text = _extract_time(str(msg.get("timestamp", "")))
    c.saveState()
    c.setFont(FONT_REG, FONT_SIZE_TS)
    c.setFillColor(TS_COLOR)
    ts_w = stringWidth(ts_text, FONT_REG, FONT_SIZE_TS)
    c.drawString(x + bubble_w - PAD_H - ts_w, y + PAD_V - 0.5, ts_text)
    c.restoreState()

    return bh




# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE FLOWABLE
# ─────────────────────────────────────────────────────────────────────────────

class MessageFlowable(Flowable):
    """
    Renders one chat message: avatar (top-aligned with bubble) + bubble.

    If the bubble height exceeds the available frame height, ``split()``
    is called.  The first portion keeps the avatar + as many lines as
    fit; remaining lines are handed to ContinuationBubbleFlowable
    instances that appear on the next page(s).
    """

    def __init__(self, msg, viewer_role, width):
        super().__init__()
        self.msg         = msg
        self.viewer_role = viewer_role
        self._width      = width
        self._is_self    = msg.get("sender", "") == viewer_role
        self._bubble_w   = min(MAX_BUBBLE_W,
                               width * 0.70 - AVATAR_D - AVATAR_PAD)
        # Sanitise: ensure data is never None
        self.msg["data"] = self.msg.get("data") or ""
        self.msg["timestamp"] = self.msg.get("timestamp") or ""
        self._all_lines  = _wrap_lines(self.msg["data"],
                                       self._bubble_w - 2 * PAD_H)
        bh, _, _, _      = bubble_dimensions(self.msg, self._bubble_w)
        self._bh         = bh
        self.width       = width
        self.height      = bh + MSG_GAP

    # ------------------------------------------------------------------
    def wrap(self, avail_w, avail_h):
        return self.width, self.height

    # ------------------------------------------------------------------
    def split(self, avail_w, avail_h):
        """Cannot split — return [] so ReportLab pushes to the next page."""
        return []

    # ------------------------------------------------------------------
    def draw(self):
        c       = self.canv
        w       = self._width
        bw      = self._bubble_w
        bh      = self._bh
        msg     = self.msg
        is_self = self._is_self
        sender  = msg.get("sender", "bot")

        msg_type = msg.get("type", "msg")

        if sender == "bot":
            av_bg, av_bd = AV_COLOR_BOT
            av_label     = "X"
            av_img       = msg.get("bot_img") or msg.get("avatar_url")
        elif sender == "freelancer":
            av_bg, av_bd = AV_COLOR_FREELANCER
            av_label     = "F"
            av_img       = msg.get("avatar_url")
        elif sender == "client":
            av_bg, av_bd = AV_COLOR_CLIENT
            av_label     = "C"
            av_img       = msg.get("avatar_url")
        else:
            # Any unknown / system sender — use "X" with bot purple
            av_bg, av_bd = AV_COLOR_BOT
            av_label     = "X"
            av_img       = msg.get("avatar_url")

        bub_y = MSG_GAP
        av_cy = bub_y + bh - AVATAR_R

        if is_self:
            av_cx = w - AVATAR_R
            bub_x = w - AVATAR_D - AVATAR_PAD - bw
        else:
            av_cx = AVATAR_R
            bub_x = AVATAR_D + AVATAR_PAD

        draw_avatar(c, av_cx, av_cy, AVATAR_R, av_label, av_img, av_bg, av_bd)
        draw_bubble(c, msg, bub_x, bub_y, bw, is_self)



# ─────────────────────────────────────────────────────────────────────────────
# DATE DIVIDER FLOWABLE
# ─────────────────────────────────────────────────────────────────────────────

PILL_FONT_SIZE = 7.4
PILL_H         = 5.5 * mm
PILL_PAD_X     = 5.0 * mm   # horizontal padding inside pill

class DateDivider(Flowable):
    def __init__(self, label, width):
        super().__init__()
        self.label  = label
        self.width  = width
        # Allocate enough height: pill sits centred vertically
        self.height = PILL_H + 4*mm   # 2 mm breathing room top and bottom

    def draw(self):
        c   = self.canv
        w   = self.width

        # Pill dimensions
        pill_text_w = stringWidth(self.label, FONT_REG, PILL_FONT_SIZE)
        pill_w      = pill_text_w + 2 * PILL_PAD_X
        pill_x      = (w - pill_w) / 2      # horizontally centred
        pill_y      = 2*mm                  # bottom of pill (2 mm from flowable bottom)
        line_y      = pill_y + PILL_H / 2   # horizontal rule at pill mid-height

        c.saveState()

        # Lines
        c.setStrokeColor(XENTRA_LIGHT_GREY)
        c.setLineWidth(0.55)
        c.line(0,               line_y, pill_x - 2*mm,          line_y)
        c.line(pill_x + pill_w + 2*mm, line_y, w,               line_y)

        # Pill background
        rrect(c, pill_x, pill_y, pill_w, PILL_H,
              PILL_H / 2,                  # fully rounded ends
              fill_color=DATE_PILL_BG,
              stroke_color=None)

        # Centred text inside pill
        c.setFont(FONT_REG, PILL_FONT_SIZE)
        c.setFillColor(DATE_PILL_TEXT)
        # drawCentredString at horizontal centre, vertical centre of pill
        text_y = pill_y + (PILL_H - PILL_FONT_SIZE) / 2 - 0.5
        c.drawCentredString(w / 2, text_y, self.label)

        c.restoreState()


# ─────────────────────────────────────────────────────────────────────────────
# LEGEND FLOWABLE
# ─────────────────────────────────────────────────────────────────────────────

class LegendBlock(Flowable):
    ITEMS = [
        (BUBBLE_SELF,     BUBBLE_SELF_BORDER,  "Your message (viewer's side)"),
        (BUBBLE_OTHER,    BUBBLE_OTHER_BORDER,  "Other party's message"),
        (BUBBLE_COMPLAIN, BUBBLE_COMPLAIN_BDR,  "Complaint"),
        (BUBBLE_COMMAND,  BUBBLE_COMMAND_BDR,   "Command execution"),
        (BUBBLE_BOT,      BUBBLE_BOT_BDR,       "Bot message"),
        (BUBBLE_LEAVE,    BUBBLE_LEAVE_BDR,     "Room leave notice"),
    ]

    def __init__(self, width):
        super().__init__()
        self.width  = width
        self.height = len(self.ITEMS) * 8.5*mm + 4*mm

    def draw(self):
        c = self.canv
        y = self.height - 3*mm
        for fill, bdr, label in self.ITEMS:
            y -= 8.5*mm
            rrect(c, 0, y + 1*mm, 15*mm, 5.5*mm, 1.4*mm,
                  fill_color=fill, stroke_color=bdr, lw=0.55)
            c.setFont(FONT_REG, 8.0)
            c.setFillColor(XENTRA_GREY)
            c.drawString(17*mm, y + 2.2*mm, label)


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT ASSEMBLY
# ─────────────────────────────────────────────────────────────────────────────

class XTranscriptDoc(BaseDocTemplate):
    pass


def _sanitise_message(msg):
    """Ensure a message dict has all required fields with valid types."""
    if msg is None:
        msg = {}
    if not isinstance(msg, dict):
        msg = {"data": str(msg)}
    msg.setdefault("data", "")
    msg.setdefault("timestamp", "")
    msg.setdefault("sender", "system")
    msg.setdefault("type", "msg")
    msg.setdefault("is_command", False)
    msg.setdefault("command_name", "")
    msg.setdefault("reply_preview", None)
    msg.setdefault("avatar_url", None)
    msg.setdefault("left_by", "")
    msg.setdefault("reason", "")
    # Ensure string fields are actually strings
    if msg["data"] is None:
        msg["data"] = ""
    else:
        msg["data"] = str(msg["data"])
    if msg["timestamp"] is None:
        msg["timestamp"] = ""
    else:
        msg["timestamp"] = str(msg["timestamp"])
    if msg["sender"] is None:
        msg["sender"] = "system"
    else:
        msg["sender"] = str(msg["sender"])
    if msg["left_by"] is None:
        msg["left_by"] = ""
    else:
        msg["left_by"] = str(msg["left_by"])
    if msg["reason"] is None:
        msg["reason"] = ""
    else:
        msg["reason"] = str(msg["reason"])
    return msg


def build_transcript_pdf(data, output_path):
    viewer_role = data.get("viewer_role", "freelancer")
    raw_messages = data.get("messages") or []
    # Sanitise every message — None, missing keys, wrong types all handled
    messages = [_sanitise_message(m) for m in raw_messages if m is not None]
    data["_msg_count"] = len(messages)

    PageCtx.room_id       = data.get("room_id", "")
    PageCtx.transcript_id = data.get("transcript_id", "")
    PageCtx.watermark_b64 = data.get("watermark_b64", "")

    doc = XTranscriptDoc(
        output_path,
        pagesize=LETTER,
        leftMargin=CONTENT_ML, rightMargin=CONTENT_MR,
        topMargin=CONTENT_MT,  bottomMargin=CONTENT_MB,
        title=f"Xentra Chat Transcript - {data.get('transcript_id', '')}",
        author="Xentra",
        subject="Chat Transcript",
    )

    frame_title = Frame(0, 0, PAGE_W, PAGE_H, id="title_frame",
                        leftPadding=0, rightPadding=0,
                        topPadding=0,  bottomPadding=0)
    frame_inner = Frame(
        CONTENT_ML, CONTENT_MB,
        CONTENT_W,
        PAGE_H - CONTENT_MT - CONTENT_MB,
        id="inner_frame",
        leftPadding=0, rightPadding=0,
        topPadding=0,  bottomPadding=0,
    )

    def title_cb(c, d):
        render_title_page(c, data)

    doc.addPageTemplates([
        PageTemplate(id="Title", frames=[frame_title], onPage=title_cb),
        PageTemplate(id="Inner", frames=[frame_inner], onPage=on_inner_page),
    ])

    ss    = getSampleStyleSheet()
    sH1   = ParagraphStyle("TH1",  parent=ss["Heading1"],
                            fontName=FONT_BOLD, fontSize=10.5,
                            textColor=XENTRA_NAVY,
                            spaceBefore=4, spaceAfter=6)
    sBody = ParagraphStyle("TBod", parent=ss["Normal"],
                            fontName=FONT_REG, fontSize=8.4,
                            textColor=XENTRA_GREY, leading=12, spaceAfter=3)

    story = [NextPageTemplate("Inner"), PageBreak()]

    # Legend
    story.append(Paragraph("Message Type Legend", sH1))
    story.append(LegendBlock(CONTENT_W))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        f"Viewer: <b>{viewer_role.capitalize()}</b> \u2014 "
        "your messages appear on the <b>right</b>; all others on the <b>left</b>.",
        sBody
    ))
    story.append(Spacer(1, 3*mm))
    story.append(HRFlowable(width="100%", thickness=0.55,
                             color=XENTRA_LIGHT_GREY, spaceAfter=4*mm))

    # ── Pre-truncate long messages so no bubble exceeds the frame ──────
    bubble_w = min(MAX_BUBBLE_W, CONTENT_W * 0.70 - AVATAR_D - AVATAR_PAD)
    inner_w  = bubble_w - 2 * PAD_H
    for msg in messages:
        raw = msg.get("data", "")
        if not raw:
            continue
        lines = _wrap_lines(raw, inner_w)
        if len(lines) > MAX_BODY_LINES:
            msg["data"] = "\n".join(lines[:MAX_BODY_LINES]) + "\n[...]"

    last_date = None
    for msg in messages:
        ts_raw     = msg.get("timestamp", "")
        date_label = _extract_date(ts_raw)
        if date_label and date_label != last_date:
            story.append(Spacer(1, 2*mm))
            story.append(DateDivider(date_label, CONTENT_W))
            story.append(Spacer(1, 1.5*mm))
            last_date = date_label

        story.append(MessageFlowable(msg, viewer_role, CONTENT_W))

    doc.build(story, canvasmaker=pdfcanvas.Canvas)
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def generate_transcript(data, output_path=None):
    """
    Generate a transcript PDF.

    If *output_path* is provided, the PDF is written to that file and the
    path is returned.  If *output_path* is ``None`` (the default), the PDF
    is generated in memory and the raw bytes are returned — suitable for
    ``discord.File(io.BytesIO(...))``.
    """
    try:
        if output_path is None:
            buf = io.BytesIO()
            build_transcript_pdf(data, buf)
            return buf.getvalue()
        return build_transcript_pdf(data, output_path)
    except Exception:
        logger.exception("Unhandled error in transcript PDF generation")
        raise
