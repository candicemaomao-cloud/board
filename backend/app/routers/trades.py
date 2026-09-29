from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Tag, TradeLog, User
from app.schemas import TagOut, TradeCreate, TradeOut, TradeUpdate
from app.services.auth import require_user
from app.services.import_trades import ImportError_, import_text
from app.services.tags import get_or_create_tags

router = APIRouter()


def to_out(trade: TradeLog) -> TradeOut:
    return TradeOut(
        id=trade.id,
        date=trade.date,
        symbol=trade.symbol,
        side=trade.side,
        pnl_amount=trade.pnl_amount,
        pnl_pct=trade.pnl_pct,
        tags=[t.name for t in trade.tags],
        notes=trade.notes,
        source=trade.source or "manual",
        created_at=trade.created_at,
    )


def _own_trade(trade: TradeLog | None, user: User) -> TradeLog:
    if not trade or trade.user_id != user.id:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    return trade


@router.get("", response_model=list[TradeOut])
def list_trades(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    symbol: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    stmt = (
        select(TradeLog)
        .options(selectinload(TradeLog.tags))
        .where(TradeLog.user_id == user.id)
        .order_by(TradeLog.date.desc(), TradeLog.id.desc())
    )
    if start:
        stmt = stmt.where(TradeLog.date >= start)
    if end:
        stmt = stmt.where(TradeLog.date <= end)
    if symbol:
        stmt = stmt.where(TradeLog.symbol == symbol.upper())
    trades = list(db.scalars(stmt).unique())
    if tag:
        trades = [t for t in trades if tag in {x.name for x in t.tags}]
    return [to_out(t) for t in trades]


@router.post("/import")
async def import_trades(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
):
    raw: str | bytes | None = None
    if file is not None:
        content = await file.read()
        if content:
            raw = content
    if raw is None and text and text.strip():
        raw = text
    if raw is None:
        raise HTTPException(status_code=400, detail="请上传 CSV 或粘贴股票成交明细")
    try:
        return import_text(db, raw, user_id=user.id)
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=TradeOut, status_code=201)
def create_trade(
    payload: TradeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    trade = TradeLog(
        user_id=user.id,
        date=payload.date,
        symbol=payload.symbol.upper().strip(),
        side=payload.side,
        pnl_amount=payload.pnl_amount,
        pnl_pct=payload.pnl_pct,
        notes=payload.notes,
        tags=get_or_create_tags(db, payload.tags),
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)
    trade = db.scalars(
        select(TradeLog).options(selectinload(TradeLog.tags)).where(TradeLog.id == trade.id)
    ).one()
    return to_out(trade)


@router.put("/{trade_id}", response_model=TradeOut)
def update_trade(
    trade_id: int,
    payload: TradeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    trade = db.scalars(
        select(TradeLog).options(selectinload(TradeLog.tags)).where(TradeLog.id == trade_id)
    ).first()
    trade = _own_trade(trade, user)
    data = payload.model_dump(exclude_unset=True)
    tags = data.pop("tags", None)
    if "symbol" in data and data["symbol"]:
        data["symbol"] = data["symbol"].upper().strip()
    for key, value in data.items():
        setattr(trade, key, value)
    if tags is not None:
        trade.tags = get_or_create_tags(db, tags)
    db.commit()
    db.refresh(trade)
    trade = db.scalars(
        select(TradeLog).options(selectinload(TradeLog.tags)).where(TradeLog.id == trade.id)
    ).one()
    return to_out(trade)


@router.delete("/{trade_id}", status_code=204)
def delete_trade(
    trade_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    trade = _own_trade(db.get(TradeLog, trade_id), user)
    db.delete(trade)
    db.commit()


@router.get("/meta/tags", response_model=list[TagOut])
def list_tags(db: Session = Depends(get_db), _user: User = Depends(require_user)):
    tags = db.scalars(select(Tag).order_by(Tag.name)).all()
    return [TagOut.model_validate(t) for t in tags]
