from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.binance import BinanceError, parse_symbols, public_quote, sync_binance
from app.services.goal import get_settings

router = APIRouter()


class SyncBody(BaseModel):
    days: int = 90
    futures: bool = True
    spot: bool = False
    symbols: str | None = None


@router.get("/quote")
def quote(symbol: str = Query(default="SKHY")):
    try:
        return public_quote(symbol)
    except BinanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sync")
def sync(payload: SyncBody, db: Session = Depends(get_db)):
    row = get_settings(db)
    symbols = parse_symbols(payload.symbols or row.binance_symbols or "SKHY")
    try:
        return sync_binance(
            db,
            row.binance_api_key or "",
            row.binance_api_secret or "",
            symbols,
            days=max(1, min(payload.days, 180)),
            futures=payload.futures,
            spot=payload.spot,
        )
    except BinanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
