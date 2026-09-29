from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DailySnapshot, User
from app.schemas import SnapshotCreate, SnapshotOut, SnapshotUpdate
from app.services.auth import require_user

router = APIRouter()


def _fill_return_pct(db: Session, snap: DailySnapshot) -> None:
    q = select(DailySnapshot).where(DailySnapshot.date < snap.date)
    if snap.user_id is not None:
        q = q.where(DailySnapshot.user_id == snap.user_id)
    prev = db.scalars(q.order_by(DailySnapshot.date.desc())).first()
    base = prev.total_equity if prev else (snap.total_equity - snap.daily_pnl)
    snap.daily_return_pct = (snap.daily_pnl / base) if base else 0.0


def _to_out(snap: DailySnapshot) -> SnapshotOut:
    return SnapshotOut.model_validate(snap)


def _own(snap: DailySnapshot | None, user: User) -> DailySnapshot:
    if not snap or snap.user_id != user.id:
        raise HTTPException(status_code=404, detail="快照不存在")
    return snap


@router.get("", response_model=list[SnapshotOut])
def list_snapshots(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    stmt = select(DailySnapshot).where(DailySnapshot.user_id == user.id).order_by(DailySnapshot.date.desc())
    if start:
        stmt = stmt.where(DailySnapshot.date >= start)
    if end:
        stmt = stmt.where(DailySnapshot.date <= end)
    return [_to_out(s) for s in db.scalars(stmt)]


@router.post("", response_model=SnapshotOut, status_code=201)
def create_snapshot(
    payload: SnapshotCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    existing = db.scalars(
        select(DailySnapshot).where(DailySnapshot.user_id == user.id, DailySnapshot.date == payload.date)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="该日期已有资金快照")

    daily_pnl = payload.daily_pnl
    if daily_pnl is None:
        prev = db.scalars(
            select(DailySnapshot)
            .where(DailySnapshot.user_id == user.id, DailySnapshot.date < payload.date)
            .order_by(DailySnapshot.date.desc())
        ).first()
        daily_pnl = payload.total_equity - prev.total_equity if prev else 0.0

    snap = DailySnapshot(
        user_id=user.id,
        date=payload.date,
        total_equity=payload.total_equity,
        daily_pnl=daily_pnl,
        cash_balance=payload.cash_balance,
        notes=payload.notes,
        daily_return_pct=payload.daily_return_pct or 0.0,
    )
    if payload.daily_return_pct is None:
        db.add(snap)
        db.flush()
        _fill_return_pct(db, snap)
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return _to_out(snap)


@router.put("/{snapshot_id}", response_model=SnapshotOut)
def update_snapshot(
    snapshot_id: int,
    payload: SnapshotUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    snap = _own(db.get(DailySnapshot, snapshot_id), user)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(snap, key, value)
    if "daily_return_pct" not in data:
        _fill_return_pct(db, snap)
    db.commit()
    db.refresh(snap)
    return _to_out(snap)


@router.delete("/{snapshot_id}", status_code=204)
def delete_snapshot(
    snapshot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    snap = _own(db.get(DailySnapshot, snapshot_id), user)
    db.delete(snap)
    db.commit()
