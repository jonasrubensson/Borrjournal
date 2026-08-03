from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..services.numrering import nummerlas_beroende
from ..models import Article, StockMovement, User
from ..schemas import iso_utc
from ..security import current_user, log_action, require_write

router = APIRouter(prefix="/api/articles", tags=["artiklar"])

ENHETER = ["st", "m", "kg", "l", "tim", "pkt", "rulle"]


class ArticleIn(BaseModel):
    name: str
    article_no: str = ""
    description: str = ""
    category: str = ""
    unit: str = "st"
    purchase_price: float = 0.0
    sales_price: float = 0.0
    vat_percent: float = 25.0
    track_stock: bool = True
    stock: float = 0.0
    min_stock: float = 0.0
    supplier: str = ""


def out(a: Article) -> dict:
    return {
        "id": a.id,
        "article_no": a.article_no,
        "name": a.name,
        "description": a.description,
        "category": a.category,
        "unit": a.unit,
        "purchase_price": a.purchase_price,
        "sales_price": a.sales_price,
        "vat_percent": a.vat_percent,
        "track_stock": a.track_stock,
        "stock": a.stock,
        "min_stock": a.min_stock,
        "supplier": a.supplier,
        "is_active": a.is_active,
        # Marginal i kronor och procent, så att en felprissatt artikel syns direkt
        "margin": round(a.sales_price - a.purchase_price, 2),
        "margin_percent": (
            round(100 * (a.sales_price - a.purchase_price) / a.sales_price, 1)
            if a.sales_price
            else None
        ),
        "low_stock": bool(a.track_stock and a.min_stock and a.stock <= a.min_stock),
        "created_at": iso_utc(a.created_at),
    }


async def _nasta_nummer(db: AsyncSession, kategori: str = "") -> str:
    from ..services.numrering import nasta_nummer, nummerlas_beroende

    prefix = "".join(c for c in (kategori or "ART").upper() if c.isalpha())[:3] or "ART"
    return await nasta_nummer(db, Article, Article.article_no, prefix, 1000)


@router.get("")
async def list_articles(
    q: str | None = None,
    category: str | None = None,
    low_stock: bool = False,
    include_inactive: bool = False,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Article).order_by(Article.category, Article.name)
    if not include_inactive:
        stmt = stmt.where(Article.is_active.is_(True))
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Article.name).like(needle),
                func.lower(Article.article_no).like(needle),
                func.lower(Article.description).like(needle),
                func.lower(Article.supplier).like(needle),
            )
        )
    if category:
        stmt = stmt.where(Article.category == category)

    rader = [out(a) for a in (await db.execute(stmt)).scalars().all()]
    if low_stock:
        rader = [a for a in rader if a["low_stock"]]
    return rader


@router.get("/summary")
async def summary(_: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    artiklar = (
        await db.execute(select(Article).where(Article.is_active.is_(True)))
    ).scalars().all()
    lagervarde = sum(
        (a.stock or 0) * (a.purchase_price or 0) for a in artiklar if a.track_stock
    )
    laga = [
        out(a) for a in artiklar if a.track_stock and a.min_stock and a.stock <= a.min_stock
    ]
    kategorier = sorted({a.category for a in artiklar if a.category})
    return {
        "antal": len(artiklar),
        "lagervarde": round(lagervarde, 2),
        "laga_saldon": laga,
        "kategorier": kategorier,
        "enheter": ENHETER,
    }


@router.post("", status_code=201)
async def create_article(
    payload: ArticleIn,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
    _las_=Depends(nummerlas_beroende),
):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Artikeln behöver ett namn")
    data = payload.model_dump()
    nummer = (data.pop("article_no") or "").strip()
    if nummer:
        taget = (
            await db.execute(select(Article.id).where(Article.article_no == nummer))
        ).first()
        if taget:
            raise HTTPException(status_code=409, detail="Artikelnumret finns redan")
    else:
        nummer = await _nasta_nummer(db, payload.category)

    startsaldo = data.pop("stock", 0.0) or 0.0
    a = Article(article_no=nummer, **data)
    a.stock = startsaldo
    db.add(a)
    await db.flush()
    if startsaldo:
        db.add(
            StockMovement(
                article_id=a.id,
                change=startsaldo,
                balance_after=startsaldo,
                reason="justering",
                note="Ingående saldo",
                by_user=user.full_name or user.username,
            )
        )
    await db.commit()
    await db.refresh(a)
    await log_action(
        db, "ARTICLE_CREATE", actor=user.username, object_type="article", object_id=a.id,
        request=request, detail=f"{a.article_no} {a.name}",
    )
    return out(a)


@router.patch("/{article_id}")
async def update_article(
    article_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    a = (await db.execute(select(Article).where(Article.id == article_id))).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Artikeln finns inte")

    if payload.get("article_no") and payload["article_no"] != a.article_no:
        taget = (
            await db.execute(select(Article.id).where(Article.article_no == payload["article_no"]))
        ).first()
        if taget:
            raise HTTPException(status_code=409, detail="Artikelnumret finns redan")
        a.article_no = payload["article_no"].strip()

    # Saldot ändras aldrig direkt, bara via lagerrörelser, annars går det inte
    # att förklara i efterhand varför saldot ser ut som det gör.
    for falt in ArticleIn.model_fields:
        if falt in payload and falt not in ("stock", "article_no"):
            setattr(a, falt, payload[falt])
    if "is_active" in payload:
        a.is_active = bool(payload["is_active"])

    await db.commit()
    await db.refresh(a)
    await log_action(
        db, "ARTICLE_UPDATE", actor=user.username, object_type="article", object_id=a.id,
        request=request, detail=a.article_no,
    )
    return out(a)


@router.post("/{article_id}/stock")
async def adjust_stock(
    article_id: str,
    payload: dict,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Justerar saldot. Antingen change (förändring) eller set_to (nytt saldo)."""
    a = (await db.execute(select(Article).where(Article.id == article_id))).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Artikeln finns inte")
    if not a.track_stock:
        raise HTTPException(status_code=400, detail="Artikeln lagerhålls inte")

    if "set_to" in payload and payload["set_to"] is not None:
        forandring = float(payload["set_to"]) - (a.stock or 0)
    elif payload.get("change") is not None:
        forandring = float(payload["change"])
    else:
        raise HTTPException(status_code=400, detail="Ange change eller set_to")
    if forandring == 0:
        return out(a)

    a.stock = round((a.stock or 0) + forandring, 3)
    db.add(
        StockMovement(
            article_id=a.id,
            change=forandring,
            balance_after=a.stock,
            reason=payload.get("reason") or ("inkop" if forandring > 0 else "justering"),
            note=(payload.get("note") or "")[:255],
            by_user=user.full_name or user.username,
        )
    )
    await db.commit()
    await db.refresh(a)
    await log_action(
        db, "STOCK_ADJUST", actor=user.username, object_type="article", object_id=a.id,
        request=request, detail=f"{a.article_no}: {forandring:+g} -> {a.stock:g}",
    )
    return out(a)


@router.get("/{article_id}/movements")
async def movements(
    article_id: str,
    limit: int = 50,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    rader = (
        await db.execute(
            select(StockMovement)
            .where(StockMovement.article_id == article_id)
            .order_by(StockMovement.at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "change": r.change,
            "balance_after": r.balance_after,
            "reason": r.reason,
            "note": r.note,
            "work_order_id": r.work_order_id,
            "at": iso_utc(r.at),
            "by_user": r.by_user,
        }
        for r in rader
    ]


@router.delete("/{article_id}", status_code=204)
async def delete_article(
    article_id: str,
    request: Request,
    user: User = Depends(require_write),
    db: AsyncSession = Depends(get_db),
):
    """Avaktiverar artikeln i stället för att radera, så att gamla order står kvar."""
    a = (await db.execute(select(Article).where(Article.id == article_id))).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Artikeln finns inte")
    a.is_active = False
    await db.commit()
    await log_action(
        db, "ARTICLE_DEACTIVATE", actor=user.username, object_type="article",
        object_id=article_id, request=request, detail=a.article_no,
    )
