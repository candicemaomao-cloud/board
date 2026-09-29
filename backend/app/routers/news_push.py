from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services import news_push
from app.services.auth import require_user

router = APIRouter()
Scope = Literal["crypto", "stock"]


class NewsPushBody(BaseModel):
    enabled: bool | None = None
    recipient_ids: list[int] | None = None
    interval_sec: int | None = None
    min_score: int | None = None
    x_accounts: str | list[str] | None = None


@router.get("/{scope}")
def read_push(scope: Scope, db: Session = Depends(get_db), user: User = Depends(require_user)):
    profile = news_push.PROFILES[scope]
    return news_push.to_out(profile, news_push.get_or_create(db, profile, user.id))


@router.put("/{scope}")
def edit_push(scope: Scope, payload: NewsPushBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    profile = news_push.PROFILES[scope]
    try:
        row = news_push.save(db, profile, user.id, payload.model_dump(exclude_none=True))
    except news_push.NewsPushError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return news_push.to_out(profile, row)


@router.post("/{scope}/run")
def run_push(
    scope: Scope,
    push: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    profile = news_push.PROFILES[scope]
    row = news_push.get_or_create(db, profile, user.id)
    return news_push.scan(db, profile, row, push=push)


@router.post("/{scope}/test")
def test_push(
    scope: Scope,
    payload: NewsPushBody | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    profile = news_push.PROFILES[scope]
    row = news_push.get_or_create(db, profile, user.id)
    try:
        return news_push.send_test(db, profile, row, payload.recipient_ids if payload else None)
    except news_push.NewsPushError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
