from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services.fundamentals import FundamentalsError, analyze

router = APIRouter()


@router.get("")
def fundamentals(symbol: str):
    try:
        return analyze(symbol)
    except FundamentalsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
