from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.alerts import (
    AlertError,
    create_alert,
    delete_alert,
    get_alert,
    list_alerts,
    next_refresh_ts,
    refresh_alerts,
    set_alert_push,
    to_out,
    update_alert,
)
from app.services.notify import NotifyError, notify_status, save_notify, send_test
from app.services.recipients import list_recipients
from app.services.strategy_plans import list_strategies, name_map

router = APIRouter()


class AlertBody(BaseModel):
    symbol: str
    indicators: list[str]
    join: str = "and"
    name: str | None = None
    notes: str | None = None
    push: bool = False
    enabled: bool | None = None
    interval_sec: int = 30
    recipient_ids: list[int] = []


class AlertPushBody(BaseModel):
    push: bool | None = None
    enabled: bool | None = None
    interval_sec: int | None = None


class NotifyBody(BaseModel):
    telegram: bool = False
    whatsapp: bool = False
    wx: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    whatsapp_phone: str | None = None
    whatsapp_apikey: str | None = None
    wx_webhook: str | None = None
    wx_sendkey: str | None = None


def _payload(rows, extra: dict | None = None, names: dict | None = None, people: dict | None = None) -> dict:
    data = {
        "items": [to_out(row, names=names, people=people) for row in rows],
        "next_refresh": next_refresh_ts(rows),
    }
    if extra:
        data.update(extra)
    return data


def _names(db: Session) -> dict[int, str]:
    return name_map(list_strategies(db))


def _people(db: Session) -> dict[int, str]:
    return {row.id: row.name for row in list_recipients(db)}


def _watch_on(payload: AlertBody) -> bool:
    if payload.enabled is not None:
        return bool(payload.enabled)
    return bool(payload.push)


@router.get("")
def alerts(db: Session = Depends(get_db)):
    return _payload(list_alerts(db), names=_names(db), people=_people(db))


@router.post("/refresh")
def refresh(force: bool = False, db: Session = Depends(get_db)):
    rows, extra = refresh_alerts(db, force=force)
    return _payload(rows, extra, names=_names(db), people=_people(db))


@router.get("/notify")
def read_notify(db: Session = Depends(get_db)):
    return notify_status(db)


@router.put("/notify")
def edit_notify(payload: NotifyBody, db: Session = Depends(get_db)):
    try:
        return save_notify(db, payload.model_dump(exclude_none=True))
    except NotifyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/notify/test")
def test_notify(db: Session = Depends(get_db)):
    try:
        return send_test(db)
    except NotifyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
def add_alert(payload: AlertBody, db: Session = Depends(get_db)):
    try:
        row, test = create_alert(
            db,
            symbol=payload.symbol,
            indicators=payload.indicators,
            join=payload.join,
            name=payload.name,
            notes=payload.notes,
            push=_watch_on(payload),
            recipient_ids=payload.recipient_ids,
            interval_sec=payload.interval_sec,
        )
    except AlertError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row, test, names=_names(db), people=_people(db))


@router.put("/{alert_id}")
def edit_alert(alert_id: int, payload: AlertBody, db: Session = Depends(get_db)):
    row = get_alert(db, alert_id)
    if not row:
        raise HTTPException(status_code=404, detail="警报不存在")
    try:
        row, test = update_alert(
            db,
            row,
            symbol=payload.symbol,
            indicators=payload.indicators,
            join=payload.join,
            name=payload.name,
            notes=payload.notes,
            push=_watch_on(payload),
            recipient_ids=payload.recipient_ids,
            interval_sec=payload.interval_sec,
        )
    except AlertError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row, test, names=_names(db), people=_people(db))


@router.put("/{alert_id}/push")
def edit_alert_push(alert_id: int, payload: AlertPushBody, db: Session = Depends(get_db)):
    row = get_alert(db, alert_id)
    if not row:
        raise HTTPException(status_code=404, detail="警报不存在")
    row, test = set_alert_push(
        db,
        row,
        push=payload.enabled if payload.enabled is not None else payload.push,
        interval_sec=payload.interval_sec,
    )
    return to_out(row, test, names=_names(db), people=_people(db))


@router.delete("/{alert_id}", status_code=204)
def remove_alert(alert_id: int, db: Session = Depends(get_db)):
    row = get_alert(db, alert_id)
    if not row:
        raise HTTPException(status_code=404, detail="警报不存在")
    delete_alert(db, row)
