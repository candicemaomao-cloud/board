from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.arb_strategy import ArbError, create_arb, to_out
from app.services.pair_scan import job_status, peek_scan, start_scan, universe

router = APIRouter()


class ScanBody(BaseModel):
    force: bool = False
    groups: list[str] | None = None


class ToStrategyBody(BaseModel):
    leg_a: str
    leg_b: str
    name: str | None = None
    notes: str | None = None


@router.get("/universe")
def pair_universe():
    return universe()


@router.get("/scan")
def pair_scan_cached():
    return peek_scan()


@router.get("/scan/status")
def pair_scan_status():
    return job_status()


@router.post("/scan")
def pair_scan_run(payload: ScanBody | None = None):
    body = payload or ScanBody()
    return start_scan(force=body.force, groups=body.groups)


@router.post("/to-strategy")
def pair_to_strategy(payload: ToStrategyBody, db: Session = Depends(get_db)):
    a = payload.leg_a.strip().upper()
    b = payload.leg_b.strip().upper()
    name = (payload.name or f"{a} / {b}").strip()[:64]
    notes = payload.notes or f"{a} 对 {b} 的相对价值，来自选对扫描。"
    try:
        row = create_arb(
            db,
            {
                "name": name,
                "notes": notes,
                "kind": "ols",
                "timeframe": "1d",
                "leg_a": a,
                "leg_b": b,
                "lookback": 60,
                "entry_z": 2.0,
                "exit_z": 0.5,
                "stop_z": 3.5,
                "notional": 10000,
                "bt_days": 0,
            },
        )
    except ArbError as exc:
        if "同名" in str(exc):
            try:
                row = create_arb(
                    db,
                    {
                        "name": f"{a}/{b} 选对",
                        "notes": notes,
                        "kind": "ols",
                        "timeframe": "1d",
                        "leg_a": a,
                        "leg_b": b,
                        "lookback": 60,
                        "entry_z": 2.0,
                        "exit_z": 0.5,
                        "stop_z": 3.5,
                        "notional": 10000,
                        "bt_days": 0,
                    },
                )
            except ArbError as exc2:
                raise HTTPException(status_code=400, detail=str(exc2)) from exc2
        else:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)
