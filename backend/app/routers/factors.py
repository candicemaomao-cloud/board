from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.factor_exposure import FactorError, analyze_factor_exposure

router = APIRouter()


class ScenarioShocks(BaseModel):
    SPY: float | None = None
    QQQ: float | None = None
    SOXX: float | None = None
    TNX: float | None = None
    DXY: float | None = None
    VIX: float | None = None
    USO: float | None = None
    GLD: float | None = None


class FactorExposureRequest(BaseModel):
    symbol: str
    window: int = Field(252, ge=60, le=1000)
    roll_window: int = Field(126, ge=40, le=500)
    shocks: ScenarioShocks | None = None


@router.post("/exposure")
def factor_exposure(payload: FactorExposureRequest):
    try:
        shocks = None
        if payload.shocks is not None:
            shocks = {k: v for k, v in payload.shocks.model_dump().items() if v is not None}
        return analyze_factor_exposure(
            payload.symbol,
            window=payload.window,
            roll_window=payload.roll_window,
            shocks=shocks or None,
        )
    except FactorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
