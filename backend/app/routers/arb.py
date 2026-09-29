from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.arb_strategy import (
    ArbError,
    create_arb,
    delete_arb,
    get_arb,
    list_arbs,
    run_arb,
    to_out,
    update_arb,
)
from app.services.user_arb_files import is_file_id, list_file_arbs, run_file_arb

router = APIRouter()


class ArbBody(BaseModel):
    name: str
    notes: str | None = None
    kind: str = "ols"
    timeframe: str = "1d"
    leg_a: str
    leg_b: str
    lookback: int = 60
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 3.5
    beta: float | None = None
    notional: float = 10000
    bt_days: int = 0
    macro_filter: bool = False
    factors: list[str] | None = None


@router.get("")
def arbs(db: Session = Depends(get_db)):
    return list_file_arbs() + [to_out(row) for row in list_arbs(db)]


@router.post("")
def add_arb(payload: ArbBody, db: Session = Depends(get_db)):
    try:
        row = create_arb(db, payload.model_dump())
    except ArbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)


def _db_arb(db: Session, arb_id: str):
    if is_file_id(arb_id):
        return None
    try:
        nid = int(arb_id)
    except (TypeError, ValueError):
        return None
    return get_arb(db, nid)


@router.put("/{arb_id}")
def edit_arb(arb_id: str, payload: ArbBody, db: Session = Depends(get_db)):
    if is_file_id(arb_id):
        raise HTTPException(status_code=400, detail="手写策略请改 backend/app/user_arbs/ 下的文件")
    row = _db_arb(db, arb_id)
    if not row:
        raise HTTPException(status_code=404, detail="套利策略不存在")
    try:
        row = update_arb(db, row, payload.model_dump())
    except ArbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)


@router.delete("/{arb_id}", status_code=204)
def remove_arb(arb_id: str, db: Session = Depends(get_db)):
    if is_file_id(arb_id):
        raise HTTPException(status_code=400, detail="手写策略请直接删 backend/app/user_arbs/ 里对应的 .py")
    row = _db_arb(db, arb_id)
    if not row:
        raise HTTPException(status_code=404, detail="套利策略不存在")
    delete_arb(db, row)


@router.post("/{arb_id}/run")
def calc_arb(arb_id: str, db: Session = Depends(get_db)):
    try:
        if is_file_id(arb_id):
            return run_file_arb(arb_id)
        row = _db_arb(db, arb_id)
        if not row:
            raise HTTPException(status_code=404, detail="套利策略不存在")
        return run_arb(row)
    except ArbError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
