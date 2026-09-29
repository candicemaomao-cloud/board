from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.user_stocks.options import analyze_option_chain

router = APIRouter()


@router.get("")
def options_analysis(symbol: str, expiration: str | None = None, risk_free_rate: float = 0.045):
    try:
        return analyze_option_chain(symbol, expiration=expiration, risk_free_rate=risk_free_rate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"期权数据抓取失败: {exc}") from exc
