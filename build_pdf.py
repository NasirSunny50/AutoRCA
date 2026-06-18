"""Render TECHNICAL_DOCUMENTATION.md into a professional, branded PDF report.

Pure-ReportLab (no system deps). Produces a cover page, running header/footer
with page numbers, styled headings, tables with auto-sized columns, code blocks,
lists and blockquotes.
"""
from __future__ import annotations

import re
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Preformatted, Spacer,
    Table, TableStyle, HRFlowable, KeepTogether, NextPageTemplate, PageBreak,
)

SRC = "TECHNICAL_DOCUMENTATION.md"
OUT = "TECHNICAL_DOCUMENTATION.pdf"

# ---- palette ---------------------------------------------------------------
INK      = colors.HexColor("#1f2733")
MUTED    = colors.HexColor("#5b6677")
ACCENT   = colors.HexColor("#4f46e5")
ACCENT_D = colors.HexColor("#3730a3")
LINE     = colors.HexColor("#e3e7ee")
CODE_BG  = colors.HexColor("#f5f6fa")
CODE_BD  = colors.HexColor("#e1e5ec")
TH_BG    = colors.HexColor("#4f46e5")
ROW_ALT  = colors.HexColor("#f6f7fb")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

# ---- character transliteration (built-in fonts lack box-drawing/arrows) -----
TRANS = {
    "─": "-", "━": "-", "│": "|", "┃": "|", "┄": "-", "┈": "-",
    "┌": "+", "┐": "+", "└": "+", "┘": "+", "├": "+", "┤": "+",
    "┬": "+", "┴": "+", "┼": "+",
    "╔": "+", "╗": "+", "╚": "+", "╝": "+", "║": "|", "═": "=",
    "►": ">", "▶": ">", "◄": "<", "◀": "<", "▼": "v", "▲": "^",
    "▸": ">", "▾": "v", "▹": ">",
    "→": "->", "←": "<-", "↑": "^", "↓": "v", "↦": "->",
    "✅": "[x]", "🔒": "[*]", "🟢": "*", "🟡": "*", "🔴": "*",
    "•": "-",
}


def tr(text: str) -> str:
    out = []
    for ch in text:
        if ch in TRANS:
            out.append(TRANS[ch])
            continue
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            out.append("")  # drop glyphs the base fonts can't show
    return "".join(out)


def inline(text: str) -> str:
    """Escape + apply **bold** and `code` for ReportLab paragraph markup."""
    text = tr(text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`",
                  r'<font face="Courier" size="8.5" color="#b3186d">\1</font>', text)
    return text


# ---- styles ----------------------------------------------------------------
ss = getSampleStyleSheet()

def style(name, **kw):
    kw.setdefault("parent", ss["Normal"])
    return ParagraphStyle(name, **kw)

BODY   = style("body", fontName="Helvetica", fontSize=9.7, leading=14.5,
               textColor=INK, alignment=TA_JUSTIFY, spaceAfter=7)
H2     = style("h2", fontName="Helvetica-Bold", fontSize=14.5, leading=18,
               textColor=ACCENT_D, spaceBefore=8, spaceAfter=6)
H3     = style("h3", fontName="Helvetica-Bold", fontSize=11.5, leading=15,
               textColor=INK, spaceBefore=8, spaceAfter=3)
BULLET = style("bullet", parent=BODY, leftIndent=16, bulletIndent=4,
               alignment=TA_LEFT, spaceAfter=3)
QUOTE  = style("quote", parent=BODY, leftIndent=12, textColor=MUTED,
               fontName="Helvetica-Oblique", alignment=TA_LEFT, borderPadding=(2, 2, 2, 8))
CODE   = style("code", fontName="Courier", fontSize=7.6, leading=9.6,
               textColor=colors.HexColor("#243049"), backColor=CODE_BG,
               borderColor=CODE_BD, borderWidth=0.6, borderPadding=7, spaceAfter=8)
TH     = style("th", fontName="Helvetica-Bold", fontSize=8.6, leading=11,
               textColor=colors.white)
TD     = style("td", fontName="Helvetica", fontSize=8.6, leading=11.5, textColor=INK)
TD_MONO= style("tdm", parent=TD, fontName="Courier", fontSize=8.0)

# cover styles
C_TITLE = style("ctitle", fontName="Helvetica-Bold", fontSize=46, leading=50,
                textColor=ACCENT_D, alignment=TA_LEFT)
C_SUB   = style("csub", fontName="Helvetica", fontSize=15, leading=20,
                textColor=INK, alignment=TA_LEFT)
C_TYPE  = style("ctype", fontName="Helvetica-Bold", fontSize=13, leading=18,
                textColor=ACCENT, alignment=TA_LEFT)
C_META  = style("cmeta", fontName="Helvetica", fontSize=10, leading=16, textColor=MUTED)
C_METAB = style("cmetab", fontName="Helvetica-Bold", fontSize=10, leading=16, textColor=INK)


# ---- markdown parsing ------------------------------------------------------
def parse(md: str):
    lines = md.split("\n")
    flow = []
    i = 0
    started = False  # skip the title block before the first "## "
    n = len(lines)

    def cell_para(txt, header=False, mono=False):
        st = TH if header else (TD_MONO if mono else TD)
        return Paragraph(inline(txt), st)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not started:
            if stripped.startswith("## "):
                started = True
            else:
                i += 1
                continue

        # code fence
        if stripped.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(tr(lines[i]))
                i += 1
            i += 1  # closing fence
            flow.append(Preformatted("\n".join(buf) or " ", CODE))
            continue

        # headings
        if stripped.startswith("### "):
            flow.append(Paragraph(inline(stripped[4:]), H3))
            i += 1
            continue
        if stripped.startswith("## "):
            flow.append(Spacer(1, 4))
            flow.append(Paragraph(inline(stripped[3:]), H2))
            flow.append(HRFlowable(width="100%", thickness=1.1, color=ACCENT,
                                   spaceBefore=1, spaceAfter=7, lineCap="round"))
            i += 1
            continue
        if stripped.startswith("# "):
            i += 1
            continue

        # horizontal rule
        if stripped in ("---", "***", "___"):
            flow.append(HRFlowable(width="100%", thickness=0.5, color=LINE,
                                   spaceBefore=4, spaceAfter=6))
            i += 1
            continue

        # table (consecutive | ... | lines)
        if stripped.startswith("|") and i + 1 < n and set(lines[i + 1].strip()) <= set("|-: "):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            flow.append(build_table(rows, cell_para))
            flow.append(Spacer(1, 6))
            continue

        # blockquote
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            flow.append(Paragraph(inline(" ".join(buf)), QUOTE))
            flow.append(Spacer(1, 3))
            continue

        # bullet list
        if re.match(r"^[-*]\s+", stripped):
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                item = re.sub(r"^[-*]\s+", "", lines[i].strip())
                flow.append(Paragraph(inline(item), BULLET, bulletText="•"))
                i += 1
            flow.append(Spacer(1, 3))
            continue

        # numbered list
        if re.match(r"^\d+\.\s+", stripped):
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                m = re.match(r"^(\d+)\.\s+(.*)", lines[i].strip())
                flow.append(Paragraph(inline(m.group(2)), BULLET, bulletText=m.group(1) + "."))
                i += 1
            flow.append(Spacer(1, 3))
            continue

        # blank
        if not stripped:
            i += 1
            continue

        # paragraph (gather until blank/structural)
        buf = [stripped]
        i += 1
        while i < n:
            s = lines[i].strip()
            if (not s or s.startswith(("#", "|", ">", "```", "---"))
                    or re.match(r"^[-*]\s+", s) or re.match(r"^\d+\.\s+", s)):
                break
            buf.append(s)
            i += 1
        flow.append(Paragraph(inline(" ".join(buf)), BODY))

    return flow


def build_table(rows, cell_para):
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    body = [[c.strip() for c in r.strip("|").split("|")] for r in rows[2:]]
    ncol = len(header)

    # auto column widths from content length (capped), last col gets extra weight
    weights = []
    for c in range(ncol):
        maxlen = len(header[c])
        for r in body:
            if c < len(r):
                maxlen = max(maxlen, len(r[c]))
        weights.append(min(max(maxlen, 4), 46))
    total = sum(weights) or 1
    widths = [CONTENT_W * w / total for w in widths_guard(weights)]

    data = [[cell_para(h, header=True) for h in header]]
    for r in body:
        r = (r + [""] * ncol)[:ncol]
        mono = [is_mono(x) for x in r]
        data.append([cell_para(x, mono=mono[k]) for k, x in enumerate(r)])

    t = Table(data, colWidths=widths, repeatRows=1)
    ts = [
        ("BACKGROUND", (0, 0), (-1, 0), TH_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT_D),
        ("GRID", (0, 1), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
    ]
    t.setStyle(TableStyle(ts))
    return t


def widths_guard(weights):
    return weights


def is_mono(text):
    t = text.strip().strip("`")
    return bool(re.match(r"^[\w./:_\-]+$", t)) and (
        "." in t or "/" in t or "_" in t) and " " not in t and len(t) > 2


# ---- cover -----------------------------------------------------------------
def cover():
    el = [Spacer(1, 70)]
    el.append(Paragraph("AutoRCA", C_TITLE))
    el.append(Spacer(1, 6))
    el.append(Paragraph("Automated Log Monitoring &amp; Error Analysis System", C_SUB))
    el.append(Spacer(1, 26))
    el.append(HRFlowable(width="38%", thickness=3, color=ACCENT, spaceAfter=20,
                         hAlign="LEFT", lineCap="round"))
    el.append(Paragraph("TECHNICAL DOCUMENTATION", C_TYPE))
    el.append(Spacer(1, 150))

    meta = [
        ["Organization", "Kona Software Lab LTD"],
        ["Document", "Technical Documentation"],
        ["Version", "1.0"],
        ["Date", date.today().strftime("%d %B %Y")],
        ["Classification", "Confidential"],
    ]
    data = [[Paragraph(k, C_META), Paragraph(v, C_METAB)] for k, v in meta]
    t = Table(data, colWidths=[4.2 * cm, 9 * cm])
    t.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.6, LINE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    el.append(t)
    el.append(NextPageTemplate("body"))
    el.append(PageBreak())
    return el


# ---- page decoration -------------------------------------------------------
def draw_cover_page(canvas, doc):
    canvas.saveState()
    # top accent band
    canvas.setFillColor(ACCENT)
    canvas.rect(0, PAGE_H - 14 * mm, PAGE_W, 14 * mm, stroke=0, fill=1)
    canvas.setFillColor(ACCENT_D)
    canvas.rect(0, PAGE_H - 16 * mm, PAGE_W, 2 * mm, stroke=0, fill=1)
    # bottom band
    canvas.setFillColor(ACCENT)
    canvas.rect(0, 0, PAGE_W, 9 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(PAGE_W / 2, 3.2 * mm,
                             "AutoRCA  -  Automated Log Monitoring & Error Analysis  -  Kona Software Lab LTD")
    canvas.restoreState()


def draw_body_page(canvas, doc):
    canvas.saveState()
    # header
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, PAGE_H - 11 * mm, "AutoRCA - Technical Documentation")
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 11 * mm, "Kona Software Lab LTD")
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, PAGE_H - 12.5 * mm, PAGE_W - MARGIN, PAGE_H - 12.5 * mm)
    # footer
    canvas.line(MARGIN, 12 * mm, PAGE_W - MARGIN, 12 * mm)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 8.5 * mm, "Confidential")
    canvas.drawCentredString(PAGE_W / 2, 8.5 * mm, "Kona Software Lab LTD")
    canvas.drawRightString(PAGE_W - MARGIN, 8.5 * mm, "Page %d" % (doc.page - 1))
    canvas.restoreState()


def build():
    with open(SRC, encoding="utf-8") as fh:
        md = fh.read()

    doc = BaseDocTemplate(OUT, pagesize=A4,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=20 * mm, bottomMargin=16 * mm,
                          title="AutoRCA - Technical Documentation",
                          author="Kona Software Lab LTD")

    cover_frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="cover")
    body_frame = Frame(MARGIN, 15 * mm, CONTENT_W, PAGE_H - 35 * mm, id="body")

    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=draw_cover_page),
        PageTemplate(id="body", frames=[body_frame], onPage=draw_body_page),
    ])

    story = cover() + parse(md)
    doc.build(story)
    print("Wrote", OUT)


if __name__ == "__main__":
    build()
