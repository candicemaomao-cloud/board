from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TradePlan, User
from app.services.auth import has_perm, require_user
from app.services.plans import PlanError, create_plan, delete_plan, list_plans, to_out, update_plan

router = APIRouter()


class PlanBody(BaseModel):
    kind: str = Field(..., description="day|week|month")
    title: str
    content: str | None = None
    plan_date: date | None = None


class PlanUpdateBody(BaseModel):
    kind: str | None = None
    title: str | None = None
    content: str | None = None
    plan_date: date | None = None


def _own(row: TradePlan | None, user: User) -> TradePlan:
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="计划不存在")
    return row


@router.get("")
def read_plans(
    kind: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    try:
        rows = list_plans(db, user.id, kind)
    except PlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": [to_out(r) for r in rows]}


@router.post("")
def add_plan(payload: PlanBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if not has_perm(user, "btn.plans.write"):
        raise HTTPException(status_code=403, detail="无权限：计划增删改")
    try:
        row = create_plan(
            db,
            user_id=user.id,
            kind=payload.kind,
            title=payload.title,
            content=payload.content,
            plan_date=payload.plan_date,
        )
    except PlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)


@router.put("/{plan_id}")
def edit_plan(
    plan_id: int,
    payload: PlanUpdateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not has_perm(user, "btn.plans.write"):
        raise HTTPException(status_code=403, detail="无权限：计划增删改")
    row = _own(db.get(TradePlan, plan_id), user)
    try:
        row = update_plan(db, row, payload.model_dump(exclude_unset=True))
    except PlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)


@router.delete("/{plan_id}", status_code=204)
def remove_plan(plan_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if not has_perm(user, "btn.plans.write"):
        raise HTTPException(status_code=403, detail="无权限：计划增删改")
    row = _own(db.get(TradePlan, plan_id), user)
    delete_plan(db, row)
