from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StockJournal

STANCES = {"空", "多", "观望"}


class StockJournalError(ValueError):
    pass


def to_out(row: StockJournal) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "log_date": row.log_date.isoformat() if row.log_date else None,
        "stance": row.stance or "观望",
        "content": row.content or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_journals(
    db: Session,
    user_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[StockJournal]:
    stmt = select(StockJournal).where(StockJournal.user_id == user_id)
    if date_from is not None:
        stmt = stmt.where(StockJournal.log_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(StockJournal.log_date <= date_to)
    stmt = stmt.order_by(StockJournal.log_date.desc(), StockJournal.id.desc())
    return list(db.scalars(stmt))


def get_journal(db: Session, journal_id: int) -> StockJournal | None:
    return db.get(StockJournal, journal_id)


def _normalize_stance(stance: str | None) -> str:
    value = (stance or "").strip()
    if value not in STANCES:
        raise StockJournalError("建议只能是 空 / 多 / 观望")
    return value


def create_journal(
    db: Session,
    *,
    user_id: int,
    log_date: date,
    stance: str,
    content: str | None = None,
) -> StockJournal:
    if not log_date:
        raise StockJournalError("请选择日期")
    stance = _normalize_stance(stance)
    exists = db.scalar(
        select(StockJournal).where(
            StockJournal.user_id == user_id,
            StockJournal.log_date == log_date,
        )
    )
    if exists:
        raise StockJournalError(f"{log_date.isoformat()} 已有日志，请直接改那一条")
    row = StockJournal(
        user_id=user_id,
        log_date=log_date,
        stance=stance,
        content=(content or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_journal(db: Session, row: StockJournal, data: dict) -> StockJournal:
    if "log_date" in data and data["log_date"] is not None:
        new_date = data["log_date"]
        if new_date != row.log_date:
            clash = db.scalar(
                select(StockJournal).where(
                    StockJournal.user_id == row.user_id,
                    StockJournal.log_date == new_date,
                    StockJournal.id != row.id,
                )
            )
            if clash:
                raise StockJournalError(f"{new_date.isoformat()} 已有日志")
            row.log_date = new_date
    if "stance" in data and data["stance"] is not None:
        row.stance = _normalize_stance(data["stance"])
    if "content" in data:
        raw = data["content"]
        row.content = (str(raw).strip() if raw is not None else "") or None
    db.commit()
    db.refresh(row)
    return row


def append_journal(
    db: Session,
    *,
    user_id: int,
    log_date: date,
    snippet: str,
    stance: str | None = None,
) -> StockJournal:
    """把一段分析追加进某日日志；没有则新建（默认观望）。"""
    if not log_date:
        raise StockJournalError("请选择日期")
    text = (snippet or "").strip()
    if not text:
        raise StockJournalError("没有可写入的内容")

    row = db.scalar(
        select(StockJournal).where(
            StockJournal.user_id == user_id,
            StockJournal.log_date == log_date,
        )
    )
    if row is None:
        return create_journal(
            db,
            user_id=user_id,
            log_date=log_date,
            stance=_normalize_stance(stance or "观望"),
            content=text,
        )

    if stance is not None:
        row.stance = _normalize_stance(stance)
    prev = (row.content or "").strip()
    row.content = f"{prev}\n\n{text}" if prev else text
    db.commit()
    db.refresh(row)
    return row


def delete_journal(db: Session, row: StockJournal) -> None:
    db.delete(row)
    db.commit()
