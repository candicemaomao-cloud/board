from fastapi import APIRouter, HTTPException, Query

from app.services.risk import RiskError, analyze_risk

router = APIRouter()


@router.get("")
def risk_model(
    symbol: str = Query(..., description="股票/合约代码，如 TSLA"),
    window: int = Query(252, ge=30, le=1000, description="回溯交易日数"),
    confidence: float = Query(0.95, description="VaR/CVaR 置信度 0.90/0.95/0.99"),
    benchmark: str | None = Query(None, description="Beta 基准，默认 SPY / 币用 BTCUSDT"),
):
    try:
        return analyze_risk(symbol, window=window, confidence=confidence, benchmark=benchmark)
    except RiskError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
