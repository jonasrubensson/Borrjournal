"""Sätter ihop den signerade PDF:en.

Originalet lämnas orört och en revisionssida läggs sist. Den sidan är det som
har bevisvärde: vem som signerade, varifrån, när, vad som visades och att
ingenting ändrats sedan dess.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone

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
GRON = colors.HexColor("#2E7D5B")
MARGIN = 18 * mm
BREDD, HOJD = A4


def _lokal(tid: datetime) -> str:
    if tid.tzinfo is None:
        tid = tid.replace(tzinfo=timezone.utc)
    return tid.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def bygg_revisionssida(
    *,
    referens: str,
    rubrik: str,
    avsandare: str,
    mottagare_epost: str,
    mottagare_namn: str,
    belopp_text: str,
    pdf_hash: str,
    signerad_at: datetime,
    handelser: list,
    kedja_ok: bool,
    kedja_text: str,
    namnteckning_png: bytes | None = None,
    tidsstampel: dict | None = None,
    egen_forklaring: str = "",
    filnamn_original: str = "dokumentet",
) -> bytes:
    buffert = io.BytesIO()
    c = canvas.Canvas(buffert, pagesize=A4)
    c.setTitle(f"Signeringsbevis {referens}")
    y = HOJD - MARGIN

    c.setFillColor(BLACK)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(MARGIN, y, "SIGNERINGSBEVIS")
    c.setFont("Helvetica", 9)
    c.setFillColor(STONE)
    c.drawRightString(BREDD - MARGIN, y, referens)
    y -= 6 * mm
    c.setStrokeColor(GRON)
    c.setLineWidth(2)
    c.line(MARGIN, y, BREDD - MARGIN, y)
    y -= 9 * mm

    c.setFont("Helvetica", 9.5)
    c.setFillColor(INK2)
    stil = ParagraphStyle("t", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK2)
    p = Paragraph(
        f"Dokumentet <b>{rubrik or referens}</b>"
        + (f" på {belopp_text}" if belopp_text else "")
        + f" godkändes elektroniskt av <b>{mottagare_epost}</b> "
        + f"den {_lokal(signerad_at)}."
        + " Mottagaren styrkte tillgången till e-postadressen med en engångskod"
        " innan dokumentet kunde godkännas.",
        stil,
    )
    _, h = p.wrap(BREDD - 2 * MARGIN, 60 * mm)
    p.drawOn(c, MARGIN, y - h)
    y -= h + 7 * mm

    # ---- fakta ----
    fakta = [
        ("Dokument", rubrik or referens),
        ("Referens", referens),
        ("Avsändare", avsandare),
        ("Godkänt av", f"{mottagare_namn} <{mottagare_epost}>".strip()),
        ("Tidpunkt", _lokal(signerad_at)),
        (f"SHA-256 av {filnamn_original}", pdf_hash),
    ]
    if tidsstampel:
        fakta.append(("Extern tidsstämpel", tidsstampel.get("tjanst", "")))
    for etikett, varde in fakta:
        if not varde:
            continue
        c.setFont("Helvetica", 7.5)
        c.setFillColor(STONE)
        c.drawString(MARGIN, y, etikett.upper())
        c.setFont("Courier" if etikett.startswith("SHA-256") else "Helvetica", 9)
        c.setFillColor(BLACK)
        rad = str(varde)
        if etikett.startswith("SHA-256") and len(rad) > 48:
            # Hela summan ska stå utskriven, annars går den inte att jämföra
            c.drawString(MARGIN + 52 * mm, y, rad[:32])
            y -= 4.2 * mm
            c.drawString(MARGIN + 52 * mm, y, rad[32:])
        else:
            c.drawString(MARGIN + 52 * mm, y, rad if len(rad) < 78 else rad[:78])
        y -= 5.6 * mm
    y -= 4 * mm

    # ---- namnteckning ----
    if namnteckning_png:
        try:
            from reportlab.lib.utils import ImageReader

            bild = ImageReader(io.BytesIO(namnteckning_png))
            bw, bh = bild.getSize()
            skala = min(70 * mm / bw, 22 * mm / bh)
            c.setFont("Helvetica", 7.5)
            c.setFillColor(STONE)
            c.drawString(MARGIN, y, "NAMNTECKNING")
            y -= 3 * mm
            c.drawImage(
                bild, MARGIN, y - bh * skala, width=bw * skala, height=bh * skala, mask="auto"
            )
            y -= bh * skala + 3 * mm
            c.setStrokeColor(LINE)
            c.setLineWidth(0.6)
            c.line(MARGIN, y, MARGIN + 70 * mm, y)
            y -= 8 * mm
        except Exception:  # noqa: BLE001
            pass

    # ---- händelser ----
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(BLACK)
    c.drawString(MARGIN, y, "Händelseförlopp")
    y -= 6 * mm

    c.setFillColor(colors.HexColor("#F4F7F7"))
    c.rect(MARGIN - 2 * mm, y - 2 * mm, BREDD - 2 * MARGIN + 4 * mm, 6 * mm, fill=1, stroke=0)
    c.setFont("Helvetica", 7)
    c.setFillColor(STONE)
    c.drawString(MARGIN, y, "NR")
    c.drawString(MARGIN + 9 * mm, y, "TIDPUNKT")
    c.drawString(MARGIN + 47 * mm, y, "HÄNDELSE")
    c.drawString(MARGIN + 120 * mm, y, "IP-ADRESS")
    y -= 5.5 * mm

    for h_ in handelser:
        if y < MARGIN + 40 * mm:
            c.showPage()
            y = HOJD - MARGIN
        c.setFont("Helvetica", 8)
        c.setFillColor(BLACK)
        c.drawString(MARGIN, y, str(h_.lopnummer))
        c.setFont("Courier", 7.5)
        c.drawString(MARGIN + 9 * mm, y, _lokal(h_.at)[:19])
        c.setFont("Helvetica", 8)
        c.setFillColor(INK2)
        c.drawString(MARGIN + 47 * mm, y, h_.beskrivning[:52])
        c.setFont("Courier", 7.5)
        c.drawString(MARGIN + 120 * mm, y, (h_.ip or "—")[:24])
        y -= 4.4 * mm
        if h_.webblasare:
            c.setFont("Helvetica", 6.5)
            c.setFillColor(STONE)
            c.drawString(MARGIN + 47 * mm, y, h_.webblasare[:88])
            y -= 3.6 * mm
        y -= 0.8 * mm

    y -= 4 * mm
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(MARGIN, y, BREDD - MARGIN, y)
    y -= 6 * mm

    # ---- förklaring ----
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(BLACK)
    c.drawString(MARGIN, y, "Om beviset")
    y -= 4.5 * mm
    forklaring = egen_forklaring or (
        f"Kontrollsumman ovan gäller filen {filnamn_original}, som bifogas kvittensen till "
        "mottagaren. Det är den handling som visades och godkändes. Jämför med "
        "<font face='Courier'>certutil -hashfile "
        f"{filnamn_original} SHA256</font> i Windows eller "
        f"<font face='Courier'>shasum -a 256 {filnamn_original}</font> i macOS och Linux. "
        "Varje post i händelseförloppet innehåller dessutom en kontrollsumma av den föregående, "
        "så att en ändring i efterhand bryter kedjan och går att upptäcka. "
        "Signaturen är en enkel elektronisk signatur enligt eIDAS-förordningen. Mottagarens "
        "tillgång till e-postadressen är styrkt med engångskod, men identiteten är inte "
        "kontrollerad mot legitimation."
    )
    forklaring = forklaring.replace("{kedja}", kedja_text) + (
        "" if "{kedja}" in (egen_forklaring or "") else f" <b>{kedja_text}</b>"
    )
    stil2 = ParagraphStyle("f", fontName="Helvetica", fontSize=7.5, leading=10.5, textColor=INK2)
    p2 = Paragraph(forklaring, stil2)
    _, h2 = p2.wrap(BREDD - 2 * MARGIN, 60 * mm)
    p2.drawOn(c, MARGIN, y - h2)

    c.setFont("Helvetica", 7)
    c.setFillColor(GRON if kedja_ok else colors.HexColor("#A6402F"))
    c.drawString(MARGIN, 12 * mm, "Kedjan verifierad" if kedja_ok else "VARNING: kedjan bruten")
    c.setFillColor(STONE)
    c.drawRightString(BREDD - MARGIN, 12 * mm, f"Signeringsbevis {referens}")

    c.save()
    buffert.seek(0)
    return buffert.read()


def _sidfot(text: str, antal_sidor: int) -> bytes:
    """Bygger en genomskinlig sidfot att lägga över varje sida i originalet."""
    buffert = io.BytesIO()
    c = canvas.Canvas(buffert, pagesize=A4)
    for _ in range(antal_sidor):
        c.setStrokeColor(GRON)
        c.setLineWidth(0.8)
        c.line(MARGIN, 10 * mm, BREDD - MARGIN, 10 * mm)
        c.setFont("Helvetica", 7)
        c.setFillColor(GRON)
        c.drawString(MARGIN, 6.5 * mm, text[:120])
        c.showPage()
    c.save()
    buffert.seek(0)
    return buffert.read()


def foga_ihop(
    original: bytes,
    bevis: bytes,
    *,
    sidfot: str = "",
) -> bytes:
    """Lägger revisionssidan sist, och en signaturrad längst ned på varje sida.

    Utan sidfoten ser originalsidorna likadana ut som den osignerade offerten.
    Skriver någon ut en enskild sida syns det inte att den är godkänd, och det
    är oftast just en sida man har framför sig.
    """
    from pypdf import PdfReader, PdfWriter

    las = PdfReader(io.BytesIO(original))
    ut = PdfWriter()
    overlagg = None
    if sidfot:
        overlagg = PdfReader(io.BytesIO(_sidfot(sidfot, len(las.pages))))
    for i, sida in enumerate(las.pages):
        if overlagg is not None and i < len(overlagg.pages):
            try:
                sida.merge_page(overlagg.pages[i])
            except Exception:  # noqa: BLE001
                pass
        ut.add_page(sida)
    for sida in PdfReader(io.BytesIO(bevis)).pages:
        ut.add_page(sida)
    buffert = io.BytesIO()
    ut.write(buffert)
    buffert.seek(0)
    return buffert.read()


def hasha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
