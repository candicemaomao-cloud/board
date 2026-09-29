from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Position, User
from app.schemas import PositionClose, PositionClosedRecordUpdate, PositionCreate, PositionOut, PositionUpdate
from app.services.auth import has_perm, require_user
from app.services.positions import (
    PositionError,
    close_position,
    create_position,
    edit_closed_record,
    estimate_position_outlook,
    list_positions,
    to_out,
    update_position,
)

router = APIRouter()


def _own(row: Position | None, user: User) -> Position:
    if not row:
        raise HTTPException(status_code=404, detail="持仓不存在")
    if row.user_id and row.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=404, detail="持仓不存在")
    if user.role != "admin" and row.user_id != user.id:
        raise HTTPException(status_code=404, detail="持仓不存在")
    return row


class PositionOutlookRequest(BaseModel):
    name: str | None = Field(None, max_length=64, description="单票代码；有 risk_portfolio_id 时可空")
    platform: str = "众安"
    shares: float | None = Field(None, gt=0)
    open_price: float | None = Field(None, gt=0)
    capital: float | None = Field(None, gt=0, description="本金；不填则用股份×开仓价")
    expected_days: int = Field(21, ge=1, le=252)
    side: str = Field("long", description="long|short")
    window: int = Field(252, ge=60, le=504)
    n_sims: int = Field(2000, ge=800, le=4000)
    risk_portfolio_id: int | None = Field(None, description="选风险组合则按组合腿测算")


@router.get("", response_model=list[PositionOut])
def read_positions(
    platform: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    # 现有持仓归 admin；admin 登录后看到自己的（含历史）持仓
    rows = list_positions(db, platform, status, user_id=user.id)
    return [PositionOut.model_validate(to_out(row)) for row in rows]


@router.post("/outlook")
def position_outlook(payload: PositionOutlookRequest, db: Session = Depends(get_db)):
    """记持仓时：按预计持仓天数反算中位收益；可选风险组合腿。"""
    try:
        capital = payload.capital
        if capital is None:
            if payload.shares and payload.open_price:
                capital = float(payload.shares) * float(payload.open_price)
            else:
                capital = None

        legs = None
        portfolio_name = None
        if payload.risk_portfolio_id:
            from app.services.risk_portfolios import get_portfolio, _parse_legs

            row = get_portfolio(db, payload.risk_portfolio_id)
            if not row:
                raise PositionError("风险组合不存在")
            legs = _parse_legs(row.legs)
            portfolio_name = row.name
            if capital is None or capital <= 0:
                capital = float(row.budget_capital or 0) or None
            if capital is None or capital <= 0:
                raise PositionError("请填写本金，或给风险组合设置预算本金")
            hz = payload.expected_days or int(row.horizon_days or 21)
            win = payload.window or int(row.window or 252)
            return estimate_position_outlook(
                capital=capital,
                horizon_days=hz,
                window=win,
                n_sims=payload.n_sims,
                legs=legs,
                portfolio_name=portfolio_name,
                open_price=payload.open_price,
            )

        if not payload.name:
            raise PositionError("请填写股票代码，或选择风险组合")
        if capital is None or capital <= 0:
            raise PositionError("请填写开仓价格和股份")
        return estimate_position_outlook(
            name=payload.name,
            platform=payload.platform,
            capital=capital,
            horizon_days=payload.expected_days,
            side=payload.side,
            open_price=payload.open_price,
            window=payload.window,
            n_sims=payload.n_sims,
        )
    except PositionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=PositionOut, status_code=201)
def add_position(
    payload: PositionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not has_perm(user, "btn.positions.create"):
        raise HTTPException(status_code=403, detail="无权限：持仓新增")
    try:
        row = create_position(
            db,
            platform=payload.platform,
            status=payload.status,
            name=payload.name,
            shares=payload.shares,
            open_price=payload.open_price,
            fee=payload.fee,
            opened_on=payload.opened_on,
            expected_days=payload.expected_days,
            notes=payload.notes,
            open_reason=payload.open_reason,
            strategy_id=payload.strategy_id,
            user_id=user.id,
        )
    except PositionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PositionOut.model_validate(to_out(row))


@router.put("/{position_id}", response_model=PositionOut)
def edit_position(
    position_id: int,
    payload: PositionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not has_perm(user, "btn.positions.edit"):
        raise HTTPException(status_code=403, detail="无权限：持仓编辑")
    row = _own(db.get(Position, position_id), user)
    try:
        row = update_position(db, row, payload.model_dump(exclude_unset=True))
    except PositionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PositionOut.model_validate(to_out(row))


@router.put("/{position_id}/closed-record", response_model=PositionOut)
def edit_position_closed_record(
    position_id: int,
    payload: PositionClosedRecordUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """已结束的持仓专用：只能改开仓记录和关仓记录，其他字段不接受。"""
    if not has_perm(user, "btn.positions.edit"):
        raise HTTPException(status_code=403, detail="无权限：持仓编辑")
    row = _own(db.get(Position, position_id), user)
    try:
        row = edit_closed_record(db, row, payload.model_dump())
    except PositionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PositionOut.model_validate(to_out(row))


@router.post("/{position_id}/close", response_model=PositionOut)
def end_position(
    position_id: int,
    payload: PositionClose,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not has_perm(user, "btn.positions.close"):
        raise HTTPException(status_code=403, detail="无权限：持仓平仓")
    row = _own(db.get(Position, position_id), user)
    try:
        row = close_position(
            db,
            row,
            close_amount=payload.close_amount,
            fee=payload.fee,
            closed_on=payload.closed_on,
            notes=payload.notes,
            close_reason=payload.close_reason,
        )
    except PositionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PositionOut.model_validate(to_out(row))


@router.delete("/{position_id}", status_code=204)
def remove_position(
    position_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not has_perm(user, "btn.positions.delete"):
        raise HTTPException(status_code=403, detail="无权限：持仓删除")
    row = _own(db.get(Position, position_id), user)
    if row.status in {"未开始", "开始"}:
        db.delete(row)
        db.commit()
        return
    raise HTTPException(status_code=400, detail="已结束的持仓留在列表里，对应盈亏已进交易日志")
