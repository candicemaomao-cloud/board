from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.arb_strategy import ArbError, create_arb, to_out
from app.services.intraday_scan import IntradayScanError, backtest_residual, job_status, peek_scan, start_scan, universe
from app.services.intraday_universe import clip_hedges

router = APIRouter()


class ScanBody(BaseModel):
    force: bool = True
    baskets: list[str] | None = None
    timeframe: str = "5m"


class BacktestBody(BaseModel):
    target: str
    factors: list[str]
    timeframe: str = "5m"
    entry_z: float = 2.5
    exit_z: float = 0.75
    stop_z: float = 4.0
    notional: float = 10000


class ToStrategyBody(BaseModel):
    target: str
    factors: list[str] | None = None
    timeframe: str = "5m"
    name: str | None = None
    notes: str | None = None
    lookback: int = 390


@router.get("/universe")
def intraday_universe():
    return universe()


@router.get("/scan")
def intraday_cached():
    return peek_scan()


@router.get("/scan/status")
def intraday_status():
    return job_status()


@router.post("/scan")
def intraday_run(payload: ScanBody | None = None):
    body = payload or ScanBody()
    return start_scan(force=body.force, baskets=body.baskets, timeframe=body.timeframe)


@router.post("/backtest")
def intraday_backtest(payload: BacktestBody):
    try:
        return backtest_residual(
            target=payload.target,
            factors=payload.factors,
            timeframe=payload.timeframe,
            entry_z=payload.entry_z,
            exit_z=payload.exit_z,
            stop_z=payload.stop_z,
            notional=payload.notional,
        )
    except IntradayScanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/to-strategy")
def intraday_to_strategy(payload: ToStrategyBody, db: Session = Depends(get_db)):
    a = payload.target.strip().upper()
    factors = clip_hedges(payload.factors or [], a)
    b = factors[0] if factors else ""
    if not a or not b:
        raise HTTPException(status_code=400, detail="需要标的和一个对冲，优先两只腿")
    tf = payload.timeframe if payload.timeframe in {"5m", "30m"} else "5m"
    hedge = "+".join(factors)
    name = (payload.name or f"{a} {tf} vs {hedge}")[:64]
    notes = payload.notes or (
        f"日内配对：{a} vs {hedge}。优先两只标的，最多三只。"
        f"用 {tf} K 线，对冲腿少是为了压手续费。"
    )
    body = {
        "name": name,
        "notes": notes,
        "kind": "ols",
        "timeframe": tf,
        "leg_a": a,
        "leg_b": b,
        "lookback": payload.lookback or (40 if tf == "30m" else 390),
        "entry_z": 2.5,
        "exit_z": 0.75,
        "stop_z": 4.0,
        "notional": 10000,
        "bt_days": 0,
        "factors": factors,
    }
    try:
        row = create_arb(db, body)
    except ArbError as exc:
        if "同名" in str(exc):
            body["name"] = f"{a}/{b} 日内"[:64]
            try:
                row = create_arb(db, body)
            except ArbError as exc2:
                raise HTTPException(status_code=400, detail=str(exc2)) from exc2
        else:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row)
