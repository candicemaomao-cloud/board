from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import DailyWatchIn, DailyWatchOut
from app.services.daily_watch import (
    DailyWatchError,
    create_watch,
    delete_watch,
    get_watch,
    list_watches,
    lookup_quote,
    refresh_options,
    refresh_price,
    refresh_regression,
    update_watch,
)
from app.services.daily_watch_analysis import analyze

router = APIRouter()


@router.get("", response_model=list[DailyWatchOut])
def read_watches(db: Session = Depends(get_db)):
    return list_watches(db)


@router.get("/lookup")
def lookup(symbol: str):
    try:
        return lookup_quote(symbol)
    except DailyWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/analysis")
def get_analysis(
    symbols: str | None = None,
    mode: str = "day",
    asof: str | None = None,
    db: Session = Depends(get_db),
):
    symbol_list = [s for s in symbols.split(",")] if symbols else None
    try:
        return analyze(db, symbol_list, mode, asof=asof)
    except DailyWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=DailyWatchOut, status_code=201)
def add_watch(payload: DailyWatchIn, db: Session = Depends(get_db)):
    try:
        return create_watch(db, payload.model_dump())
    except DailyWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{watch_id}", response_model=DailyWatchOut)
def edit_watch(watch_id: int, payload: DailyWatchIn, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        return update_watch(db, row, payload.model_dump())
    except DailyWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{watch_id}", status_code=204)
def remove_watch(watch_id: int, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    delete_watch(db, row)


@router.post("/{watch_id}/refresh-price", response_model=DailyWatchOut)
def refresh_watch_price(watch_id: int, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        return refresh_price(db, row)
    except DailyWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{watch_id}/refresh-options")
def refresh_watch_options(watch_id: int, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        row, skew_pct = refresh_options(db, row)
    except DailyWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = DailyWatchOut.model_validate(row).model_dump()
    out["skew_pct"] = skew_pct
    return out


@router.post("/{watch_id}/refresh-regression", response_model=DailyWatchOut)
def refresh_watch_regression(watch_id: int, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        return refresh_regression(db, row)
    except DailyWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# Detail routes remain under the watch list permission; notes are private to their author.
from typing import Literal
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.models import StockAnalysisNote, User
from app.services.auth import require_user, has_perm


def detail_watch(db, watch_id, user):
    if not has_perm(user, 'menu.dailyWatch'):
        raise HTTPException(status_code=403, detail='无股票列表权限')
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail='股票不存在')
    return row


def detail_symbol(symbol: str, user: User) -> str:
    if not (has_perm(user, 'menu.dailyWatch') or has_perm(user, 'menu.stockScreener')):
        raise HTTPException(status_code=403, detail='无股票详情权限')
    code = (symbol or '').strip().upper()
    if not code or len(code) > 16 or not code.replace('-', '').replace('.', '').isalnum():
        raise HTTPException(status_code=400, detail='股票代码无效')
    return code


def _detail_for_symbol(symbol: str, section: str):
    from app.services import fundamentals, ohlc, screener, stock_detail
    if section == 'financials':
        return fundamentals.analyze(symbol)
    if section == 'price':
        return ohlc.fetch_closes(symbol, '1d', apply_live=False)
    if section == 'regression':
        return screener.analyze_regression(symbol)
    if section == 'options':
        from app.user_stocks.options import analyze_option_chain
        return analyze_option_chain(symbol)
    return getattr(stock_detail, section)(symbol)


@router.get('/{watch_id}/detail/{section}')
def read_detail(watch_id: int, section: Literal['financials', 'price', 'options', 'regression', 'ratings', 'news', 'events'],
                db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = detail_watch(db, watch_id, user)
    try:
        return _detail_for_symbol(row.symbol, section)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning('Stock detail %s/%s failed: %s', row.symbol, section, exc)
        raise HTTPException(status_code=502, detail='数据暂不可用，请稍后重试') from exc


@router.get('/symbol/{symbol}/detail/{section}')
def read_symbol_detail(symbol: str, section: Literal['financials', 'price', 'options', 'regression', 'ratings', 'news', 'events'],
                       user: User = Depends(require_user)):
    code = detail_symbol(symbol, user)
    try:
        return _detail_for_symbol(code, section)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning('Stock detail %s/%s failed: %s', code, section, exc)
        raise HTTPException(status_code=502, detail='数据暂不可用，请稍后重试') from exc


class AnalysisNoteBody(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


def note_out(row):
    from datetime import timezone
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {'id': row.id, 'content': row.content, 'created_at': created.isoformat()}


@router.get('/{watch_id}/notes')
def read_notes(watch_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    watch = detail_watch(db, watch_id, user)
    rows = db.scalars(select(StockAnalysisNote).where(
        StockAnalysisNote.user_id == user.id, StockAnalysisNote.symbol == watch.symbol
    ).order_by(StockAnalysisNote.created_at.desc(), StockAnalysisNote.id.desc()))
    return {'items': [note_out(r) for r in rows]}


@router.post('/{watch_id}/notes', status_code=201)
def add_note(watch_id: int, payload: AnalysisNoteBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    watch = detail_watch(db, watch_id, user)
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail='分析记录不能为空')
    row = StockAnalysisNote(user_id=user.id, symbol=watch.symbol, content=payload.content.strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return note_out(row)


class EntryAnalysisBody(BaseModel):
    horizon: Literal[3, 10, 30, 60] = 10
    cost_bps: float = Field(default=20, ge=0, le=500, allow_inf_nan=False)
    borrow_pct: float = Field(default=5, ge=0, le=200, allow_inf_nan=False)


@router.post('/{watch_id}/entry-analysis')
def entry_analysis(watch_id: int, payload: EntryAnalysisBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    watch = detail_watch(db, watch_id, user)
    from app.services.entry_analysis import analyze
    try:
        return analyze(watch.symbol, payload.horizon, payload.cost_bps, payload.borrow_pct)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail='买卖价分析暂不可用，请稍后重试') from exc


@router.post('/symbol/{symbol}/entry-analysis')
def symbol_entry_analysis(symbol: str, payload: EntryAnalysisBody, user: User = Depends(require_user)):
    code = detail_symbol(symbol, user)
    from app.services.entry_analysis import analyze
    try:
        return analyze(code, payload.horizon, payload.cost_bps, payload.borrow_pct)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail='买卖价分析暂不可用，请稍后重试') from exc


class EntryPriceBody(BaseModel):
    side: Literal['long', 'short']
    price: float = Field(gt=0, le=10_000_000, allow_inf_nan=False)


@router.post('/{watch_id}/entry-price', response_model=DailyWatchOut)
def save_entry_price(
    watch_id: int,
    payload: EntryPriceBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    watch = detail_watch(db, watch_id, user)
    if not has_perm(user, 'btn.daily_watch.write'):
        raise HTTPException(status_code=403, detail='无权限：股票列表增删改')
    if payload.side == 'long':
        watch.long_entry_price = payload.price
    else:
        watch.short_entry_price = payload.price
    db.commit()
    db.refresh(watch)
    return watch
