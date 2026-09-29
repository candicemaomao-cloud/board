from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.auth import require_user
from app.services.indicator_scan import SCAN_TIMEFRAMES, ScanError, scan_hits
from app.services.ohlc import OhlcError
from app.services.strategy_calc import TIMEFRAMES, CalcError, calculate
from app.services.strategy_plans import (
    StrategyError,
    create_strategy,
    delete_strategy,
    formula_library,
    get_strategy,
    list_strategies,
    name_map,
    to_out,
    update_strategy,
)
from app.services.strategy_spec import BUY_TARGETS, INDICATORS, empty_formula
from app.services.user_stock_files import (
    get_file_plan,
    is_file_id,
    run_file_stock,
)

router = APIRouter()


class CalcBody(BaseModel):
    symbol: str
    timeframe: str = "1d"
    strategy_id: str | int | None = None


class PlanBody(BaseModel):
    name: str
    notes: str | None = None
    side: str = "long"
    timeframe: str = "1d"
    formula: dict | None = None


class ScanBody(BaseModel):
    symbol: str
    timeframe: str = "1d"
    start: str | None = None
    end: str | None = None
    indicators: list[str] | None = None
    strategy_id: str | int | None = None
    plan_ids: list[int] | None = None


@router.get("/indicators")
def indicators():
    from app.services.indicator_code import CODE_TEMPLATE

    groups: dict[str, list] = {}
    for row in INDICATORS:
        groups.setdefault(row["group"], []).append(row)
    return {
        "items": INDICATORS,
        "groups": [{"name": name, "items": items} for name, items in groups.items()],
        "empty_formula": empty_formula(),
        "targets": BUY_TARGETS,
        "code_template": CODE_TEMPLATE,
    }


@router.get("/plans")
def plans(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = list_strategies(db, user_id=user.id)
    names = name_map(rows)
    # 只返回当前用户的指标；手写文件策略仍可选（不占用「我的指标」用户隔离）
    return [to_out(row, names) for row in rows]


@router.post("/plans")
def add_plan(payload: PlanBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    try:
        row = create_strategy(
            db,
            user_id=user.id,
            name=payload.name,
            notes=payload.notes,
            formula=payload.formula,
            side=payload.side,
            timeframe=payload.timeframe,
        )
    except StrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = list_strategies(db, user_id=user.id)
    return to_out(row, name_map(rows))


def _db_id(plan_id) -> int | None:
    if plan_id is None or is_file_id(plan_id):
        return None
    try:
        return int(plan_id)
    except (TypeError, ValueError):
        return None


def _resolve_plan(plan_id, db: Session, user_id: int | None = None) -> dict | None:
    if plan_id is None or plan_id == "":
        return None
    if is_file_id(plan_id):
        return get_file_plan(str(plan_id))
    nid = _db_id(plan_id)
    if nid is None:
        return None
    row = get_strategy(db, nid, user_id=user_id)
    if not row:
        return None
    rows = list_strategies(db, user_id=user_id if user_id is not None else row.user_id)
    return to_out(row, name_map(rows))


@router.put("/plans/{plan_id}")
def edit_plan(
    plan_id: str,
    payload: PlanBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if is_file_id(plan_id):
        raise HTTPException(status_code=400, detail="手写策略请改 backend/app/user_stocks/ 下的文件")
    nid = _db_id(plan_id)
    row = get_strategy(db, nid, user_id=user.id) if nid is not None else None
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    try:
        row = update_strategy(
            db,
            row,
            name=payload.name,
            notes=payload.notes,
            formula=payload.formula,
            side=payload.side,
            timeframe=payload.timeframe,
        )
    except StrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = list_strategies(db, user_id=user.id)
    return to_out(row, name_map(rows))


@router.delete("/plans/{plan_id}", status_code=204)
def remove_plan(plan_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if is_file_id(plan_id):
        raise HTTPException(status_code=400, detail="手写策略请直接删 backend/app/user_stocks/ 里对应的 .py")
    nid = _db_id(plan_id)
    row = get_strategy(db, nid, user_id=user.id) if nid is not None else None
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    try:
        delete_strategy(db, row)
    except StrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/calc")
def calc(payload: CalcBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if payload.timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="周期只能是 D / 4h / 30min / 5min")
    try:
        if payload.strategy_id and is_file_id(payload.strategy_id):
            return run_file_stock(str(payload.strategy_id), payload.symbol, payload.timeframe)
        plan = None
        formula = None
        rows = list_strategies(db, user_id=user.id)
        names = name_map(rows)
        library = formula_library(rows)
        if payload.strategy_id:
            plan = _resolve_plan(payload.strategy_id, db, user_id=user.id)
            if not plan:
                raise HTTPException(status_code=404, detail="策略不存在")
            formula = plan["formula"]
        result = calculate(
            payload.symbol,
            payload.timeframe,
            formula,
            library,
            names,
            trade_side=(plan or {}).get("side"),
        )
    except StrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CalcError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OhlcError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["strategy"] = plan
    return result


class IntradayBtBody(BaseModel):
    symbol: str
    days: int = 10
    budget: float = 5000
    hedge: bool = False
    integral: bool = True
    gradient: bool = True
    day_filter: bool = True
    vol_adapt: bool = True
    allow_short: bool = True
    allow_trend: bool = False
    vol_confirm: bool = False
    vol_z_th: float = 1.0
    kelly: bool = False
    hedge_symbol: str = "SPY"
    wma_filter: bool = False
    wma_period: int = 20


@router.post("/intraday-bt")
def intraday_bt(payload: IntradayBtBody):
    from app.services.intraday_calculus import run_intraday_bt

    try:
        return run_intraday_bt(
            payload.symbol,
            days=payload.days,
            budget=payload.budget,
            hedge=payload.hedge,
            integral=payload.integral,
            gradient=payload.gradient,
            day_filter=payload.day_filter,
            vol_adapt=payload.vol_adapt,
            allow_short=payload.allow_short,
            allow_trend=payload.allow_trend,
            vol_confirm=payload.vol_confirm,
            vol_z_th=payload.vol_z_th,
            kelly=payload.kelly,
            hedge_symbol=payload.hedge_symbol,
            wma_filter=payload.wma_filter,
            wma_period=payload.wma_period,
        )
    except OhlcError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class IntradayPoolBtBody(BaseModel):
    symbols: list[str]
    days: int = 10
    budget: float = 5000
    hedge: bool = False
    integral: bool = True
    gradient: bool = True
    day_filter: bool = True
    vol_adapt: bool = True
    allow_short: bool = True
    allow_trend: bool = False
    vol_confirm: bool = False
    vol_z_th: float = 1.0
    kelly: bool = False
    hedge_symbol: str = "SPY"
    wma_filter: bool = False
    wma_period: int = 20


@router.post("/intraday-bt-pool")
def intraday_bt_pool(payload: IntradayPoolBtBody):
    from app.services.intraday_calculus import run_pool_bt

    try:
        return run_pool_bt(
            payload.symbols,
            days=payload.days,
            budget=payload.budget,
            hedge=payload.hedge,
            integral=payload.integral,
            gradient=payload.gradient,
            day_filter=payload.day_filter,
            vol_adapt=payload.vol_adapt,
            allow_short=payload.allow_short,
            allow_trend=payload.allow_trend,
            vol_confirm=payload.vol_confirm,
            vol_z_th=payload.vol_z_th,
            kelly=payload.kelly,
            hedge_symbol=payload.hedge_symbol,
            wma_filter=payload.wma_filter,
            wma_period=payload.wma_period,
        )
    except OhlcError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/scan")
def scan(payload: ScanBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if payload.timeframe not in SCAN_TIMEFRAMES:
        raise HTTPException(status_code=400, detail="周期只能是 日 / 周 / 4h / 30min / 5min")
    formula = None
    plan = None
    if payload.strategy_id:
        try:
            plan = _resolve_plan(payload.strategy_id, db, user_id=user.id)
        except StrategyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not plan:
            raise HTTPException(status_code=404, detail="策略不存在")
        formula = plan["formula"]

    user_plans: list[dict] = []
    seen_plan: set[int] = set()
    for raw in payload.plan_ids or []:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid in seen_plan:
            continue
        row = get_strategy(db, pid, user_id=user.id)
        if not row:
            raise HTTPException(status_code=404, detail=f"指标不存在：{pid}")
        seen_plan.add(pid)
        out = to_out(row)
        user_plans.append(
            {
                "id": out["id"],
                "name": out["name"],
                "kind": out.get("kind") or "formula",
                "formula": out["formula"],
            }
        )
    # 下拉选的组合若未勾进 plan_ids，仍单独统计一次
    if plan and plan.get("id") is not None:
        try:
            sid = int(plan["id"])
        except (TypeError, ValueError):
            sid = None
        if sid is not None and sid not in seen_plan:
            user_plans.append(
                {
                    "id": sid,
                    "name": plan.get("name") or f"指标{sid}",
                    "kind": plan.get("kind") or "formula",
                    "formula": plan.get("formula") or formula,
                }
            )
            formula = None  # 已并入 user_plans，避免重复

    try:
        result = scan_hits(
            payload.symbol,
            payload.timeframe,
            payload.start,
            payload.end,
            payload.indicators,
            formula,
            user_plans=user_plans,
        )
    except ScanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OhlcError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["strategy"] = plan
    return result
