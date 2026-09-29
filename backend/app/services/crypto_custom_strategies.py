"""虚拟币自定义策略：目录 + 实例（导数策略等）监听/推送/回测。"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CryptoCustomStrategy
from app.services.binance import normalize_symbol
from app.services.notify import _pack_results, send_all

CN = ZoneInfo("Asia/Shanghai")

_log = logging.getLogger("crypto_custom_strategies")
_tick_lock = threading.Lock()

CATALOG = [
    {"key": "deriv_advanced", "name": "导数高级策略", "file": "_advanced_derivative.py",
     "notes": "平滑导数 + 已收盘高周期 EMA + 相对量能；交易周期 ATR 初始/跟踪止损，可跨日持有。"},
    {
        "key": "deriv",
        "name": "导数策略",
        "file": "_new_strategy_probe.py",
        "notes": "5min 一阶导数方向 + 放量开仓，二阶导数真反转止盈，日线 ATR 止损。币安 U 本位 K 线，按 UTC 日切分。",
    },
    {
        "key": "deriv_regime",
        "name": "导数趋势线策略（三态）",
        "file": "_derivative_advanced_probe.py",
        "notes": "导数策略副本，专为虚拟币：先判急涨/急跌（一小时直线）、横盘、普通趋势三态；"
                 "急涨急跌顺势追+跟踪止损，横盘通道高抛低吸，趋势沿回归趋势线用导数+放量开仓、破线离场。",
    },
]

REGIME_SPECS = [
    {"key": "use_regime", "label": "启用三态趋势线（关=原导数逻辑）", "type": "bool", "default": True},
    {"key": "regime_impulse_chase", "label": "急涨急跌顺势追", "type": "bool", "default": True},
    {"key": "regime_range_trade", "label": "横盘高抛低吸", "type": "bool", "default": True},
    {"key": "regime_trend_trade", "label": "趋势线顺势开仓", "type": "bool", "default": True},
    {"key": "regime_trend_entry", "label": "趋势开仓方式", "type": "enum", "default": "breakout", "options": ["breakout", "pullback"]},
    {"key": "regime_trend_min_slope", "label": "趋势线最小斜率(×ATR)", "type": "float", "default": 1.5, "min": 0, "max": 10},
    {"key": "regime_impulse_minutes", "label": "急涨急跌判定时长(分钟)", "type": "int", "default": 60, "min": 15, "max": 360},
    {"key": "regime_impulse_er", "label": "直线度阈值(0-1)", "type": "float", "default": 0.6, "min": 0.3, "max": 0.95},
    {"key": "regime_impulse_atr", "label": "急涨急跌幅度(×K线ATR)", "type": "float", "default": 4.0, "min": 1.5, "max": 15},
    {"key": "regime_trend_bars", "label": "趋势线根数", "type": "int", "default": 48, "min": 12, "max": 300},
    {"key": "regime_sideways_er", "label": "横盘效率比上限", "type": "float", "default": 0.25, "min": 0.05, "max": 0.6},
    {"key": "regime_sideways_adx", "label": "横盘 ADX 上限", "type": "float", "default": 20, "min": 10, "max": 40},
    {"key": "regime_channel_k", "label": "通道宽度(×残差σ)", "type": "float", "default": 1.5, "min": 0.5, "max": 4},
    {"key": "regime_atr_n", "label": "K线 ATR 周期", "type": "int", "default": 14, "min": 5, "max": 60},
    {"key": "regime_trail_atr", "label": "跟踪止损(×K线ATR)", "type": "float", "default": 4.0, "min": 0.5, "max": 10},
    {"key": "regime_impulse_giveback", "label": "急涨急跌回吐比例", "type": "float", "default": 0.4, "min": 0.1, "max": 0.9},
    {"key": "regime_trend_break_atr", "label": "破趋势线缓冲(×ATR)", "type": "float", "default": 0.25, "min": 0, "max": 3},
    {"key": "regime_range_stop_atr", "label": "横盘止损缓冲(×ATR)", "type": "float", "default": 1.0, "min": 0.2, "max": 5},
]

REGIME_SHARED = {
    "timeframe", "lookback", "context_days", "vwap_trend_lookback", "volume_dist_days", "volume_high_pct",
    "volume_low_pct", "min_slot_samples", "volume_max_window_days", "sma_short_period", "sma_long_period",
    "use_sma_precondition", "allow_short", "daily_atr_n", "stop_loss_atr_mult", "capital_per_trade",
    "compound", "backtest_trading_days",
}

PARAM_SPECS = [
    {"key": "timeframe", "label": "K线周期", "type": "enum", "default": "5m", "options": ["5m", "30m", "1h", "1d"]},
    {"key": "lookback", "label": "导数滑窗", "type": "int", "default": 5, "min": 3, "max": 30},
    {"key": "context_days", "label": "铺垫天数", "type": "int", "default": 3, "min": 1, "max": 10},
    {"key": "vwap_trend_lookback", "label": "VWAP 趋势回看", "type": "int", "default": 6, "min": 2, "max": 30},
    {"key": "volume_dist_days", "label": "量能分布天数", "type": "int", "default": 15, "min": 5, "max": 40},
    {"key": "volume_high_pct", "label": "放量分位", "type": "float", "default": 90, "min": 50, "max": 99},
    {"key": "volume_low_pct", "label": "缩量分位", "type": "float", "default": 10, "min": 1, "max": 50},
    {"key": "min_slot_samples", "label": "槽位最少样本", "type": "int", "default": 8, "min": 3, "max": 40},
    {"key": "volume_max_window_days", "label": "最大量窗口天", "type": "int", "default": 14, "min": 3, "max": 40},
    {"key": "sma_short_period", "label": "SMA 短", "type": "int", "default": 10, "min": 2, "max": 60},
    {"key": "sma_long_period", "label": "SMA 长", "type": "int", "default": 20, "min": 2, "max": 120},
    {"key": "use_sma_precondition", "label": "做空需站上再跌破 SMA", "type": "bool", "default": True},
    {"key": "allow_short", "label": "允许做空", "type": "bool", "default": True},
    {"key": "daily_atr_n", "label": "日线 ATR 周期", "type": "int", "default": 14, "min": 5, "max": 60},
    {"key": "stop_loss_atr_mult", "label": "止损 ATR 倍数", "type": "float", "default": 0.5, "min": 0.1, "max": 5},
    {"key": "trade_every_signal", "label": "每信号都交易", "type": "bool", "default": False},
    {"key": "use_shape_filter", "label": "形态过滤", "type": "bool", "default": False},
    {"key": "shape_confidence_threshold", "label": "形态置信度%", "type": "float", "default": 40, "min": 10, "max": 90},
    {"key": "shape_min_train_days", "label": "形态最少训练天", "type": "int", "default": 30, "min": 10, "max": 120},
    {"key": "close_no_trade_minutes", "label": "日末禁开仓分钟", "type": "int", "default": 0, "min": 0, "max": 120},
    {"key": "market_symbol", "label": "大盘参照", "type": "str", "default": "BTCUSDT"},
    {"key": "capital_per_trade", "label": "回测本金", "type": "float", "default": 10000, "min": 100, "max": 1_000_000},
    {"key": "compound", "label": "回测复利", "type": "bool", "default": True},
    {"key": "backtest_trading_days", "label": "回测交易日数", "type": "int", "default": 21, "min": 5, "max": 120},
]


from app.user_stocks._advanced_derivative import SPECS as ADVANCED_SPECS, COMMON as ADVANCED_COMMON
for _spec in PARAM_SPECS:
    _spec['strategies'] = ['deriv', 'deriv_advanced'] if _spec['key'] in ADVANCED_COMMON else ['deriv']
PARAM_SPECS.extend([{**spec, 'strategies': ['deriv_advanced']} for spec in ADVANCED_SPECS])
for _spec in PARAM_SPECS:
    if _spec['key'] in REGIME_SHARED:
        _spec['strategies'] = _spec['strategies'] + ['deriv_regime']
PARAM_SPECS.extend([{**spec, 'strategies': ['deriv_regime']} for spec in REGIME_SPECS])


class CryptoCustomStrategyError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _flag(v) -> int:
    return 1 if v else 0


def _load_json(raw, default):
    try:
        return json.loads(raw or "") if raw else default
    except Exception:  # noqa: BLE001
        return default


def _ids(raw) -> list[int]:
    data = _load_json(raw, [])
    out = []
    for x in data or []:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def catalog() -> list[dict]:
    return list(CATALOG)


def param_specs() -> list[dict]:
    return list(PARAM_SPECS)


def default_params() -> dict:
    return {s["key"]: s["default"] for s in PARAM_SPECS}


TIMEFRAMES = ("5m", "30m", "1h", "1d")


def normalize_timeframe(raw) -> str:
    tf = str(raw or "5m").strip().lower()
    if tf in ("5min", "5"):
        tf = "5m"
    elif tf in ("30min", "30"):
        tf = "30m"
    elif tf in ("60m", "60min", "1hour"):
        tf = "1h"
    elif tf in ("1day", "d", "day"):
        tf = "1d"
    return tf if tf in TIMEFRAMES else "5m"


def normalize_params(raw: dict | None) -> dict:
    base = default_params()
    src = raw if isinstance(raw, dict) else {}
    for spec in PARAM_SPECS:
        key = spec["key"]
        if key not in src or src[key] is None:
            continue
        val = src[key]
        typ = spec["type"]
        try:
            if typ == "bool":
                base[key] = bool(val)
            elif typ == "int":
                n = int(val)
                if "min" in spec:
                    n = max(int(spec["min"]), n)
                if "max" in spec:
                    n = min(int(spec["max"]), n)
                base[key] = n
            elif typ == "float":
                n = float(val)
                if "min" in spec:
                    n = max(float(spec["min"]), n)
                if "max" in spec:
                    n = min(float(spec["max"]), n)
                base[key] = n
            elif typ == "enum":
                if key == "timeframe":
                    base[key] = normalize_timeframe(val)
                else:
                    opts = spec.get("options") or []
                    s = str(val).strip()
                    base[key] = s if s in opts else spec["default"]
            else:
                base[key] = str(val).strip().upper() if key == "market_symbol" else str(val).strip()
        except (TypeError, ValueError):
            continue
    if not base.get("market_symbol"):
        base["market_symbol"] = "BTCUSDT"
    base["timeframe"] = normalize_timeframe(src.get("timeframe", base.get("timeframe")))
    # 监听币种挂在 params 里，不算策略算法参数
    base["watch_symbols"] = _normalize_watch_symbols(src.get("watch_symbols"), fallback_symbol=src.get("binance_symbol") or src.get("symbol"))
    return base


def _normalize_watch_symbols(raw, *, fallback_symbol: str | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    items = raw if isinstance(raw, list) else []
    if not items and fallback_symbol:
        items = [fallback_symbol]
    for x in items:
        sym = normalize_symbol(str(x or ""))
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out or ["BTCUSDT"]


def watch_symbols_of(row: CryptoCustomStrategy) -> list[str]:
    raw = _load_json(row.params, {})
    return _normalize_watch_symbols(
        raw.get("watch_symbols") if isinstance(raw, dict) else None,
        fallback_symbol=row.binance_symbol or row.symbol,
    )


def strategy_label(key: str) -> str:
    for row in CATALOG:
        if row["key"] == key:
            return row["name"]
    return key or "自定义"


def to_out(row: CryptoCustomStrategy) -> dict:
    params = normalize_params(_load_json(row.params, {}))
    watches = params.get("watch_symbols") or watch_symbols_of(row)
    return {
        "id": row.id,
        "user_id": row.user_id,
        "coin_id": row.coin_id,
        "symbol": row.symbol,
        "binance_symbol": row.binance_symbol or (watches[0] if watches else None),
        "watch_symbols": watches,
        "timeframe": params.get("timeframe") or "5m",
        "strategy_key": row.strategy_key or "deriv",
        "strategy_name": strategy_label(row.strategy_key or "deriv"),
        "name": row.name or strategy_label(row.strategy_key or "deriv"),
        "notes": row.notes or "",
        "file": next((c["file"] for c in CATALOG if c["key"] == (row.strategy_key or "deriv")), None),
        "params": params,
        "recipient_ids": _ids(row.recipient_ids),
        "enabled": bool(row.enabled),
        "allow_push": bool(row.allow_push),
        "interval_sec": row.interval_sec or 60,
        "last_price": row.last_price,
        "last_action": row.last_action,
        "last_asof": row.last_asof,
        "last_direction": row.last_direction,
        "last_error": row.last_error,
        "last_notified_asof": row.last_notified_asof,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def ensure_defaults(db: Session, user_id: int) -> list[CryptoCustomStrategy]:
    """每个用户目录里每种算法只保留一条：缺的补上，重复的删掉（留最早那条）。"""
    rows = list(
        db.scalars(
            select(CryptoCustomStrategy)
            .where(CryptoCustomStrategy.user_id == user_id)
            .order_by(CryptoCustomStrategy.id.asc())
        )
    )
    by_key: dict[str, list[CryptoCustomStrategy]] = {}
    for row in rows:
        by_key.setdefault(row.strategy_key or "deriv", []).append(row)
    dirty = False
    for key, group in by_key.items():
        for extra in group[1:]:
            db.delete(extra)
            dirty = True
    existing = {k for k, g in by_key.items() if g}
    for cat in CATALOG:
        if cat["key"] in existing:
            continue
        params = default_params()
        params["watch_symbols"] = ["BTCUSDT"]
        params["timeframe"] = "30m" if cat['key'] == 'deriv_advanced' else "5m"
        db.add(
            CryptoCustomStrategy(
                user_id=user_id,
                coin_id=None,
                symbol=cat["name"],
                binance_symbol="BTCUSDT",
                strategy_key=cat["key"],
                name=cat["name"],
                notes=cat.get("notes"),
                params=json.dumps(params, ensure_ascii=False),
                recipient_ids="[]",
                enabled=0,
                allow_push=0,
                interval_sec=60,
            )
        )
        dirty = True
    if dirty:
        db.commit()
    return list(
        db.scalars(
            select(CryptoCustomStrategy)
            .where(CryptoCustomStrategy.user_id == user_id)
            .order_by(CryptoCustomStrategy.id.asc())
        )
    )


def list_rows(db: Session, user_id: int) -> list[CryptoCustomStrategy]:
    return ensure_defaults(db, user_id)


def get_row(db: Session, sid: int, user_id: int) -> CryptoCustomStrategy | None:
    row = db.get(CryptoCustomStrategy, sid)
    if not row or row.user_id != user_id:
        return None
    return row


def notify_listen_start(db: Session, row: CryptoCustomStrategy, *, reason: str = "listen") -> dict:
    """策略刚开启监听/推送时确认一条。未指定推送人则发给全部已接通推送人。"""
    rids = _ids(row.recipient_ids)
    watches = "、".join(watch_symbols_of(row)) or "—"
    title = "已开启推送" if reason == "push" else "已开启监听"
    text = (
        f"【自定义策略·{title}】{row.name or strategy_label(row.strategy_key or 'deriv')}\n"
        f"算法：{strategy_label(row.strategy_key or 'deriv')}\n"
        f"监听币：{watches}\n"
        f"检查间隔：{row.interval_sec or 60} 秒\n"
        f"推送：{'开' if row.allow_push else '关'}\n"
        f"说明：仅买入/卖出/止盈事件会推送，观望状态不推"
    )
    results = send_all(db, text, recipient_ids=rids or None)
    if not results:
        return {"ok": False, "error": "没有可用的推送人（请先在「推送人」里配好渠道）", "results": []}
    return _pack_results(results)


def create_row(db: Session, user_id: int, data: dict) -> tuple[CryptoCustomStrategy, dict | None]:
    """一般不需要手动新建：目录策略会自动落库。保留接口给以后加新算法。"""
    key = (data.get("strategy_key") or "deriv").strip()
    if key not in {c["key"] for c in CATALOG}:
        raise CryptoCustomStrategyError("未知策略")
    ensure_defaults(db, user_id)
    exists = db.scalars(
        select(CryptoCustomStrategy).where(
            CryptoCustomStrategy.user_id == user_id,
            CryptoCustomStrategy.strategy_key == key,
        )
    ).first()
    if exists:
        raise CryptoCustomStrategyError(f"{strategy_label(key)} 已在列表中，直接点修改即可")
    params = normalize_params(data.get("params") if isinstance(data.get("params"), dict) else {})
    if data.get("watch_symbols") is not None:
        params["watch_symbols"] = _normalize_watch_symbols(data.get("watch_symbols"))
    watches = params.get("watch_symbols") or ["BTCUSDT"]
    row = CryptoCustomStrategy(
        user_id=user_id,
        coin_id=None,
        symbol=(data.get("name") or "").strip() or strategy_label(key),
        binance_symbol=watches[0],
        strategy_key=key,
        name=(data.get("name") or "").strip() or strategy_label(key),
        notes=(data.get("notes") or None),
        params=json.dumps(params, ensure_ascii=False),
        recipient_ids=json.dumps([int(x) for x in (data.get("recipient_ids") or [])], ensure_ascii=False),
        enabled=_flag(data.get("enabled")),
        allow_push=_flag(data.get("allow_push")),
        interval_sec=max(30, min(int(data.get("interval_sec") or 60), 3600)),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    notify = None
    if row.enabled or row.allow_push:
        try:
            notify = notify_listen_start(db, row, reason="push" if row.allow_push and not row.enabled else "listen")
        except Exception:  # noqa: BLE001
            _log.exception("crypto custom listen notify failed id=%s", row.id)
    return row, notify


def update_row(db: Session, row: CryptoCustomStrategy, data: dict) -> tuple[CryptoCustomStrategy, dict | None]:
    was_on = bool(row.enabled)
    was_push = bool(row.allow_push)
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip() or strategy_label(row.strategy_key or "deriv")
        row.symbol = row.name
    if "notes" in data:
        row.notes = (data.get("notes") or None)
    params = normalize_params(_load_json(row.params, {}))
    if "params" in data and isinstance(data["params"], dict):
        merged = {**params, **data["params"]}
        params = normalize_params(merged)
    if "watch_symbols" in data and data["watch_symbols"] is not None:
        params["watch_symbols"] = _normalize_watch_symbols(data["watch_symbols"])
    row.params = json.dumps(params, ensure_ascii=False)
    watches = params.get("watch_symbols") or ["BTCUSDT"]
    row.binance_symbol = watches[0]
    if "recipient_ids" in data and data["recipient_ids"] is not None:
        row.recipient_ids = json.dumps([int(x) for x in data["recipient_ids"]], ensure_ascii=False)
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = _flag(data["enabled"])
    if "allow_push" in data and data["allow_push"] is not None:
        row.allow_push = _flag(data["allow_push"])
    if "interval_sec" in data and data["interval_sec"] is not None:
        row.interval_sec = max(30, min(int(data["interval_sec"]), 3600))
    db.commit()
    db.refresh(row)
    notify = None
    turned_on = (not was_on and row.enabled) or (not was_push and row.allow_push)
    if turned_on:
        try:
            reason = "push" if (not was_push and row.allow_push) else "listen"
            notify = notify_listen_start(db, row, reason=reason)
        except Exception:  # noqa: BLE001
            _log.exception("crypto custom listen notify failed id=%s", row.id)
    return row, notify


def delete_row(db: Session, row: CryptoCustomStrategy) -> None:
    # 目录策略删了下次打开会再补回来；允许清配置
    db.delete(row)
    db.commit()


def _deriv_kwargs(p: dict) -> dict:
    return dict(
        lookback=int(p["lookback"]),
        context_days=int(p["context_days"]),
        vwap_trend_lookback=int(p["vwap_trend_lookback"]),
        volume_dist_days=int(p["volume_dist_days"]),
        volume_high_pct=float(p["volume_high_pct"]),
        volume_low_pct=float(p["volume_low_pct"]),
        min_slot_samples=int(p["min_slot_samples"]),
        volume_max_window_days=int(p["volume_max_window_days"]),
        daily_atr_n=int(p["daily_atr_n"]),
        stop_loss_atr_mult=float(p["stop_loss_atr_mult"]),
        allow_short=bool(p["allow_short"]),
        use_sma_precondition=bool(p["use_sma_precondition"]),
        sma_short_period=int(p["sma_short_period"]),
        sma_long_period=int(p["sma_long_period"]),
        market_symbol=p.get("market_symbol") or "BTCUSDT",
        market="crypto",
        timeframe=normalize_timeframe(p.get("timeframe")),
    )


def _regime_kwargs(p: dict) -> dict:
    return {s["key"]: p.get(s["key"], s["default"]) for s in REGIME_SPECS}


def _check_symbol(row: CryptoCustomStrategy, symbol: str) -> dict:
    if row.strategy_key == 'deriv_regime':
        from app.user_stocks._derivative_advanced_probe import current_signal as regime_signal
        p = normalize_params(_load_json(row.params, {}))
        return regime_signal(normalize_symbol(symbol), **_deriv_kwargs(p), **_regime_kwargs(p))
    if row.strategy_key == 'deriv_advanced':
        from app.user_stocks._advanced_derivative import current_signal
        p = normalize_params(_load_json(row.params, {}))
        start = row.created_at.date() if row.created_at else _utcnow().date()
        return current_signal(normalize_symbol(symbol), advanced_start=start.isoformat(), **p)
    if (row.strategy_key or "deriv") != "deriv":
        raise CryptoCustomStrategyError("暂不支持该策略")
    from app.user_stocks._new_strategy_probe import current_signal

    p = normalize_params(_load_json(row.params, {}))
    return current_signal(
        normalize_symbol(symbol),
        lookback=int(p["lookback"]),
        context_days=int(p["context_days"]),
        vwap_trend_lookback=int(p["vwap_trend_lookback"]),
        volume_dist_days=int(p["volume_dist_days"]),
        volume_high_pct=float(p["volume_high_pct"]),
        volume_low_pct=float(p["volume_low_pct"]),
        min_slot_samples=int(p["min_slot_samples"]),
        volume_max_window_days=int(p["volume_max_window_days"]),
        daily_atr_n=int(p["daily_atr_n"]),
        stop_loss_atr_mult=float(p["stop_loss_atr_mult"]),
        allow_short=bool(p["allow_short"]),
        use_sma_precondition=bool(p["use_sma_precondition"]),
        sma_short_period=int(p["sma_short_period"]),
        sma_long_period=int(p["sma_long_period"]),
        use_shape_filter=bool(p["use_shape_filter"]),
        shape_confidence_threshold=float(p["shape_confidence_threshold"]),
        shape_min_train_days=int(p["shape_min_train_days"]),
        close_no_trade_minutes=int(p["close_no_trade_minutes"]),
        market_symbol=p.get("market_symbol") or "BTCUSDT",
        market="crypto",
        timeframe=normalize_timeframe(p.get("timeframe")),
    )


def check_one(row: CryptoCustomStrategy) -> dict:
    """检查监听列表里所有币，返回最后一只的结果，并把多币事件打上 symbol 前缀。"""
    watches = watch_symbols_of(row)
    last: dict = {}
    all_events: list[dict] = []
    errors: list[str] = []
    for sym in watches:
        try:
            result = _check_symbol(row, sym)
            last = {**result, "symbol": sym}
            for ev in result.get("events") or []:
                all_events.append({**ev, "symbol": sym, "key": f"{sym} {ev.get('day', '')} {ev.get('time', '')}"})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{sym}: {exc}")
    if not last and errors:
        raise CryptoCustomStrategyError("；".join(errors))
    if last:
        last["events"] = all_events
        if errors:
            last["warnings"] = (last.get("warnings") or []) + errors
    return last


def describe_algo(row: CryptoCustomStrategy) -> str:
    p = normalize_params(_load_json(row.params, {}))
    if row.strategy_key == 'deriv_regime':
        if not p.get("use_regime", True):
            return f"导数趋势线策略·{p['timeframe']}：三态关闭，按原导数逻辑"
        parts = []
        if p["regime_impulse_chase"]:
            parts.append(f"急涨急跌（{p['regime_impulse_minutes']}分钟直线≥{p['regime_impulse_atr']}ATR）顺势追")
        if p["regime_range_trade"]:
            parts.append("横盘通道高抛低吸")
        if p["regime_trend_trade"]:
            parts.append(f"{p['regime_trend_bars']}根趋势线顺势")
        return f"导数趋势线策略·{p['timeframe']}：" + "、".join(parts) + f"；跟踪止损{p['regime_trail_atr']}ATR"
    if row.strategy_key == 'deriv_advanced':
        return (f"导数高级策略V2·{p['timeframe']}：{p['advanced_window']}根回归斜率/ATR + 高周期EMA + "
                f"量比≥{p['advanced_volume']}；初始止损{p['advanced_stop']}ATR，跟踪{p['advanced_trail']}ATR；允许跨日持仓")
    return (
        f"导数策略·{normalize_timeframe(p.get('timeframe'))}：f′ 方向 + 放量（分位≥{p['volume_high_pct']:.0f}），"
        f"f″ 真反转止盈，止损 {p['stop_loss_atr_mult']}×日线{p['daily_atr_n']}期ATR，"
        + ("允许做空" if p["allow_short"] else "只做多")
        + (f" · 形态过滤≥{p['shape_confidence_threshold']:.0f}%" if p["use_shape_filter"] else "")
    )


def _cn_from_utc_hhmm(day: str | None, hhmm: str | None) -> str | None:
    """把 UTC 的 YYYY-MM-DD + HH:MM 转成北京时间说明。"""
    if not hhmm:
        return None
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        dt = datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        return dt.astimezone(CN).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _format_push(row: CryptoCustomStrategy, result: dict, *, test: bool = False, event: dict | None = None) -> str:
    snap = result.get("snap") or {}
    sym = (event or {}).get("symbol") or result.get("symbol") or "—"
    direction = (event or {}).get("direction") or result.get("direction") or "观望"
    asof = (event or {}).get("time") or result.get("asof")
    day = (event or {}).get("day") or result.get("day")
    price = (event or {}).get("price", result.get("price"))
    if event:
        etype = event.get("type") or ""
        action = f"{etype} @ {price}" if etype in ("买入", "卖出/做空") else f"{etype}+放量，如持有对应方向仓位建议止盈/离场"
        if row.strategy_key == 'deriv_regime':
            action = f"{etype} · {event.get('reason') or ''}（{event.get('regime') or '—'}）@ {price}"
        if row.strategy_key == 'deriv_advanced':
            action = f"{etype} · {event.get('reason') or '收盘确认'} @ {price}"
            if event.get('reference_price'):
                action += '（信号收盘参考价，非实际成交价）'
    else:
        action = result.get("action")
    cn = _cn_from_utc_hhmm(day, asof)
    time_line = f"时间 {asof} UTC"
    if cn:
        time_line += f"（北京 {cn}）"
    bits = [
        f"{'【测试】' if test else ('【信号】' if event else '【状态】')}{row.name or strategy_label(row.strategy_key or 'deriv')} · {sym}",
        f"方向 {direction}",
        time_line,
        f"现价 {price}",
        f"动作 {action}",
        f"算法 {describe_algo(row)}",
    ]
    f1, f2 = snap.get("f1"), snap.get("f2")
    if f1 is not None and f2 is not None:
        bits.append(f"f′ {f1:+.4f}  f″ {f2:+.4f}")
    if test:
        bits.append("（测试推送，收到说明通道正常；正式推送只在买入/卖出/止盈时发）")
    return "\n".join(bits)


def refresh_one(db: Session, row: CryptoCustomStrategy, *, force: bool = False, notify: bool = True) -> CryptoCustomStrategy:
    if not row.enabled and not force:
        return row
    try:
        result = check_one(row)
        row.last_price = result.get("price")
        row.last_action = result.get("action")
        row.last_asof = result.get("asof")
        row.last_direction = result.get("direction")
        row.last_error = None
        row.last_checked_at = _utcnow()
        if notify and row.allow_push:
            rids = _ids(row.recipient_ids) or None
            events = result.get("events")
            if events is not None:
                if row.last_notified_asof:
                    pending = [
                        e
                        for e in events
                        if (e.get("key") or f"{e.get('day', '')} {e['time']}") > row.last_notified_asof
                    ]
                else:
                    pending = events[-1:] if events else []
                for event in pending:
                    sent = send_all(db, _format_push(row, result, event=event), recipient_ids=rids)
                    if not sent:
                        row.last_error = "没有可用的推送人（请先在「推送人」里配好渠道）"
                        break
                    if any(item.get("ok") for item in sent):
                        row.last_notified_asof = event.get("key") or f"{event.get('day', '')} {event['time']}"
                        row.last_error = None
                    else:
                        packed = _pack_results(sent)
                        row.last_error = (packed.get("error") or "推送失败")[:240]
                        break
            elif result.get("hit") and row.last_notified_asof != result.get("asof"):
                sent = send_all(db, _format_push(row, result), recipient_ids=rids)
                if not sent:
                    row.last_error = "没有可用的推送人（请先在「推送人」里配好渠道）"
                elif any(item.get("ok") for item in sent):
                    row.last_notified_asof = result.get("asof")
                else:
                    packed = _pack_results(sent)
                    row.last_error = (packed.get("error") or "推送失败")[:240]
        db.commit()
        db.refresh(row)
    except Exception as exc:  # noqa: BLE001
        row.last_error = str(exc)[:240]
        row.last_checked_at = _utcnow()
        db.commit()
        db.refresh(row)
    return row


def send_test_push(db: Session, row: CryptoCustomStrategy) -> dict:
    """手动试发：拉最新信号状态推到所选推送人（不更新 last_notified）。"""
    rids = _ids(row.recipient_ids)
    try:
        result = check_one(row)
    except Exception as exc:  # noqa: BLE001
        raise CryptoCustomStrategyError(f"拉行情失败：{exc}") from exc
    row.last_price = result.get("price")
    row.last_action = result.get("action")
    row.last_asof = result.get("asof")
    row.last_direction = result.get("direction")
    row.last_error = None
    row.last_checked_at = _utcnow()
    text = _format_push(row, result, test=True)
    notified = send_all(db, text, recipient_ids=rids or None)
    packed = _pack_results(notified)
    if not packed.get("ok"):
        err = packed.get("error") or "测试推送失败"
        row.last_error = err[:240]
        db.commit()
        db.refresh(row)
        raise CryptoCustomStrategyError(err)
    db.commit()
    db.refresh(row)
    out = to_out(row)
    out["push_test"] = packed
    return out


def run_backtest_for(row: CryptoCustomStrategy | None, *, symbol: str | None = None, params: dict | None = None,
                     range_start: date | None = None, range_end: date | None = None) -> dict:
    from app.user_stocks._new_strategy_probe import run_backtest

    p = normalize_params(params if params is not None else (_load_json(row.params, {}) if row else {}))
    watches = p.get("watch_symbols") or (watch_symbols_of(row) if row else [])
    sym = normalize_symbol(symbol or (watches[0] if watches else "") or "")
    if not sym:
        raise CryptoCustomStrategyError("请填写交易对")
    if row and row.strategy_key == 'deriv_advanced':
        from app.user_stocks._advanced_derivative import run_backtest as advanced_backtest
        return advanced_backtest(sym, range_start, range_end, **p)
    if row and row.strategy_key == 'deriv_regime':
        from app.user_stocks._derivative_advanced_probe import run_backtest as regime_backtest
        return regime_backtest(
            sym, range_start, range_end,
            **_deriv_kwargs(p), **_regime_kwargs(p),
            backtest_trading_days=int(p["backtest_trading_days"]),
            capital_per_trade=float(p["capital_per_trade"]),
            compound=bool(p["compound"]),
        )
    return run_backtest(
        sym,
        range_start,
        range_end,
        lookback=int(p["lookback"]),
        context_days=int(p["context_days"]),
        vwap_trend_lookback=int(p["vwap_trend_lookback"]),
        volume_dist_days=int(p["volume_dist_days"]),
        volume_high_pct=float(p["volume_high_pct"]),
        volume_low_pct=float(p["volume_low_pct"]),
        min_slot_samples=int(p["min_slot_samples"]),
        volume_max_window_days=int(p["volume_max_window_days"]),
        sma_short_period=int(p["sma_short_period"]),
        sma_long_period=int(p["sma_long_period"]),
        use_sma_precondition=bool(p["use_sma_precondition"]),
        allow_short=bool(p["allow_short"]),
        daily_atr_n=int(p["daily_atr_n"]),
        stop_loss_atr_mult=float(p["stop_loss_atr_mult"]),
        backtest_trading_days=int(p["backtest_trading_days"]),
        capital_per_trade=float(p["capital_per_trade"]),
        compound=bool(p["compound"]),
        trade_every_signal=bool(p["trade_every_signal"]),
        use_shape_filter=bool(p["use_shape_filter"]),
        shape_confidence_threshold=float(p["shape_confidence_threshold"]),
        shape_min_train_days=int(p["shape_min_train_days"]),
        close_no_trade_minutes=int(p["close_no_trade_minutes"]),
        market_symbol=p.get("market_symbol") or "BTCUSDT",
        market="crypto",
        timeframe=normalize_timeframe(p.get("timeframe")),
    )


def tick_due_custom_strategies() -> dict:
    from app.database import SessionLocal

    if not _tick_lock.acquire(blocking=False):
        return {"skipped": True}
    checked = notified = 0
    try:
        db = SessionLocal()
        try:
            rows = list(db.scalars(select(CryptoCustomStrategy).where(CryptoCustomStrategy.enabled == 1)))
            now = _utcnow()
            for row in rows:
                interval = max(30, int(row.interval_sec or 60))
                last = row.last_checked_at
                if last is not None:
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if (now - last).total_seconds() < interval:
                        continue
                before = row.last_notified_asof
                refresh_one(db, row, notify=True)
                checked += 1
                if row.last_notified_asof and row.last_notified_asof != before:
                    notified += 1
            if checked:
                _log.info("crypto custom tick checked=%s notified=%s", checked, notified)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        _log.exception("crypto custom tick failed")
    finally:
        _tick_lock.release()
    return {"checked": checked, "notified": notified}
