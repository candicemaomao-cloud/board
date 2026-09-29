from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from app.services.ohlc import OhlcError
from app.services.screener import analyze_regression

router = APIRouter()


@router.get("/regression")
def regression(
    symbol: str,
    ma1: int = 7,
    ma2: int = 8,
    start: date | None = None,
    end: date | None = None,
    timeframe: str = "daily",
    price_mode: str = "price",
    exclude_events: bool = False,
):
    try:
        return analyze_regression(symbol, ma1=ma1, ma2=ma2, start=start, end=end,
                                  timeframe=timeframe, price_mode=price_mode,
                                  exclude_events=exclude_events)
    except OhlcError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
