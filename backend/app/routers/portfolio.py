from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.portfolio import PortfolioError, analyze_portfolio, credit_migration

router = APIRouter()


class PortfolioLeg(BaseModel):
    symbol: str
    weight: float | None = None
    amount: float | None = None
    side: str | None = Field("long", description="long|short 或 做多|做空")


class PortfolioRequest(BaseModel):
    legs: list[PortfolioLeg] = Field(..., min_length=1, max_length=20)
    window: int = Field(252, ge=30, le=1000)
    confidence: float = 0.95
    benchmark: str | None = None
    capital: float | None = Field(None, ge=0)
    horizon_days: int = Field(63, ge=1, le=252, description="概率展望交易日数")
    n_sims: int = Field(4000, ge=500, le=20000)
    drift_mode: str = Field("historical", description="historical=保留样本漂移; zero=去均值风险视角")
    loss_limit: float = Field(0.05, ge=0.005, le=0.5, description="安全仓位最大回撤硬约束，默认5%")


class CreditMigrateRequest(BaseModel):
    years: int = Field(10, ge=1, le=50)
    notional: float = Field(10_000_000_000.0, ge=0)
    start: dict[str, float] | None = None


@router.post("")
def portfolio_analyze(payload: PortfolioRequest):
    try:
        return analyze_portfolio(
            [leg.model_dump() for leg in payload.legs],
            window=payload.window,
            confidence=payload.confidence,
            benchmark=payload.benchmark,
            capital=payload.capital,
            horizon_days=payload.horizon_days,
            n_sims=payload.n_sims,
            drift_mode=payload.drift_mode,
            loss_limit=payload.loss_limit,
        )
    except PortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/credit-migrate")
def portfolio_credit_migrate(payload: CreditMigrateRequest):
    try:
        return credit_migration(
            years=payload.years,
            start=payload.start,
            notional=payload.notional,
        )
    except PortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
