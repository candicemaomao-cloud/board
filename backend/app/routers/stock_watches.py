from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.recipients import list_recipients
from app.services.stock_watches import (
    StockWatchError,
    create_watch,
    delete_watch,
    get_watch,
    list_watches,
    refresh_one,
    send_test_push,
    set_monitor,
    set_push,
    to_out,
    update_watch,
)

router = APIRouter()


class StockWatchBody(BaseModel):
    symbol: str
    notes: str | None = None
    strategy: str = "mu_dip"
    monitor: bool = False
    allow_push: bool = False
    recipient_ids: list[int] | None = None
    interval_sec: int = 60
    budget: float = 10000
    hedge_symbol: str = "SPY"
    allow_trend: bool = False
    allow_short: bool = False
    day_filter: bool = True
    vol_adapt: bool = True
    integral: bool = True
    hedge: bool = False
    gradient: bool = True
    vol_confirm: bool = False
    vol_z_th: float = 1.0
    kelly: bool = False
    fml_stop_loss_atr_mult: float = 0.5
    fml_daily_atr_n: int = 14
    fml_volume_high_pct: float = 90.0
    fml_vwap_trend_lookback: int = 6
    fml_allow_short: bool = False
    fml_use_shape_filter: bool = False
    fml_shape_confidence_threshold: float = 40.0
    fml_close_no_trade_minutes: int = 0
    fml_market_symbol: str = "SPY"


class ToggleBody(BaseModel):
    enabled: bool = False


def _people_map(db: Session) -> dict[int, str]:
    return {r.id: r.name for r in list_recipients(db)}


@router.get("")
def watches(db: Session = Depends(get_db)):
    people = _people_map(db)
    return [to_out(row, people) for row in list_watches(db)]


@router.post("")
def add_watch(payload: StockWatchBody, db: Session = Depends(get_db)):
    try:
        row = create_watch(db, payload.model_dump())
    except StockWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row, _people_map(db))


@router.put("/{watch_id}")
def edit_watch(watch_id: int, payload: StockWatchBody, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="股票不存在")
    try:
        row = update_watch(db, row, payload.model_dump())
    except StockWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row, _people_map(db))


@router.delete("/{watch_id}", status_code=204)
def remove_watch(watch_id: int, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="股票不存在")
    delete_watch(db, row)


@router.put("/{watch_id}/monitor")
def edit_monitor(watch_id: int, payload: ToggleBody, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="股票不存在")
    row = set_monitor(db, row, payload.enabled)
    return to_out(row, _people_map(db))


@router.put("/{watch_id}/push")
def edit_push(watch_id: int, payload: ToggleBody, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="股票不存在")
    row = set_push(db, row, payload.enabled)
    return to_out(row, _people_map(db))


@router.post("/{watch_id}/test-push")
def test_push_watch(watch_id: int, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="股票不存在")
    try:
        return send_test_push(db, row)
    except StockWatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{watch_id}/refresh")
def refresh_watch(watch_id: int, db: Session = Depends(get_db)):
    row = get_watch(db, watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="股票不存在")
    out = refresh_one(db, row, force=True)
    people = _people_map(db)
    packed = to_out(get_watch(db, watch_id), people)
    packed["notify_results"] = out.get("notify_results") or []
    if out.get("error"):
        packed["error"] = out["error"]
    return packed
