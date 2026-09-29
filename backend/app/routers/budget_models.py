from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.budget_models import (
    BudgetModelError,
    get_budget_model,
    list_budget_models,
    run_budget_model,
    solve_target_entry,
)
from app.services.kakeya_budget import list_kakeya_directions, run_kakeya_model

router = APIRouter()


class BudgetLeg(BaseModel):
    symbol: str
    weight: float | None = None
    amount: float | None = None
    side: str | None = "long"
    entry_price: float | None = None


class BudgetRunRequest(BaseModel):
    model_id: str = Field("block_bootstrap", description="预算模型 id")
    symbols: list[str] | None = Field(None, description="股票代码，多个；与 legs 二选一")
    legs: list[BudgetLeg] | None = None
    horizon_days: int = Field(21, ge=1, le=252, description="预计持仓交易日数")
    capital: float = Field(10000, gt=0)
    window: int = Field(252, ge=60, le=1000)
    n_sims: int = Field(2000, ge=800, le=8000)
    drift_mode: str = Field("historical")
    loss_limit: float = Field(0.05, ge=0.005, le=0.5)


class TargetEntryRequest(BaseModel):
    symbols: list[str] | None = None
    symbol: str | None = None
    target_return: float = Field(0.05, gt=-0.5, le=1.0, description="目标收益率，如 0.05=一个月赚5%")
    horizon_days: int = Field(21, ge=5, le=126)
    window: int = Field(252, ge=60, le=1000)
    n_sims: int = Field(3000, ge=1000, le=8000)
    drift_mode: str = Field("historical")
    target_buy_price: float | None = Field(
        None,
        gt=0,
        description="可选：你的目标买入价；不填则用模型最优买点",
    )


class KakeyaShockIn(BaseModel):
    id: str
    enabled: bool | None = None
    value: float | None = None
    vol_mult: float | None = None
    gap: float | None = None
    left_tail: float | None = None


class KakeyaLegIn(BaseModel):
    symbol: str
    weight: float | None = None
    amount: float | None = None
    side: str | None = "long"


class KakeyaRunRequest(BaseModel):
    symbols: list[str] | None = None
    symbol: str | None = None
    legs: list[KakeyaLegIn] | None = Field(None, description="组合腿（多空）；有则走组合对冲对比模式")
    hedge: bool = Field(True, description="组合模式：相对基准去 beta 对冲前后对比")
    hedge_benchmark: str = Field("SPY", description="对冲基准")
    horizon_days: int = Field(21, ge=5, le=126)
    capital: float = Field(10000, gt=0)
    window: int = Field(252, ge=60, le=1000)
    n_sims: int = Field(2000, ge=800, le=6000)
    loss_limit: float = Field(0.05, ge=0.005, le=0.5)
    shocks: list[KakeyaShockIn] | None = None


@router.get("")
def budget_models_list():
    return {"items": list_budget_models(), "default": "block_bootstrap"}


@router.get("/kakeya/directions")
def kakeya_directions():
    return {"items": list_kakeya_directions()}


@router.post("/kakeya/run")
def kakeya_run(payload: KakeyaRunRequest):
    try:
        return run_kakeya_model(
            symbols=payload.symbols,
            symbol=payload.symbol,
            legs=[x.model_dump() for x in payload.legs] if payload.legs else None,
            hedge=payload.hedge,
            hedge_benchmark=payload.hedge_benchmark,
            horizon_days=payload.horizon_days,
            capital=payload.capital,
            window=payload.window,
            n_sims=payload.n_sims,
            loss_limit=payload.loss_limit,
            shocks=[s.model_dump() for s in payload.shocks] if payload.shocks else None,
        )
    except BudgetModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/run")
def budget_models_run(payload: BudgetRunRequest):
    try:
        return run_budget_model(
            model_id=payload.model_id,
            symbols=payload.symbols,
            legs=[leg.model_dump() for leg in payload.legs] if payload.legs else None,
            horizon_days=payload.horizon_days,
            capital=payload.capital,
            window=payload.window,
            n_sims=payload.n_sims,
            drift_mode=payload.drift_mode,
            loss_limit=payload.loss_limit,
        )
    except BudgetModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/target-entry")
def budget_target_entry(payload: TargetEntryRequest):
    """目标收益 → 买入价 + 回撤触及概率/时间。"""
    try:
        return solve_target_entry(
            symbols=payload.symbols,
            symbol=payload.symbol,
            target_return=payload.target_return,
            horizon_days=payload.horizon_days,
            window=payload.window,
            n_sims=payload.n_sims,
            drift_mode=payload.drift_mode,
            target_buy_price=payload.target_buy_price,
        )
    except BudgetModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{model_id}")
def budget_model_detail(model_id: str):
    try:
        return get_budget_model(model_id)
    except BudgetModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
