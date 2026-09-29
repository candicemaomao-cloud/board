from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.notify import NotifyError
from app.services.recipients import (
    RecipientError,
    create_recipient,
    delete_recipient,
    get_recipient,
    list_recipients,
    test_recipient,
    to_out,
    update_recipient,
)

router = APIRouter()


class RecipientBody(BaseModel):
    name: str
    notes: str | None = None
    telegram: bool = False
    whatsapp: bool = False
    wx: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    whatsapp_phone: str | None = None
    whatsapp_apikey: str | None = None
    wx_webhook: str | None = None
    wx_sendkey: str | None = None


@router.get("")
def recipients(db: Session = Depends(get_db)):
    return {"items": [to_out(row) for row in list_recipients(db)]}


@router.post("")
def add_recipient(payload: RecipientBody, db: Session = Depends(get_db)):
    try:
        row = create_recipient(db, payload.model_dump(exclude_none=True))
    except RecipientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)


@router.put("/{recipient_id}")
def edit_recipient(recipient_id: int, payload: RecipientBody, db: Session = Depends(get_db)):
    row = get_recipient(db, recipient_id)
    if not row:
        raise HTTPException(status_code=404, detail="推送人不存在")
    try:
        row = update_recipient(db, row, payload.model_dump(exclude_none=True))
    except RecipientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)


@router.post("/{recipient_id}/test")
def ping_recipient(recipient_id: int, db: Session = Depends(get_db)):
    row = get_recipient(db, recipient_id)
    if not row:
        raise HTTPException(status_code=404, detail="推送人不存在")
    try:
        return test_recipient(db, row)
    except NotifyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{recipient_id}", status_code=204)
def remove_recipient(recipient_id: int, db: Session = Depends(get_db)):
    row = get_recipient(db, recipient_id)
    if not row:
        raise HTTPException(status_code=404, detail="推送人不存在")
    delete_recipient(db, row)
