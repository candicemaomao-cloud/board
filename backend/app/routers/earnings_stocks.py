from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import DailyWatchIn, DailyWatchOut
from app.services.daily_watch import DailyWatchError, create_watch, list_watches, lookup_quote
from app.services.earnings_stocks import EarningsStocksError, upcoming_earnings

router = APIRouter()


@router.get("")
def read_recent_earnings(
    days: int = Query(default=30, ge=1, le=45),
    enrich: bool = Query(default=True),
    max_enrich: int = Query(default=80, ge=0, le=200),
    db: Session = Depends(get_db),
):
    try:
        data = upcoming_earnings(days=days, enrich=enrich, max_enrich=max_enrich)
    except EarningsStocksError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    watched = {(w.symbol or "").upper() for w in list_watches(db)}
    for row in data.get("items") or []:
        row["in_list"] = (row.get("symbol") or "").upper() in watched
    return data


@router.post("/add", response_model=DailyWatchOut, status_code=201)
def add_earnings_to_watch(payload: dict, db: Session = Depends(get_db)):
    """把财报股票加入股票列表（每日观察）。已存在则报错提示。"""
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="请提供股票代码")
    existing = {(w.symbol or "").upper() for w in list_watches(db)}
    if symbol in existing:
        raise HTTPException(status_code=400, detail=f"{symbol} 已在股票列表中")

    sector = str(payload.get("sector_zh") or payload.get("sector") or "其他").strip() or "其他"
    from app.services.daily_watch import SECTORS

    if sector not in SECTORS:
        sector = "其他"

    events_bits = []
    if payload.get("earnings_date"):
        events_bits.append(f"财报日 {payload.get('earnings_date')}")
    if payload.get("name"):
        events_bits.append(str(payload.get("name")))
    events = " · ".join(events_bits) or None

    try:
        quote = lookup_quote(symbol)
    except DailyWatchError:
        quote = {
            "symbol": symbol,
            "prev_close": None,
            "prev_open": None,
            "prev_low": None,
            "prev_high": None,
            "current_price": payload.get("price"),
        }

    body = DailyWatchIn(
        symbol=symbol,
        prev_close=quote.get("prev_close"),
        prev_open=quote.get("prev_open"),
        prev_low=quote.get("prev_low"),
        prev_high=quote.get("prev_high"),
        current_price=quote.get("current_price") or payload.get("price"),
        market_direction="横盘",
        sector=sector,
        events=events,
        is_potential=False,
    )
    try:
        return create_watch(db, body.model_dump())
    except DailyWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
