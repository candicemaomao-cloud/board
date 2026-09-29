from __future__ import annotations

import json

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Position, Strategy
from app.services.indicator_code import IndicatorCodeError, is_code_formula, normalize_code_formula
from app.services.strategy_calc import TIMEFRAMES
from app.services.strategy_spec import formula_text, normalize_formula, ref_ids

SIDES = {"long", "short"}
TF_ALIASES = {
    "d": "1d",
    "d线": "1d",
    "1d线": "1d",
    "day": "1d",
    "daily": "1d",
    "5min": "5m",
    "30min": "30m",
}


class StrategyError(Exception):
    pass


def _dump(formula: dict) -> str:
    return json.dumps(formula, ensure_ascii=False)


def _load(raw: str | None) -> dict:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


def name_map(rows: list[Strategy]) -> dict[int, str]:
    return {row.id: row.name for row in rows}


def formula_library(rows: list[Strategy]) -> dict[int, dict]:
    return {row.id: _load(row.formula) for row in rows}


def normalize_side(raw) -> str:
    value = str(raw or "long").strip().lower()
    if value in {"short", "sell", "做空", "空"}:
        return "short"
    return "long"


def normalize_timeframe(raw) -> str:
    value = str(raw or "1d").strip().lower()
    value = TF_ALIASES.get(value, value)
    if value not in TIMEFRAMES:
        raise StrategyError("K线周期只能是 5min / 30min / 4h / D线")
    return value


def plan_timeframe(row: Strategy) -> str:
    try:
        return normalize_timeframe(getattr(row, "timeframe", None))
    except StrategyError:
        return "1d"


def to_out(row: Strategy, names: dict[int, str] | None = None) -> dict:
    formula = _load(row.formula)
    if is_code_formula(formula):
        text = "代码指标 · 返回 True/False"
    else:
        text = formula_text(formula, names, side=normalize_side(getattr(row, "side", None)))
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "notes": row.notes or "",
        "side": normalize_side(getattr(row, "side", None)),
        "timeframe": plan_timeframe(row),
        "kind": "code" if is_code_formula(formula) else "formula",
        "formula": formula,
        "text": text,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def list_strategies(db: Session, user_id: int | None = None) -> list[Strategy]:
    stmt = select(Strategy).order_by(Strategy.id.desc())
    if user_id is not None:
        stmt = stmt.where(Strategy.user_id == user_id)
    return list(db.scalars(stmt))


def get_strategy(db: Session, strategy_id: int, user_id: int | None = None) -> Strategy | None:
    row = db.get(Strategy, strategy_id)
    if not row:
        return None
    if user_id is not None and row.user_id != user_id:
        return None
    return row


def _assert_refs(db: Session, user_id: int, spec: dict, self_id: int | None) -> None:
    rows = list_strategies(db, user_id=user_id)
    library = formula_library(rows)
    existing = {row.id for row in rows}
    if self_id in ref_ids(spec):
        raise StrategyError("不能引用自己")
    if any(sid not in existing for sid in ref_ids(spec)):
        raise StrategyError("引用的策略不存在或不属于当前用户")
    if self_id:
        library[self_id] = spec

    def walk(sid: int, stack: set[int]) -> None:
        if sid in stack:
            raise StrategyError("策略不能循环引用")
        nested = library.get(sid)
        if not nested:
            raise StrategyError("引用的策略不存在或不属于当前用户")
        stack.add(sid)
        for ref in ref_ids(nested):
            walk(ref, stack)
        stack.discard(sid)

    start = {self_id} if self_id else set()
    for ref in ref_ids(spec):
        walk(ref, set(start))


def _normalize_spec(formula: dict | None) -> dict:
    if is_code_formula(formula) or (isinstance(formula, dict) and formula.get("code") and not formula.get("groups")):
        try:
            return normalize_code_formula(
                formula if is_code_formula(formula) else {"kind": "code", **(formula or {})}
            )
        except IndicatorCodeError as exc:
            raise StrategyError(str(exc)) from exc
    try:
        return normalize_formula(formula)
    except ValueError as exc:
        raise StrategyError(str(exc)) from exc


def create_strategy(
    db: Session,
    *,
    user_id: int,
    name: str,
    notes: str | None,
    formula: dict | None,
    side: str | None = None,
    timeframe: str | None = None,
) -> Strategy:
    title = (name or "").strip()
    if not title:
        raise StrategyError("请填写策略名")
    if db.scalar(select(Strategy).where(Strategy.user_id == user_id, Strategy.name == title)):
        raise StrategyError("你已经有同名指标")
    spec = _normalize_spec(formula)
    if not is_code_formula(spec):
        _assert_refs(db, user_id, spec, None)
    row = Strategy(
        user_id=user_id,
        name=title[:64],
        notes=(notes or "").strip() or None,
        formula=_dump(spec),
        side=normalize_side(side),
        timeframe=normalize_timeframe(timeframe),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_strategy(
    db: Session,
    row: Strategy,
    *,
    name: str,
    notes: str | None,
    formula: dict | None,
    side: str | None = None,
    timeframe: str | None = None,
) -> Strategy:
    title = (name or "").strip()
    if not title:
        raise StrategyError("请填写策略名")
    uid = row.user_id
    other = db.scalar(
        select(Strategy).where(Strategy.user_id == uid, Strategy.name == title, Strategy.id != row.id)
    )
    if other:
        raise StrategyError("你已经有同名指标")
    spec = _normalize_spec(formula)
    if not is_code_formula(spec) and uid is not None:
        _assert_refs(db, uid, spec, row.id)
    row.name = title[:64]
    row.notes = (notes or "").strip() or None
    row.formula = _dump(spec)
    row.side = normalize_side(side)
    row.timeframe = normalize_timeframe(timeframe)
    db.commit()
    db.refresh(row)
    return row


def referenced_by(db: Session, strategy_id: int, user_id: int | None = None) -> list[str]:
    names = []
    for row in list_strategies(db, user_id=user_id):
        if row.id == strategy_id:
            continue
        if strategy_id in ref_ids(_load(row.formula)):
            names.append(row.name)
    return names


def delete_strategy(db: Session, row: Strategy) -> None:
    used = referenced_by(db, row.id, user_id=row.user_id)
    if used:
        raise StrategyError(f"还有指标在用它：{'、'.join(used)}")
    db.execute(update(Position).where(Position.strategy_id == row.id).values(strategy_id=None))
    db.delete(row)
    db.commit()
