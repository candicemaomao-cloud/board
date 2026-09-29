from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.auth import require_user
from app.services.goal import get_settings
from app.services.live_symbols import (
    add_live_symbol,
    get_live_symbols,
    remove_live_symbol,
    set_live_symbols,
)
from app.services.market import build_market
from app.services.quotes import build_tape, fetch_mixed
from app.services.stock_sentiment import (
    StockSentimentError,
    fear_greed as stock_fear_greed,
    fear_panel,
    fear_stock_path,
    vix_history,
)
from app.services.tradingview import us_equity_session

router = APIRouter()


class QuoteItem(BaseModel):
    symbol: str
    name: str | None = None
    source: str | None = None


class QuotesBody(BaseModel):
    items: list[QuoteItem] = []


class LiveSymbolBody(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    name: str | None = Field(None, max_length=64)
    source: str = "tradingview"


class LiveSymbolsReplaceBody(BaseModel):
    items: list[LiveSymbolBody] = []


@router.get("")
def market():
    return build_market()


@router.get("/board-calendar")
def read_board_calendar(user: User = Depends(require_user)):
    """看板日程日历（宏观/FOMC/美债/三巫日），与 /market 拆开懒加载。"""
    _ = user
    from app.services.board_calendar import build_board_calendar

    return build_board_calendar()


@router.get("/board-calendar/earnings")
def read_board_calendar_earnings(
    ym: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """看板日历：按月懒加载股票列表财报日。ym=YYYY-MM，默认当月。"""
    _ = user
    from app.services.board_calendar import watchlist_earnings_month

    today = date.today()
    if ym:
        try:
            y_s, m_s = ym.strip().split("-", 1)
            year, month = int(y_s), int(m_s)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="ym 格式用 YYYY-MM") from exc
    else:
        year, month = today.year, today.month
    try:
        return watchlist_earnings_month(db, year=year, month=month, today=today)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/fear-greed")
def read_fear_greed(limit: int = 30, user: User = Depends(require_user)):
    _ = user
    try:
        return stock_fear_greed(history_days=limit)
    except StockSentimentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/fear-panel")
def read_fear_panel(date: str | None = None, user: User = Depends(require_user)):
    _ = user
    try:
        return fear_panel(asof=date)
    except StockSentimentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/vix-history")
def read_vix_history(days: int = 365, user: User = Depends(require_user)):
    _ = user
    try:
        return vix_history(days=days)
    except StockSentimentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/fear-path")
def read_fear_path(
    symbol: str,
    event_date: str,
    before: int = 30,
    after: int = 60,
    user: User = Depends(require_user),
):
    _ = user
    try:
        return fear_stock_path(symbol, event_date=event_date, before=before, after=after)
    except StockSentimentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tape")
def tape(db: Session = Depends(get_db), user: User = Depends(require_user)):
    items = get_live_symbols(user)
    names = [x["symbol"] for x in items]
    if not names:
        row = get_settings(db)
        names = [x.strip().upper() for x in (row.watchlist or "NVDA,AAPL,TSLA,QQQ").split(",") if x.strip()]
    return build_tape(names)


@router.get("/live-symbols")
def read_live_symbols(user: User = Depends(require_user)):
    return {"items": get_live_symbols(user)}


@router.put("/live-symbols")
def replace_live_symbols(
    payload: LiveSymbolsReplaceBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    items = set_live_symbols(db, user, [x.model_dump() for x in payload.items])
    return {"items": items}


@router.post("/live-symbols")
def create_live_symbol(
    payload: LiveSymbolBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    try:
        items = add_live_symbol(db, user, payload.symbol, payload.name, payload.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items}


@router.delete("/live-symbols/{symbol}")
def delete_live_symbol(
    symbol: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    items = remove_live_symbol(db, user, symbol)
    return {"items": items}


@router.post("/quotes")
def quotes(payload: QuotesBody):
    items = [item.model_dump() for item in payload.items if (item.symbol or "").strip()]
    return {"quotes": fetch_mixed(items), "market": us_equity_session()}
