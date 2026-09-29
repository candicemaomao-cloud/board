from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSettings, DailySnapshot, TradeLog, User
from app.services.positions import mark_open_positions


def book_equity(db: Session, user_id: int | None = None) -> float:
    """账面资产。

    有登录用户时：以用户「总金额」为准（看板「修改总金额」会写这里）。
    无用户时：回退到全局 settings + 快照/交易（兼容旧逻辑）。
    """
    if user_id is not None:
        user = db.get(User, user_id)
        if user is not None:
            return float(user.total_amount or 0)

    settings = db.get(AppSettings, 1)
    if settings is None:
        return 0.0
    principal = float(settings.account_a or 0) + float(settings.account_b or 0)
    snapshots = list(db.scalars(select(DailySnapshot).order_by(DailySnapshot.date)))
    trades = list(db.scalars(select(TradeLog)))
    if snapshots:
        latest = snapshots[-1]
        extra = sum(t.pnl_amount for t in trades if t.date > latest.date)
        return float(latest.total_equity) + extra
    journaled = sum(t.pnl_amount for t in trades)
    return principal + float(settings.current_pnl or 0) + journaled


def account_totals(db: Session, user_id: int | None = None) -> dict:
    book = book_equity(db, user_id=user_id)
    mark = mark_open_positions(db, user_id=user_id)
    available = book - mark["invested"] - mark["fees"]
    total = available + mark["live_value"]
    return {
        "book_equity": round(book, 2),
        "equity": round(total, 2),
        "available": round(available, 2),
        "invested": round(mark["invested"], 2),
        "fees": round(mark["fees"], 4),
        "live_value": round(mark["live_value"], 2),
        "position_pnl": round(mark["pnl"], 2),
    }
