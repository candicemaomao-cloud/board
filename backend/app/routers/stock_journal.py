from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import StockJournal, User
from app.services.auth import has_perm, require_user
from app.services.stock_journal import (
    StockJournalError,
    append_journal,
    create_journal,
    delete_journal,
    get_journal,
    list_journals,
    to_out,
    update_journal,
)

router = APIRouter()


class JournalBody(BaseModel):
    log_date: date
    stance: str = Field(default="观望", description="空|多|观望")
    content: str | None = None


class JournalUpdateBody(BaseModel):
    log_date: date | None = None
    stance: str | None = None
    content: str | None = None


class JournalAppendBody(BaseModel):
    log_date: date
    snippet: str = Field(..., min_length=1, description="追加进日志的分析正文")
    stance: str | None = Field(default=None, description="可选：顺带改建议；不传则保留原值")


def _own(row: StockJournal | None, user: User) -> StockJournal:
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="日志不存在")
    return row


@router.get("")
def read_journals(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    rows = list_journals(db, user.id, date_from=date_from, date_to=date_to)
    return {"items": [to_out(r) for r in rows]}


@router.post("")
def add_journal(payload: JournalBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if not has_perm(user, "btn.stock_journal.write"):
        raise HTTPException(status_code=403, detail="无权限：股票日志增删改")
    try:
        row = create_journal(
            db,
            user_id=user.id,
            log_date=payload.log_date,
            stance=payload.stance,
            content=payload.content,
        )
    except StockJournalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)


@router.post("/append")
def append_to_journal(
    payload: JournalAppendBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not has_perm(user, "btn.stock_journal.write"):
        raise HTTPException(status_code=403, detail="无权限：股票日志增删改")
    try:
        row = append_journal(
            db,
            user_id=user.id,
            log_date=payload.log_date,
            snippet=payload.snippet,
            stance=payload.stance,
        )
    except StockJournalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)


@router.put("/{journal_id}")
def edit_journal(
    journal_id: int,
    payload: JournalUpdateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not has_perm(user, "btn.stock_journal.write"):
        raise HTTPException(status_code=403, detail="无权限：股票日志增删改")
    row = _own(get_journal(db, journal_id), user)
    try:
        row = update_journal(db, row, payload.model_dump(exclude_unset=True))
    except StockJournalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)


@router.delete("/{journal_id}", status_code=204)
def remove_journal(
    journal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not has_perm(user, "btn.stock_journal.write"):
        raise HTTPException(status_code=403, detail="无权限：股票日志增删改")
    row = _own(get_journal(db, journal_id), user)
    delete_journal(db, row)
