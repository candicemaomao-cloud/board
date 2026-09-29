from __future__ import annotations

from datetime import date as date_cls, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
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
from app.services.crypto_market import CryptoMarketError, klines, rankings, search_coin
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
        "bars": bars,
    }


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
