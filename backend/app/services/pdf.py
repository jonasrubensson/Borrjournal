"""Genererar offert och arbetsorder som PDF.

Byggd med reportlab direkt i stället för en HTML-till-PDF-motor, eftersom den
senare kräver en webbläsare i containern. En offert är ett enkelt dokument och
tjänar inte på ett helt renderingslager.

Företagsuppgifterna hämtas från inställningarna, så att samma mall fungerar för
vem som helst utan att något är hårdkodat.
"""

from __future__ import annotations

import io
import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

BLACK = colors.HexColor("#0E1F2A")
INK2 = colors.HexColor("#33505E")
STONE = colors.HexColor("#6B7A80")
LINE = colors.HexColor("#D3DBDC")
WATER = colors.HexColor("#1F7A8C")

MARGIN = 20 * mm
BREDD, HOJD = A4


def kr(varde: float) -> str:
    """Svenskt talformat: mellanslag som tusenavskiljare, komma som decimal."""
    text = f"{varde:,.2f}".replace(",", " ").replace(".", ",")
    return text


def antal(varde: float) -> str:
    if abs(varde - round(varde)) < 0.005:
        return str(int(round(varde)))
    return f"{varde:.2f}".replace(".", ",")


def summera(rader: list[dict], rabatt_procent: float = 0.0) -> dict:
    netto = 0.0
    moms = 0.0
    for r in rader:
        radsumma = r["quantity"] * r["unit_price"]
        radsumma *= 1 - (r.get("discount_percent") or 0) / 100
        netto += radsumma
    netto *= 1 - (rabatt_procent or 0) / 100
    for r in rader:
        radsumma = r["quantity"] * r["unit_price"]
        radsumma *= 1 - (r.get("discount_percent") or 0) / 100
        radsumma *= 1 - (rabatt_procent or 0) / 100
        moms += radsumma * (r.get("vat_percent") or 0) / 100
    return {
        "netto": round(netto, 2),
        "moms": round(moms, 2),
        "brutto": round(netto + moms, 2),
    }


class _Sida:
    def __init__(self, c: canvas.Canvas, foretag: dict, typ: str, nummer: str):
        self.c = c
        self.y = HOJD - MARGIN
        self.foretag = foretag
        self.typ = typ
        self.nummer = nummer

    def sidfot(self) -> None:
        self.c.setFont("Helvetica", 7.5)
        self.c.setFillColor(STONE)
        self.c.drawString(
            MARGIN, 12 * mm, f"{self.foretag.get('namn', '')} · {self.typ} {self.nummer}"
        )
        self.c.drawRightString(BREDD - MARGIN, 12 * mm, f"Sida {self.c.getPageNumber()}")

    def plats(self, behov: float) -> None:
        """Ny sida när det som ska ritas inte får plats. Sidfoten följer med."""
        if self.y - behov < MARGIN + 14 * mm:
            self.sidfot()
            self.c.showPage()
            self.y = HOJD - MARGIN

    def text(self, txt: str, x: float, storlek: float = 9, font: str = "Helvetica", farg=BLACK):
        self.c.setFont(font, storlek)
        self.c.setFillColor(farg)
        self.c.drawString(x, self.y, txt)

    def hoger(self, txt: str, x: float, storlek: float = 9, font: str = "Helvetica", farg=BLACK):
        self.c.setFont(font, storlek)
        self.c.setFillColor(farg)
        self.c.drawRightString(x, self.y, txt)


def _stycke(c: canvas.Canvas, text: str, x: float, y: float, bredd: float, storlek=9) -> float:
    stil = ParagraphStyle(
        "brod", fontName="Helvetica", fontSize=storlek, leading=storlek * 1.45, textColor=INK2
    )
    p = Paragraph(text.replace("\n", "<br/>"), stil)
    _, h = p.wrap(bredd, 400)
    p.drawOn(c, x, y - h)
    return h


def bygg_pdf(
    *,
    typ: str,
    nummer: str,
    titel: str,
    foretag: dict,
    mottagare: dict,
    rader: list[dict],
    intro: str = "",
    villkor: str = "",
    datum: str = "",
    giltig_till: str = "",
    rabatt_procent: float = 0.0,
    referens: str = "",
    fotnot: str = "",
) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    c.setTitle(f"{typ} {nummer}")
    c.setAuthor(foretag.get("namn", "Borrjournal"))
    s = _Sida(c, foretag, typ, nummer)

    # ---- huvud ----
    logo_hojd = 0.0
    logotyp = foretag.get("logotyp") or ""
    if logotyp and os.path.exists(logotyp):
        try:
            from reportlab.lib.utils import ImageReader

            bild = ImageReader(logotyp)
            bw, bh = bild.getSize()
            # Max 22 mm hög och 60 mm bred, proportionerna behålls
            skala = min(22 * mm / bh, 60 * mm / bw)
            logo_hojd = bh * skala
            c.drawImage(
                bild,
                MARGIN,
                s.y - logo_hojd + 4 * mm,
                width=bw * skala,
                height=logo_hojd,
                mask="auto",
            )
        except Exception:  # noqa: BLE001 - en trasig logotyp får inte fälla dokumentet
            logo_hojd = 0.0

    c.setFillColor(BLACK)
    c.setFont("Helvetica-Bold", 20)
    if logo_hojd:
        # Dokumenttypen till höger när logotypen tar vänstra sidan
        c.drawRightString(BREDD - MARGIN, s.y - 4, typ.upper())
        c.setFont("Helvetica", 10)
        c.setFillColor(STONE)
        c.drawRightString(BREDD - MARGIN, s.y - 11 * mm, f"Nr {nummer}")
        s.y -= max(logo_hojd, 14 * mm) - 2 * mm
    else:
        c.drawString(MARGIN, s.y - 4, typ.upper())
        c.setFont("Helvetica", 10)
        c.setFillColor(STONE)
        c.drawRightString(BREDD - MARGIN, s.y, f"Nr {nummer}")
    s.y -= 8 * mm

    c.setStrokeColor(WATER)
    c.setLineWidth(2)
    c.line(MARGIN, s.y, BREDD - MARGIN, s.y)
    s.y -= 8 * mm

    # avsändare till vänster, mottagare till höger
    topp = s.y
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(BLACK)
    c.drawString(MARGIN, s.y, foretag.get("namn", ""))
    s.y -= 4.6 * mm
    c.setFont("Helvetica", 8.5)
    c.setFillColor(INK2)
    for rad in [
        foretag.get("adress", ""),
        f"{foretag.get('postnr', '')} {foretag.get('ort', '')}".strip(),
        foretag.get("telefon", ""),
        foretag.get("epost", ""),
        (f"Org.nr {foretag['orgnr']}" if foretag.get("orgnr") else ""),
        ("Godkänd för F-skatt" if foretag.get("f_skatt") else ""),
    ]:
        if rad.strip():
            c.drawString(MARGIN, s.y, rad)
            s.y -= 4.2 * mm

    hy = topp
    c.setFont("Helvetica", 8)
    c.setFillColor(STONE)
    c.drawString(BREDD / 2, hy, "MOTTAGARE")
    hy -= 5 * mm
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(BLACK)
    c.drawString(BREDD / 2, hy, mottagare.get("namn", ""))
    hy -= 4.6 * mm
    c.setFont("Helvetica", 8.5)
    c.setFillColor(INK2)
    for rad in [
        mottagare.get("adress", ""),
        mottagare.get("fastighet", ""),
        mottagare.get("telefon", ""),
        mottagare.get("epost", ""),
    ]:
        if (rad or "").strip():
            c.drawString(BREDD / 2, hy, rad)
            hy -= 4.2 * mm

    s.y = min(s.y, hy) - 6 * mm

    # ---- faktarad ----
    fakta = [("Datum", datum or date.today().isoformat())]
    if giltig_till:
        fakta.append(("Giltig till", giltig_till))
    if referens:
        fakta.append(("Referens", referens))
    x = MARGIN
    for etikett, varde in fakta:
        c.setFont("Helvetica", 7.5)
        c.setFillColor(STONE)
        c.drawString(x, s.y, etikett.upper())
        c.setFont("Helvetica", 9)
        c.setFillColor(BLACK)
        c.drawString(x, s.y - 4.5 * mm, varde)
        x += 45 * mm
    s.y -= 12 * mm

    if titel:
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(BLACK)
        c.drawString(MARGIN, s.y, titel)
        s.y -= 7 * mm

    if intro.strip():
        h = _stycke(c, intro, MARGIN, s.y, BREDD - 2 * MARGIN)
        s.y -= h + 6 * mm

    # ---- tabellhuvud ----
    kol_benamning = MARGIN
    kol_antal = MARGIN + 96 * mm
    kol_enhet = MARGIN + 112 * mm
    kol_pris = MARGIN + 140 * mm
    kol_summa = BREDD - MARGIN

    def rita_huvud():
        c.setFillColor(colors.HexColor("#F4F7F7"))
        c.rect(MARGIN - 2 * mm, s.y - 2 * mm, BREDD - 2 * MARGIN + 4 * mm, 7 * mm, fill=1, stroke=0)
        c.setFont("Helvetica", 7.5)
        c.setFillColor(STONE)
        c.drawString(kol_benamning, s.y, "BENÄMNING")
        c.drawRightString(kol_antal, s.y, "ANTAL")
        c.drawString(kol_enhet, s.y, "ENHET")
        c.drawRightString(kol_pris, s.y, "À-PRIS")
        c.drawRightString(kol_summa, s.y, "SUMMA")
        s.y -= 6 * mm

    rita_huvud()

    grupper = [("material", "Material"), ("arbete", "Arbete"), ("ovrigt", "Övrigt")]
    for nyckel, rubrik in grupper:
        i_grupp = [r for r in rader if (r.get("kind") or "material") == nyckel]
        if not i_grupp:
            continue
        s.plats(14 * mm)
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(WATER)
        c.drawString(kol_benamning, s.y, rubrik.upper())
        s.y -= 5.5 * mm

        for r in i_grupp:
            s.plats(12 * mm)
            radsumma = r["quantity"] * r["unit_price"] * (1 - (r.get("discount_percent") or 0) / 100)
            c.setFont("Helvetica", 9)
            c.setFillColor(BLACK)
            namn = r["name"]
            if r.get("article_no"):
                namn = f"{r['article_no']}  {namn}"
            c.drawString(kol_benamning, s.y, namn[:58])
            c.drawRightString(kol_antal, s.y, antal(r["quantity"]))
            c.setFillColor(INK2)
            c.drawString(kol_enhet, s.y, (r.get("unit") or "st")[:8])
            c.drawRightString(kol_pris, s.y, kr(r["unit_price"]))
            c.setFillColor(BLACK)
            c.drawRightString(kol_summa, s.y, kr(radsumma))
            s.y -= 4.6 * mm

            if r.get("note"):
                c.setFont("Helvetica-Oblique", 7.5)
                c.setFillColor(STONE)
                c.drawString(kol_benamning + 3 * mm, s.y, r["note"][:80])
                s.y -= 4 * mm
            if r.get("discount_percent"):
                c.setFont("Helvetica", 7.5)
                c.setFillColor(STONE)
                c.drawRightString(kol_summa, s.y, f"rabatt {antal(r['discount_percent'])} %")
                s.y -= 4 * mm

            s.y -= 1.4 * mm
            c.setStrokeColor(LINE)
            c.setLineWidth(0.4)
            c.line(MARGIN, s.y, BREDD - MARGIN, s.y)
            s.y -= 4 * mm

    # ---- summering ----
    s.plats(34 * mm)
    total = summera(rader, rabatt_procent)
    s.y -= 2 * mm
    x_etikett = BREDD - MARGIN - 60 * mm

    if rabatt_procent:
        c.setFont("Helvetica", 9)
        c.setFillColor(INK2)
        c.drawString(x_etikett, s.y, f"Rabatt {antal(rabatt_procent)} %")
        s.y -= 5.5 * mm

    for etikett, varde, fet in [
        ("Netto", total["netto"], False),
        ("Moms", total["moms"], False),
    ]:
        c.setFont("Helvetica-Bold" if fet else "Helvetica", 9)
        c.setFillColor(INK2)
        c.drawString(x_etikett, s.y, etikett)
        c.setFillColor(BLACK)
        c.drawRightString(kol_summa, s.y, f"{kr(varde)} kr")
        s.y -= 5.5 * mm

    s.y -= 1 * mm
    c.setStrokeColor(BLACK)
    c.setLineWidth(1)
    c.line(x_etikett, s.y + 3 * mm, BREDD - MARGIN, s.y + 3 * mm)
    s.y -= 2 * mm
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(BLACK)
    c.drawString(x_etikett, s.y, "Att betala")
    c.drawRightString(kol_summa, s.y, f"{kr(total['brutto'])} kr")
    s.y -= 10 * mm

    if villkor.strip():
        s.plats(30 * mm)
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(STONE)
        c.drawString(MARGIN, s.y, "VILLKOR")
        s.y -= 5 * mm
        h = _stycke(c, villkor, MARGIN, s.y, BREDD - 2 * MARGIN, storlek=8)
        s.y -= h + 4 * mm

    if fotnot.strip():
        s.plats(16 * mm)
        h = _stycke(c, fotnot, MARGIN, s.y, BREDD - 2 * MARGIN, storlek=7.5)
        s.y -= h

    s.sidfot()
    c.save()
    buffer.seek(0)
    return buffer.read()
