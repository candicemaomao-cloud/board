from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.risk_portfolios import (
    RiskPortfolioError,
    create_portfolio,
    delete_portfolio,
    get_portfolio,
    list_portfolios,
    list_runs,
    run_crisis_scan,
    set_enabled,
    to_out,
    update_portfolio,
)

router = APIRouter()


class LegIn(BaseModel):
    symbol: str
    weight: float | None = None
    amount: float | None = None
    side: str | None = "long"
    entry_price: float | None = Field(None, gt=0, description="买入成本价；有则危机系数按成本计价")


class RiskPortfolioBody(BaseModel):
    name: str
    legs: list[LegIn] = Field(..., min_length=1, max_length=20)
    notes: str | None = None
    loss_limit: float = Field(0.05, ge=0.005, le=0.5)
    window: int = Field(252, ge=30, le=1000)
    horizon_days: int = Field(21, ge=1, le=252)
    enabled: bool = False
    budget_capital: float | None = Field(None, gt=0, description="预算本金 $")
    budget_target_pnl: float | None = Field(None, description="可选手动覆盖；有持仓日则自动用展望中位")
    budget_days: int = Field(30, ge=7, le=120)
    budget_model_pnl: float | None = Field(None, description="模型中位预期盈利 $")
    entry_date: str | None = Field(None, description="持仓日期 YYYY-MM-DD，用当天收盘作展望起点")
    budget_model_id: str = Field("block_bootstrap", description="预算模型 id")


class RiskPortfolioPatch(BaseModel):
    name: str | None = None
    legs: list[LegIn] | None = None
    notes: str | None = None
    loss_limit: float | None = Field(None, ge=0.005, le=0.5)
    window: int | None = Field(None, ge=30, le=1000)
    horizon_days: int | None = Field(None, ge=1, le=252)
    enabled: bool | None = None
    budget_capital: float | None = Field(None, gt=0)
    budget_target_pnl: float | None = None
    budget_days: int | None = Field(None, ge=7, le=120)
    budget_model_pnl: float | None = None
    reset_budget: bool | None = Field(None, description="true=重设预算起点")
    entry_date: str | None = None
    budget_model_id: str | None = None


class EnableBody(BaseModel):
    enabled: bool


@router.get("")
def api_list(db: Session = Depends(get_db)):
    rows = list_portfolios(db)
    items = []
    for row in rows:
        runs = list_runs(db, row.id, limit=7)
        items.append(to_out(row, runs=runs))
    return {"items": items}


@router.get("/{pid}")
def api_get(pid: int, db: Session = Depends(get_db)):
    row = get_portfolio(db, pid)
    if not row:
        raise HTTPException(status_code=404, detail="组合不存在")
    return to_out(row, runs=list_runs(db, row.id, limit=30))


@router.post("")
def api_create(payload: RiskPortfolioBody, db: Session = Depends(get_db)):
    try:
        row = create_portfolio(
            db,
            name=payload.name,
            legs=[x.model_dump() for x in payload.legs],
            notes=payload.notes,
            loss_limit=payload.loss_limit,
            window=payload.window,
            horizon_days=payload.horizon_days,
            enabled=payload.enabled,
            budget_capital=payload.budget_capital,
            budget_target_pnl=payload.budget_target_pnl,
            budget_days=payload.budget_days,
            budget_model_pnl=payload.budget_model_pnl,
            entry_date=payload.entry_date,
            budget_model_id=payload.budget_model_id,
        )
        return to_out(row)
    except RiskPortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{pid}")
def api_update(pid: int, payload: RiskPortfolioPatch, db: Session = Depends(get_db)):
    row = get_portfolio(db, pid)
    if not row:
        raise HTTPException(status_code=404, detail="组合不存在")
    try:
        legs = [x.model_dump() for x in payload.legs] if payload.legs is not None else None
        data = payload.model_dump(exclude_unset=True)
        row = update_portfolio(
            db,
            row,
            name=payload.name,
            legs=legs,
            notes=payload.notes,
            loss_limit=payload.loss_limit,
            window=payload.window,
            horizon_days=payload.horizon_days,
            enabled=payload.enabled,
            budget_capital=data["budget_capital"] if "budget_capital" in data else None,
            budget_target_pnl=data["budget_target_pnl"] if "budget_target_pnl" in data else None,
            budget_days=data["budget_days"] if "budget_days" in data else None,
            budget_model_pnl=data["budget_model_pnl"] if "budget_model_pnl" in data else None,
            reset_budget=data.get("reset_budget"),
            entry_date=data["entry_date"] if "entry_date" in data else None,
            budget_model_id=data.get("budget_model_id"),
        )
        return to_out(row, runs=list_runs(db, row.id, limit=7))
    except RiskPortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{pid}/enable")
def api_enable(pid: int, payload: EnableBody, db: Session = Depends(get_db)):
    row = get_portfolio(db, pid)
    if not row:
        raise HTTPException(status_code=404, detail="组合不存在")
    row = set_enabled(db, row, payload.enabled)
    # 刚开启时立刻扫一次，方便列表马上看到危机系数
    if payload.enabled:
        try:
            row = run_crisis_scan(db, row, force=True, n_sims=1200)
        except RiskPortfolioError as exc:
            return {**to_out(row), "scan_error": str(exc)}
    return to_out(row, runs=list_runs(db, row.id, limit=7))


@router.post("/{pid}/scan")
def api_scan(pid: int, db: Session = Depends(get_db)):
    row = get_portfolio(db, pid)
    if not row:
        raise HTTPException(status_code=404, detail="组合不存在")
    try:
        row = run_crisis_scan(db, row, force=True, n_sims=2000)
        return to_out(row, runs=list_runs(db, row.id, limit=30))
    except RiskPortfolioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{pid}")
def api_delete(pid: int, db: Session = Depends(get_db)):
    row = get_portfolio(db, pid)
    if not row:
        raise HTTPException(status_code=404, detail="组合不存在")
    delete_portfolio(db, row)
    return {"ok": True}
