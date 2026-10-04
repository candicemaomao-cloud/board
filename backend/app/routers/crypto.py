from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as date_cls, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CryptoPaperTrade, CryptoPatternStudy, CryptoStrategySignal, User
from app.services.auth import has_perm, require_user
from app.services.crypto_coins import (
    CryptoCoinError,
    create_coin,
    delete_coin,
    ensure_defaults,
    get_coin,
    list_out,
    to_out,
    update_coin,
)
from app.services.crypto_market import CryptoMarketError, klines, rankings, search_coin, spot_price
from app.services.crypto_pattern import close_paper_trade, due_timestamp, paper_trade_out, study_out
from app.services.crypto_news import fetch_news
from app.services.crypto_onchain import overview as onchain_overview
from app.services.crypto_sentiment import fear_greed
from app.services.crypto_strategies import (
    CryptoStrategyError,
    backtest_row,
    create_strategy,
    delete_strategy,
    get_strategy,
    list_strategies,
    refresh_one,
    refresh_user,
    to_out as strategy_out,
    update_strategy,
)
from app.services.crypto_strategy_records import review_signal, signal_out, summary as signal_summary
from app.services.crypto_custom_strategies import (
    CryptoCustomStrategyError,
    catalog as custom_catalog,
    create_row as create_custom,
    delete_row as delete_custom,
    get_row as get_custom,
    list_rows as list_custom,
    normalize_params,
    param_specs as custom_param_specs,
    refresh_one as refresh_custom,
    run_backtest_for,
    send_test_push as custom_test_push,
    to_out as custom_out,
    update_row as update_custom,
)
from app.services.crypto_tech import analyze
from app.services.crypto_tech import CRYPTO_INDICATORS
from app.services.ohlc import OhlcError

router = APIRouter()


class CoinBody(BaseModel):
    symbol: str
    name: str | None = None
    coingecko_id: str | None = None
    binance_symbol: str | None = None
    notes: str | None = None
    sort_order: int | None = None


class CoinUpdateBody(BaseModel):
    symbol: str | None = None
    name: str | None = None
    coingecko_id: str | None = None
    binance_symbol: str | None = None
    notes: str | None = None
    sort_order: int | None = None


class PatternStudyBody(BaseModel):
    symbol: str
    interval: str
    slice_start_ts: int
    slice_end_ts: int
    horizon: int = 3
    direction: str = "震荡"
    probability: float = 0
    composite_score: float = 0
    sample_symbols: list[str] = []
    sample_count: int = 0
    entry_price: float = 0
    payload: dict = {}


class PaperTradeBody(BaseModel):
    study_id: int | None = None
    symbol: str
    interval: str
    trade_type: str = "spot"
    leverage: float = 1
    side: str = "long"
    notional: float = 1000
    entry_price: float
    target_price: float | None = None
    exit_after_bars: int = 3


class PaperSellBody(BaseModel):
    quantity: float
    price: float


class StrategyBody(BaseModel):
    name: str | None = None
    notes: str | None = None
    coin_id: int | None = None
    symbol: str | None = None
    binance_symbol: str | None = None
    join: str = "and"
    indicators: list[str]
    timeframe: str = "1d"
    recipient_ids: list[int] = []
    enabled: bool = False
    allow_push: bool = False
    interval_sec: int = 300


class StrategyUpdateBody(BaseModel):
    name: str | None = None
    notes: str | None = None
    coin_id: int | None = None
    symbol: str | None = None
    binance_symbol: str | None = None
    join: str | None = None
    indicators: list[str] | None = None
    timeframe: str | None = None
    recipient_ids: list[int] | None = None
    enabled: bool | None = None
    allow_push: bool | None = None
    interval_sec: int | None = None


def _write(user: User) -> None:
    if not (has_perm(user, "btn.crypto.write") or has_perm(user, "menu.cryptoAnalysis")):
        raise HTTPException(status_code=403, detail="无权限：虚拟币增删改")


def _strategy_write(user: User) -> None:
    if not (
        has_perm(user, "btn.crypto.strategy")
        or has_perm(user, "btn.crypto.write")
        or has_perm(user, "menu.cryptoStrategy")
    ):
        raise HTTPException(status_code=403, detail="无权限：虚拟币策略")


@router.get("/coins")
def read_coins(
    with_quote: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    rows = ensure_defaults(db, user.id)
    return {"items": list_out(rows, with_quote=with_quote)}


@router.post("/coins")
def add_coin(payload: CoinBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    _write(user)
    try:
        row = create_coin(
            db,
            user_id=user.id,
            symbol=payload.symbol,
            name=payload.name,
            coingecko_id=payload.coingecko_id,
            binance_symbol=payload.binance_symbol,
            notes=payload.notes,
            sort_order=payload.sort_order,
        )
    except CryptoCoinError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row, with_quote=True)


@router.put("/coins/{coin_id}")
def edit_coin(
    coin_id: int,
    payload: CoinUpdateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    _write(user)
    row = get_coin(db, coin_id, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="币种不存在")
    try:
        row = update_coin(db, row, payload.model_dump(exclude_unset=True))
    except CryptoCoinError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_out(row, with_quote=True)


@router.delete("/coins/{coin_id}", status_code=204)
def remove_coin(coin_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    _write(user)
    row = get_coin(db, coin_id, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="币种不存在")
    delete_coin(db, row)


@router.get("/rankings")
def read_rankings(
    kind: str = Query(default="market_cap", description="market_cap | volume"),
    limit: int = Query(default=30, ge=5, le=50),
    user: User = Depends(require_user),
):
    _ = user
    try:
        items = rankings("volume" if kind == "volume" else "market_cap", limit=limit)
    except CryptoMarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"kind": "volume" if kind == "volume" else "market_cap", "items": items}


@router.get("/search")
def search(q: str = Query(default=""), user: User = Depends(require_user)):
    _ = user
    try:
        return {"items": search_coin(q)}
    except CryptoMarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/coins/{coin_id}/klines")
def read_klines(
    coin_id: int,
    interval: str = Query(default="1d"),
    limit: int = Query(default=90, ge=20, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = get_coin(db, coin_id, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="币种不存在")
    try:
        bars = klines(row.binance_symbol or row.symbol, interval=interval, limit=limit)
    except CryptoMarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "symbol": row.symbol,
        "binance_symbol": row.binance_symbol,
        "interval": interval,
        "market": bars[0].get("market") if bars else None,
        "bars": bars,
    }


@router.get("/pattern-samples")
def read_pattern_samples(
    symbols: str = Query(default="OPUSDT,BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT"),
    interval: str = Query(default="1d"),
    limit: int = Query(default=1500, ge=100, le=1500),
    user: User = Depends(require_user),
):
    _ = user
    allowed_intervals = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"}
    if interval not in allowed_intervals:
        raise HTTPException(status_code=400, detail="不支持的 K 线周期")
    requested = []
    for value in symbols.split(","):
        value = value.strip().upper()
        if value and value not in requested:
            requested.append(value)
    requested = requested[:10]
    items = []
    with ThreadPoolExecutor(max_workers=min(5, len(requested) or 1)) as pool:
        jobs = {pool.submit(klines, value, interval, limit): value for value in requested}
        for job in as_completed(jobs):
            value = jobs[job]
            try:
                rows = job.result()
            except Exception:
                rows = []
            items.append({
                "symbol": value,
                "market": rows[0].get("market") if rows else None,
                "bars": rows,
            })
    items.sort(key=lambda row: requested.index(row["symbol"]))
    return {"interval": interval, "limit": limit, "items": items}


@router.get("/pattern-studies")
def list_pattern_studies(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    rows = db.scalars(select(CryptoPatternStudy).where(
        CryptoPatternStudy.user_id == user.id
    ).order_by(CryptoPatternStudy.id.desc()).limit(100))
    return {"items": [study_out(row) for row in rows]}


@router.post("/pattern-studies")
def create_pattern_study(
    body: PatternStudyBody, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    row = CryptoPatternStudy(
        user_id=user.id,
        symbol=body.symbol.upper(),
        interval=body.interval,
        slice_start_ts=body.slice_start_ts,
        slice_end_ts=body.slice_end_ts,
        horizon=max(1, body.horizon),
        direction=body.direction,
        probability=body.probability,
        composite_score=body.composite_score,
        sample_symbols=json.dumps(body.sample_symbols, ensure_ascii=False),
        sample_count=body.sample_count,
        entry_price=body.entry_price,
        payload=json.dumps(body.payload, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return study_out(row)


@router.get("/paper-trades")
def list_paper_trades(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    rows = list(db.scalars(select(CryptoPaperTrade).where(
        CryptoPaperTrade.user_id == user.id
    ).order_by(CryptoPaperTrade.id.desc()).limit(100)))
    open_spot = [row for row in rows if row.trade_type != "leverage" and row.status == "open"]
    locked_cost = sum(float(row.entry_price) * float(row.remaining_quantity if row.remaining_quantity is not None else row.quantity) for row in open_spot)
    realized = sum(float(row.realized_pnl or 0.0) for row in rows if row.trade_type != "leverage")
    return {
        "items": [paper_trade_out(row) for row in rows],
        "total_amount": float(user.total_amount or 0.0),
        "available_usdt": max(0.0, float(user.total_amount or 0.0) - locked_cost + realized),
    }


@router.delete("/research-data", status_code=204)
def reset_pattern_research_data(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    db.execute(delete(CryptoPaperTrade).where(CryptoPaperTrade.user_id == user.id))
    db.execute(delete(CryptoPatternStudy).where(CryptoPatternStudy.user_id == user.id))
    db.commit()


@router.post("/paper-trades")
def create_paper_trade(
    body: PaperTradeBody, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    if body.entry_price <= 0 or body.notional <= 0:
        raise HTTPException(status_code=400, detail="模拟金额和买入价格必须大于 0")
    trade_type = "leverage" if body.trade_type == "leverage" else "spot"
    side = "short" if trade_type == "leverage" and body.side == "short" else "long"
    leverage = min(125.0, max(1.0, body.leverage)) if trade_type == "leverage" else 1.0
    if trade_type == "spot" and body.target_price is not None and body.target_price <= body.entry_price:
        raise HTTPException(status_code=400, detail="现货自动卖出价必须高于买入价")
    if trade_type == "leverage" and body.target_price is not None:
        invalid_target = (side == "long" and body.target_price <= body.entry_price) or (
            side == "short" and body.target_price >= body.entry_price
        )
        if invalid_target:
            raise HTTPException(status_code=400, detail="目标平仓价方向与杠杆方向不一致")
    if trade_type == "spot":
        rows = list(db.scalars(select(CryptoPaperTrade).where(CryptoPaperTrade.user_id == user.id)))
        open_spot = [row for row in rows if row.trade_type != "leverage" and row.status == "open"]
        locked_cost = sum(float(row.entry_price) * float(row.remaining_quantity if row.remaining_quantity is not None else row.quantity) for row in open_spot)
        realized = sum(float(row.realized_pnl or 0.0) for row in rows if row.trade_type != "leverage")
        available = max(0.0, float(user.total_amount or 0.0) - locked_cost + realized)
        if body.notional > available + 0.000001:
            raise HTTPException(status_code=400, detail=f"模拟账户可用余额不足，当前可用 {available:.2f} USDT")
    entry_fee = body.notional * 0.001 if trade_type == "spot" else 0.0
    quantity = (body.notional - entry_fee) / body.entry_price if trade_type == "spot" else body.notional / body.entry_price
    row = CryptoPaperTrade(
        user_id=user.id,
        study_id=body.study_id,
        symbol=body.symbol.upper(),
        interval=body.interval,
        trade_type=trade_type,
        leverage=leverage,
        side=side,
        quantity=quantity,
        remaining_quantity=quantity,
        notional=body.notional,
        entry_price=body.entry_price,
        target_price=body.target_price,
        realized_pnl=-entry_fee,
        fee_paid=entry_fee,
        exit_after_bars=max(1, body.exit_after_bars),
        entry_ts=int(time.time()),
        due_ts=due_timestamp(body.interval, body.exit_after_bars),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return paper_trade_out(row)


@router.post("/paper-trades/{trade_id}/sell")
def sell_pattern_spot_position(
    trade_id: int,
    body: PaperSellBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = db.get(CryptoPaperTrade, trade_id)
    if not row or row.user_id != user.id or row.trade_type != "spot":
        raise HTTPException(status_code=404, detail="现货模拟持仓不存在")
    if row.status != "open":
        raise HTTPException(status_code=400, detail="该持仓已经卖出")
    remaining = row.remaining_quantity if row.remaining_quantity is not None else row.quantity
    requested = float(body.quantity)
    close_tolerance = max(1e-8, remaining * 1e-6)
    quantity = remaining if remaining - requested <= close_tolerance else min(requested, remaining)
    if quantity <= 0 or body.price <= 0:
        raise HTTPException(status_code=400, detail="卖出数量和价格必须大于 0")
    fee = quantity * body.price * 0.001
    row.realized_pnl = (row.realized_pnl or 0.0) + quantity * (body.price - row.entry_price) - fee
    row.fee_paid = (row.fee_paid or 0.0) + fee
    row.remaining_quantity = max(0.0, remaining - quantity)
    row.pnl_amount = row.realized_pnl
    row.pnl_pct = row.realized_pnl / max(0.00000001, row.notional) * 100
    row.exit_price = body.price
    row.exit_reason = "手动卖出" if row.remaining_quantity <= 0 else "部分卖出"
    if row.remaining_quantity <= 0:
        row.status = "closed"
        row.closed_at = datetime.now().astimezone()
    db.commit()
    db.refresh(row)
    return paper_trade_out(row)


@router.post("/paper-trades/{trade_id}/close")
def close_pattern_paper_trade(
    trade_id: int,
    price: float | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = db.get(CryptoPaperTrade, trade_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="模拟交易不存在")
    if row.status == "open":
        try:
            exit_price = price or spot_price(row.symbol)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="无法取得当前现货价格") from exc
        close_paper_trade(row, exit_price, "手动卖出")
        db.commit()
        db.refresh(row)
    return paper_trade_out(row)


@router.get("/coins/{coin_id}/tech")
def read_tech(
    coin_id: int,
    interval: str = Query(default="1d"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = get_coin(db, coin_id, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="币种不存在")
    try:
        data = analyze(row.binance_symbol or row.symbol, interval=interval)
    except CryptoMarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data["coin_id"] = row.id
    data["coin_symbol"] = row.symbol
    data["coin_name"] = row.name
    return data


@router.get("/indicators")
def read_indicator_catalog(user: User = Depends(require_user)):
    _ = user
    groups: dict[str, list] = {}
    for row in CRYPTO_INDICATORS:
        groups.setdefault(row.get("group") or "其他", []).append(row)
    return {
        "items": CRYPTO_INDICATORS,
        "groups": [{"name": k, "items": v} for k, v in groups.items()],
    }


@router.get("/sentiment/fear-greed")
def read_fear_greed(limit: int = Query(default=30, ge=1, le=365), user: User = Depends(require_user)):
    _ = user
    try:
        return fear_greed(limit=limit)
    except CryptoMarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/onchain")
def read_onchain(
    symbol: str = Query(default="BTCUSDT"),
    user: User = Depends(require_user),
):
    _ = user
    return onchain_overview(symbol)


@router.get("/news")
def read_news(limit: int = Query(default=40, ge=10, le=80), user: User = Depends(require_user)):
    _ = user
    try:
        return fetch_news(limit=limit)
    except CryptoMarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/strategies")
def read_strategies(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return {"items": [strategy_out(r) for r in list_strategies(db, user.id)]}


@router.get("/strategies/signals/summary")
def read_strategy_signal_summary(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return signal_summary(db, user.id)


@router.get("/strategies/signals")
def read_strategy_signals(
    status: str | None = None,
    symbol: str | None = None,
    month: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    stmt = select(CryptoStrategySignal).where(CryptoStrategySignal.user_id == user.id)
    if status:
        stmt = stmt.where(CryptoStrategySignal.review_status == status)
    if symbol:
        stmt = stmt.where(CryptoStrategySignal.symbol == symbol.strip().upper())
    if month:
        try:
            month_start = datetime.strptime(f"{month}-01", "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="月份格式应为 YYYY-MM") from exc
        stmt = stmt.where(CryptoStrategySignal.created_at >= month_start)
    rows = list(db.scalars(stmt.order_by(CryptoStrategySignal.id.desc()).limit(limit)))
    return {"items": [signal_out(row) for row in rows]}


@router.post("/strategies/signals/{signal_id}/review")
def run_strategy_signal_review(
    signal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = db.scalar(select(CryptoStrategySignal).where(
        CryptoStrategySignal.id == signal_id,
        CryptoStrategySignal.user_id == user.id,
    ))
    if not row:
        raise HTTPException(status_code=404, detail="命中记录不存在")
    if row.review_status == "not_evaluable":
        raise HTTPException(status_code=400, detail="该记录属于监控预警，不进入成功率复核")
    if row.review_due_at and row.review_due_at.replace(tzinfo=None) > datetime.utcnow():
        raise HTTPException(status_code=400, detail="尚未到达三天复核时间")
    try:
        return signal_out(review_signal(db, row))
    except CryptoMarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/strategies")
def add_strategy(payload: StrategyBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    _strategy_write(user)
    try:
        row, notify = create_strategy(db, user.id, payload.model_dump())
    except CryptoStrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = strategy_out(row)
    out["notify"] = notify
    return out


@router.put("/strategies/{sid}")
def edit_strategy(
    sid: int,
    payload: StrategyUpdateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    _strategy_write(user)
    row = get_strategy(db, sid, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    try:
        row, notify = update_strategy(db, row, payload.model_dump(exclude_unset=True))
    except CryptoStrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = strategy_out(row)
    out["notify"] = notify
    return out


@router.delete("/strategies/{sid}", status_code=204)
def remove_strategy(sid: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    _strategy_write(user)
    row = get_strategy(db, sid, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    delete_strategy(db, row)


@router.post("/strategies/refresh")
def refresh_strategies(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return {"items": refresh_user(db, user.id)}


@router.post("/strategies/{sid}/check")
def check_strategy(sid: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = get_strategy(db, sid, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    return strategy_out(refresh_one(db, row, notify=False))


class StrategyBacktestBody(BaseModel):
    capital: float = 10000


@router.post("/strategies/{sid}/backtest")
def backtest_strategy(
    sid: int,
    payload: StrategyBacktestBody | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = get_strategy(db, sid, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    try:
        return backtest_row(row, capital=(payload.capital if payload else 10000))
    except CryptoMarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class CustomUpdateBody(BaseModel):
    strategy_key: str | None = None
    name: str | None = None
    notes: str | None = None
    coin_id: int | None = None
    symbol: str | None = None
    binance_symbol: str | None = None
    watch_symbols: list[str] | None = None
    params: dict | None = None
    recipient_ids: list[int] | None = None
    enabled: bool | None = None
    allow_push: bool | None = None
    interval_sec: int | None = None


class CustomBody(BaseModel):
    strategy_key: str = "deriv"
    name: str | None = None
    notes: str | None = None
    coin_id: int | None = None
    symbol: str | None = None
    binance_symbol: str | None = None
    watch_symbols: list[str] | None = None
    params: dict | None = None
    recipient_ids: list[int] = []
    enabled: bool = False
    allow_push: bool = False
    interval_sec: int = 60


class CustomBacktestBody(BaseModel):
    id: int | None = None
    symbol: str | None = None
    params: dict | None = None
    range_start: str | None = None
    range_end: str | None = None


def _parse_ymd(s: str | None) -> date_cls | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"日期格式不对（要 YYYY-MM-DD）：{s}") from exc


@router.get("/custom-strategies/catalog")
def read_custom_catalog(user: User = Depends(require_user)):
    _ = user
    return {"items": custom_catalog(), "param_specs": custom_param_specs(), "defaults": normalize_params({})}


@router.get("/custom-strategies")
def read_custom_strategies(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return {"items": [custom_out(r) for r in list_custom(db, user.id)]}


@router.post("/custom-strategies")
def add_custom_strategy(payload: CustomBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    _strategy_write(user)
    try:
        row, notify = create_custom(db, user.id, payload.model_dump())
    except CryptoCustomStrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = custom_out(row)
    out["notify"] = notify
    return out


@router.put("/custom-strategies/{sid}")
def edit_custom_strategy(
    sid: int,
    payload: CustomUpdateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    _strategy_write(user)
    row = get_custom(db, sid, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    try:
        row, notify = update_custom(db, row, payload.model_dump(exclude_unset=True))
    except CryptoCustomStrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = custom_out(row)
    out["notify"] = notify
    return out


@router.delete("/custom-strategies/{sid}", status_code=204)
def remove_custom_strategy(sid: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    _strategy_write(user)
    row = get_custom(db, sid, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    delete_custom(db, row)


@router.post("/custom-strategies/{sid}/check")
def check_custom_strategy(sid: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = get_custom(db, sid, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    return custom_out(refresh_custom(db, row, force=True, notify=False))


@router.post("/custom-strategies/{sid}/test-push")
def test_push_custom_strategy(sid: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    _strategy_write(user)
    row = get_custom(db, sid, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    try:
        return custom_test_push(db, row)
    except CryptoCustomStrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/custom-strategies/backtest")
def backtest_custom_strategy(
    payload: CustomBacktestBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = None
    if payload.id:
        row = get_custom(db, payload.id, user.id)
        if not row:
            raise HTTPException(status_code=404, detail="策略不存在")
    try:
        return run_backtest_for(
            row,
            symbol=payload.symbol,
            params=payload.params,
            range_start=_parse_ymd(payload.range_start),
            range_end=_parse_ymd(payload.range_end),
        )
    except (CryptoCustomStrategyError, OhlcError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
