from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TradePlan

KINDS = {"day", "week", "month"}


class PlanError(ValueError):
    pass


def to_out(row: TradePlan) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "kind": row.kind,
        "title": row.title or "",
        "content": row.content or "",
        "plan_date": row.plan_date.isoformat() if row.plan_date else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_plans(db: Session, user_id: int, kind: str | None = None) -> list[TradePlan]:
    stmt = select(TradePlan).where(TradePlan.user_id == user_id)
    if kind:
        if kind not in KINDS:
            raise PlanError("计划类型只能是 day / week / month")
        stmt = stmt.where(TradePlan.kind == kind)
    stmt = stmt.order_by(TradePlan.id.desc())
    return list(db.scalars(stmt))


def create_plan(
    db: Session,
    *,
    user_id: int,
    kind: str,
    title: str,
    content: str | None = None,
    plan_date: date | None = None,
) -> TradePlan:
    if kind not in KINDS:
        raise PlanError("计划类型只能是 day / week / month")
    title = (title or "").strip()
    if not title:
        raise PlanError("请填写标题")
    row = TradePlan(
        user_id=user_id,
        kind=kind,
        title=title,
        content=(content or "").strip() or None,
        plan_date=plan_date,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_plan(db: Session, row: TradePlan, data: dict) -> TradePlan:
    if "kind" in data and data["kind"] is not None:
        if data["kind"] not in KINDS:
            raise PlanError("计划类型只能是 day / week / month")
        row.kind = data["kind"]
    if "title" in data and data["title"] is not None:
        title = str(data["title"]).strip()
        if not title:
            raise PlanError("请填写标题")
        row.title = title
    if "content" in data:
        raw = data["content"]
        row.content = (str(raw).strip() if raw is not None else "") or None
    if "plan_date" in data:
        row.plan_date = data["plan_date"]
    db.commit()
    db.refresh(row)
    return row


def delete_plan(db: Session, row: TradePlan) -> None:
    db.delete(row)
    db.commit()
