"""日内策略股票列表：CRUD + 监听价格 / 信号推手机。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StockWatch, utcnow
from app.services.notify import send_all
from app.services.ohlc import OhlcError
from app.user_stocks.tsla_golden import (
    K_STOP,
    LOOKBACK_5,
    LOOKBACK_30,
    MIN_HOLD_BARS,
    TREND_THRESHOLD,
    atr_wilder_series,
    et_time,
    is_rth,
    mu_index,
    opening_range_series,
    signal_at,
)

log = logging.getLogger("stock_watches")

STRATEGY_CHOICES = {"mu_dip": "局部底/顶 μ", "deriv": "导数策略"}
STRATEGY_DEFAULT = "mu_dip"

BOOL_DEFAULTS = {
    "monitor": False,
    "allow_push": False,
    "allow_trend": False,
    "allow_short": False,
    "day_filter": True,
    "vol_adapt": True,
    "integral": True,
    "hedge": False,
    "gradient": True,
    "vol_confirm": False,
    "kelly": False,
}


class StockWatchError(Exception):
    pass


def _sym(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not s:
        raise StockWatchError("请填写股票代码")
    return s[:32]


def _flag(v) -> int:
    return 1 if v else 0


def _ids(raw) -> list[int]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [p for p in raw.replace(",", " ").split() if p.strip()]
    if not isinstance(raw, list):
        return []
    out = []
    for x in raw:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return out


def _dump_ids(ids: list[int]) -> str:
    return json.dumps(ids, ensure_ascii=False)


def to_out(row: StockWatch, people: dict | None = None) -> dict:
    rids = _ids(row.recipient_ids)
    names = []
    if people:
        for rid in rids:
            if rid in people:
                names.append(people[rid])
    return {
        "id": row.id,
        "symbol": row.symbol,
        "notes": row.notes or "",
        "strategy": row.strategy or STRATEGY_DEFAULT,
        "monitor": bool(row.monitor),
        "allow_push": bool(row.allow_push),
        "push": bool(row.allow_push),
        "recipient_ids": rids,
        "recipient_names": names,
        "interval_sec": int(row.interval_sec or 60),
        "budget": float(row.budget or 10000),
        "hedge_symbol": row.hedge_symbol or "SPY",
        "allow_trend": bool(row.allow_trend),
        "allow_short": bool(row.allow_short),
        "day_filter": bool(row.day_filter),
        "vol_adapt": bool(row.vol_adapt),
        "integral": bool(row.integral),
        "hedge": bool(row.hedge),
        "gradient": bool(row.gradient),
        "vol_confirm": bool(row.vol_confirm),
        "vol_z_th": float(row.vol_z_th or 1.0),
        "kelly": bool(row.kelly),
        "fml_stop_loss_atr_mult": float(row.fml_stop_loss_atr_mult or 0.5),
        "fml_daily_atr_n": int(row.fml_daily_atr_n or 14),
        "fml_volume_high_pct": float(row.fml_volume_high_pct or 90.0),
        "fml_vwap_trend_lookback": int(row.fml_vwap_trend_lookback or 6),
        "fml_allow_short": bool(row.fml_allow_short),
        "fml_use_shape_filter": bool(row.fml_use_shape_filter),
        "fml_shape_confidence_threshold": float(row.fml_shape_confidence_threshold or 40.0),
        "fml_close_no_trade_minutes": int(row.fml_close_no_trade_minutes or 0),
        "fml_market_symbol": row.fml_market_symbol or "SPY",
        "last_price": row.last_price,
        "last_action": row.last_action,
        "last_asof": row.last_asof,
        "last_error": row.last_error,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_watches(db: Session) -> list[StockWatch]:
    return list(db.scalars(select(StockWatch).order_by(StockWatch.symbol.asc())).all())


def get_watch(db: Session, watch_id: int) -> StockWatch | None:
    return db.get(StockWatch, watch_id)


def _apply(row: StockWatch, payload: dict, *, creating: bool) -> None:
    if creating or "symbol" in payload:
        row.symbol = _sym(payload.get("symbol") or row.symbol)
    if "notes" in payload or creating:
        row.notes = (payload.get("notes") or "").strip() or None
    if "strategy" in payload or creating:
        strat = (payload.get("strategy") or STRATEGY_DEFAULT).strip()
        row.strategy = strat if strat in STRATEGY_CHOICES else STRATEGY_DEFAULT
    for key, default in BOOL_DEFAULTS.items():
        if key in payload:
            row.__setattr__(key, _flag(payload.get(key)))
        elif creating:
            row.__setattr__(key, _flag(default))
    if "recipient_ids" in payload or creating:
        row.recipient_ids = _dump_ids(_ids(payload.get("recipient_ids")))
    if "interval_sec" in payload or creating:
        sec = int(payload.get("interval_sec") or 60)
        row.interval_sec = max(30, min(sec, 3600))
    if "budget" in payload or creating:
        row.budget = max(200.0, float(payload.get("budget") or 10000))
    if "hedge_symbol" in payload or creating:
        row.hedge_symbol = (payload.get("hedge_symbol") or "SPY").strip().upper()[:16] or "SPY"
    if "vol_z_th" in payload or creating:
        row.vol_z_th = min(3.0, max(0.5, float(payload.get("vol_z_th") or 1.0)))
    if "fml_stop_loss_atr_mult" in payload or creating:
        row.fml_stop_loss_atr_mult = min(5.0, max(0.1, float(payload.get("fml_stop_loss_atr_mult") or 0.5)))
    if "fml_daily_atr_n" in payload or creating:
        row.fml_daily_atr_n = min(60, max(5, int(payload.get("fml_daily_atr_n") or 14)))
    if "fml_volume_high_pct" in payload or creating:
        row.fml_volume_high_pct = min(99.0, max(50.0, float(payload.get("fml_volume_high_pct") or 90.0)))
    if "fml_vwap_trend_lookback" in payload or creating:
        row.fml_vwap_trend_lookback = min(30, max(2, int(payload.get("fml_vwap_trend_lookback") or 6)))
    if "fml_allow_short" in payload:
        row.fml_allow_short = _flag(payload.get("fml_allow_short"))
    elif creating:
        row.fml_allow_short = 0
    if "fml_use_shape_filter" in payload:
        row.fml_use_shape_filter = _flag(payload.get("fml_use_shape_filter"))
    elif creating:
        row.fml_use_shape_filter = 0
    if "fml_shape_confidence_threshold" in payload or creating:
        row.fml_shape_confidence_threshold = min(90.0, max(10.0, float(payload.get("fml_shape_confidence_threshold") or 40.0)))
    if "fml_close_no_trade_minutes" in payload or creating:
        row.fml_close_no_trade_minutes = min(120, max(0, int(payload.get("fml_close_no_trade_minutes") or 0)))
    if "fml_market_symbol" in payload or creating:
        row.fml_market_symbol = (payload.get("fml_market_symbol") or "SPY").strip().upper()[:16] or "SPY"


def create_watch(db: Session, payload: dict) -> StockWatch:
    symbol = _sym(payload.get("symbol"))
    exists = db.scalars(select(StockWatch).where(StockWatch.symbol == symbol)).first()
    if exists:
        raise StockWatchError(f"{symbol} 已在列表里")
    row = StockWatch()
    _apply(row, payload, creating=True)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_watch(db: Session, row: StockWatch, payload: dict) -> StockWatch:
    if "symbol" in payload:
        symbol = _sym(payload.get("symbol"))
        other = db.scalars(select(StockWatch).where(StockWatch.symbol == symbol)).first()
        if other and other.id != row.id:
            raise StockWatchError(f"{symbol} 已在列表里")
    _apply(row, payload, creating=False)
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def delete_watch(db: Session, row: StockWatch) -> None:
    db.delete(row)
    db.commit()


def set_monitor(db: Session, row: StockWatch, enabled: bool) -> StockWatch:
    row.monitor = _flag(enabled)
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def set_push(db: Session, row: StockWatch, enabled: bool) -> StockWatch:
    row.allow_push = _flag(enabled)
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def _action_from_snap(snap: dict, opts: dict) -> str:
    if snap.get("fire_trend"):
        return f"顺势买 @ {snap.get('price')}"
    if snap.get("fire_trend_short"):
        return f"顺势空 @ {snap.get('price')}"
    if snap.get("fire"):
        return f"局部底买 @ {snap.get('price')}"
    if snap.get("fire_short"):
        return f"局部顶空 @ {snap.get('price')}"
    if snap.get("fade") and opts.get("vol_adapt"):
        return "涨速变慢，考虑平多"
    if snap.get("fade_short") and opts.get("vol_adapt"):
        return "跌速变慢，考虑平空"
    if snap.get("local_min"):
        return "等确认 / 局部底附近"
    if snap.get("local_max") and opts.get("allow_short"):
        return "等确认 / 局部顶附近"
    return "观望"


def _direction_from_snap(snap: dict, opts: dict) -> str:
    if snap.get("fire_trend") or snap.get("fire"):
        return "做多"
    if snap.get("fire_trend_short") or snap.get("fire_short"):
        return "做空"
    if snap.get("fade") and opts.get("vol_adapt"):
        return "平多"
    if snap.get("fade_short") and opts.get("vol_adapt"):
        return "平空"
    return "观望"


def _signal_hit(snap: dict) -> bool:
    return bool(
        snap.get("fire")
        or snap.get("fire_short")
        or snap.get("fire_trend")
        or snap.get("fire_trend_short")
    )


def _check_one_mu_dip(row: StockWatch) -> dict:
    """拉 5min/30min，用该股勾选项算最新信号（局部底/顶 + 30min μ，老策略）。"""
    from app.services.intraday_calculus import garch_scale_series, load_bars
    from app.services.param_matrix import precompute_session_highs, precompute_sigma

    bars_5, bars_30, _source = load_bars(row.symbol)
    if len(bars_5) < LOOKBACK_5 + 6 or len(bars_30) < LOOKBACK_30:
        raise StockWatchError("K 线不够")

    vol_adapt = bool(row.vol_adapt)
    params = {
        "trend_threshold": TREND_THRESHOLD,
        "lookback_5": LOOKBACK_5,
        "lookback_30": LOOKBACK_30,
        "day_filter": bool(row.day_filter),
        "k_stop": K_STOP if vol_adapt else 0.0,
        "allow_short": bool(row.allow_short),
        "allow_trend": bool(row.allow_trend),
        "min_hold_bars": MIN_HOLD_BARS,
        "mu_index": mu_index(bars_30, LOOKBACK_30),
        "sigma_index": precompute_sigma(bars_5),
        "session_highs": precompute_session_highs(bars_5),
        "atr_index": atr_wilder_series(bars_5) if vol_adapt else None,
        "garch_index": garch_scale_series(bars_5) if vol_adapt else None,
    }
    params["or_highs"], params["or_lows"] = opening_range_series(bars_5)

    last_i = None
    for i in range(len(bars_5) - 1, -1, -1):
        if is_rth(bars_5[i]["ts"]):
            last_i = i
            break
    if last_i is None:
        raise StockWatchError("没有盘中 K 线")

    snap = signal_at(bars_5, bars_30, last_i, params) or {}
    # fire 在 signal_at 里已含 day_ok；顺势/抄底确认留给回测，监听页用 raw 信号提示
    bar = bars_5[last_i]
    price = float(bar["close"])
    asof = et_time(bar["ts"])
    opts = {
        "vol_adapt": bool(row.vol_adapt),
        "allow_short": bool(row.allow_short),
    }
    fire = bool(snap.get("fire") or snap.get("trend_long"))
    fire_short = bool(snap.get("fire_short") or snap.get("trend_short"))
    action_snap = {
        **snap,
        "price": round(price, 4),
        "fire": fire and not fire_short,
        "fire_short": fire_short,
        "fire_trend": bool(snap.get("trend_long")),
        "fire_trend_short": bool(snap.get("trend_short")),
    }
    action = _action_from_snap(action_snap, opts)
    return {
        "price": round(price, 4),
        "asof": asof,
        "direction": _direction_from_snap(action_snap, opts),
        "action": action,
        "hit": _signal_hit(action_snap),
        "snap": action_snap,
    }


def _check_one_deriv(row: StockWatch) -> dict:
    """导数策略：VWAP 趋势 + 放量 + 二阶导数反转，不看老策略那套局部底/μ。"""
    from app.user_stocks._new_strategy_probe import current_signal

    return current_signal(
        row.symbol,
        vwap_trend_lookback=int(row.fml_vwap_trend_lookback or 6),
        volume_high_pct=float(row.fml_volume_high_pct or 90.0),
        daily_atr_n=int(row.fml_daily_atr_n or 14),
        stop_loss_atr_mult=float(row.fml_stop_loss_atr_mult or 0.5),
        allow_short=bool(row.fml_allow_short),
        use_shape_filter=bool(row.fml_use_shape_filter),
        shape_confidence_threshold=float(row.fml_shape_confidence_threshold or 40.0),
        close_no_trade_minutes=int(row.fml_close_no_trade_minutes or 0),
        market_symbol=row.fml_market_symbol or "SPY",
    )


def check_one(row: StockWatch) -> dict:
    """按 row.strategy 分派到对应算法算最新信号。"""
    if (row.strategy or STRATEGY_DEFAULT) == "deriv":
        return _check_one_deriv(row)
    return _check_one_mu_dip(row)


def describe_algo(row: StockWatch) -> str:
    """把勾选项翻成推送里能看懂的算法说明。"""
    if (row.strategy or STRATEGY_DEFAULT) == "deriv":
        return (
            f"导数策略：VWAP 趋势（回看 {row.fml_vwap_trend_lookback} 根）+ 放量确认"
            f"（分位≥{row.fml_volume_high_pct:.0f}），二阶导数真反转+放量止盈，"
            f"止损 {row.fml_stop_loss_atr_mult}×日线{row.fml_daily_atr_n}期ATR，"
            + ("允许做空" if row.fml_allow_short else "只做多")
            + (
                f" · 形态过滤开（置信度≥{row.fml_shape_confidence_threshold:.0f}%"
                + (f"，大盘参照{row.fml_market_symbol}" if row.fml_market_symbol else "")
                + "）"
                if row.fml_use_shape_filter else ""
            )
            + (f" · 收盘前{row.fml_close_no_trade_minutes}分钟不开仓" if row.fml_close_no_trade_minutes else "")
        )
    bits = ["5min 拐点 + 30min μ"]
    if row.allow_trend:
        bits.append("顺势突破")
    if row.allow_short:
        bits.append("允许做空")
    else:
        bits.append("只做多")
    if row.day_filter:
        bits.append("当天过滤")
    if row.integral:
        bits.append("积分闸门")
    if row.vol_adapt:
        bits.append("2.5×ATR 止损 / 3×ATR 止盈")
    if row.vol_confirm:
        bits.append(f"量能 Z≥{row.vol_z_th:.1f}")
    if row.hedge:
        bits.append(f"对冲 {row.hedge_symbol or 'SPY'}")
    if row.gradient:
        bits.append("梯度调仓")
    if row.kelly:
        bits.append("半凯利")
    return " · ".join(bits)


def _format_push(row: StockWatch, result: dict, *, test: bool = False, event: dict | None = None) -> str:
    """event 给了值时，推的是「补推」的历史信号（方向/时间/价格都取事件本身的，不是现在的最新状态）。"""
    snap = result.get("snap") or {}
    algo = describe_algo(row)
    direction = (event or {}).get("direction") or result.get("direction") or "观望"
    asof = (event or {}).get("time") or result.get("asof")
    price = (event or {}).get("price", result.get("price"))
    if event:
        etype = event.get("type") or ""
        action = f"{etype} @ {price}" if etype in ("买入", "卖出/做空") else f"{etype}+放量，如持有对应方向仓位建议止盈/离场"
    else:
        action = result.get("action")
    bits = [
        f"{'【测试】' if test else ('【补推】' if event else '【信号】')}{row.symbol}",
        f"方向 {direction}",
        f"时间 {asof} ET",
        f"现价 ${price}",
        f"动作 {action}",
        f"算法 {algo}",
    ]
    shape = result.get("shape") or {}
    if shape.get("enabled") and not event:
        state_bits = [shape.get("predicted") or "其他/观望"]
        if shape.get("substate"):
            state_bits.append(shape["substate"])
        bits.append(f"形态状态 {' · '.join(state_bits)}")
    f1, f2 = snap.get("f1"), snap.get("f2")
    if f1 is not None and f2 is not None:
        bits.append(f"f′ {f1:+.3f}  f″ {f2:+.3f}")
    if snap.get("mu") is not None:
        bits.append(f"30min μ {snap['mu']:.4f}")
    if snap.get("atr"):
        bits.append(f"ATR {snap['atr']:.2f}")
    if direction in ("做多", "平多"):
        if snap.get("stop"):
            bits.append(f"止损 {snap['stop']:.2f}")
    elif direction in ("做空", "平空"):
        if snap.get("stop_short"):
            bits.append(f"止损 {snap['stop_short']:.2f}")
    elif snap.get("stop"):
        bits.append(f"止损 {snap['stop']:.2f}")
    if snap.get("up"):
        bits.append(f"止盈 {snap['up']:.2f}")
    if row.notes:
        bits.append(f"备注 {row.notes}")
    if test:
        bits.append("（测试推送，收到说明通道正常）")
    return "\n".join(bits)


def send_test_push(db: Session, row: StockWatch) -> dict:
    """保存后试发：拉最新价 + 算法说明，推到所选推送人。"""
    from app.services.notify import NotifyError, _pack_results, send_all

    rids = _ids(row.recipient_ids)
    if not rids:
        raise StockWatchError("请先选推送人")
    try:
        result = check_one(row)
    except Exception as exc:
        raise StockWatchError(f"拉行情失败：{exc}") from exc
    row.last_price = result["price"]
    row.last_action = result["action"]
    row.last_asof = result["asof"]
    row.last_error = None
    row.last_checked_at = utcnow()
    text = _format_push(row, result, test=True)
    notified = send_all(db, text, recipient_ids=rids)
    packed = _pack_results(notified)
    if not packed.get("ok"):
        raise StockWatchError(packed.get("error") or "测试推送失败")
    db.commit()
    db.refresh(row)
    out = to_out(row)
    out["push_test"] = packed
    return out


def refresh_one(db: Session, row: StockWatch, *, force: bool = False) -> dict:
    if not row.monitor and not force:
        return to_out(row)
    try:
        result = check_one(row)
        row.last_price = result["price"]
        row.last_action = result["action"]
        row.last_asof = result["asof"]
        row.last_error = None
        row.last_checked_at = utcnow()
        notified = []
        if row.allow_push:
            rids = _ids(row.recipient_ids)
            events = result.get("events")
            if events is not None:
                # 支持补推的策略（导数策略）：按时间顺序把上次通知之后漏掉的事件都补上，
                # 不是只看"现在这一刻"，避免两次检查之间跳过的 K 线上的信号被悄悄丢掉。
                # 从来没通知过的话，只推最新一条，不把当天一整天的历史都灌过去。
                if row.last_notified_asof:
                    pending = [e for e in events if f"{e.get('day', '')} {e['time']}" > row.last_notified_asof]
                else:
                    pending = events[-1:]
                for event in pending:
                    if not rids:
                        break
                    sent = send_all(db, _format_push(row, result, event=event), recipient_ids=rids)
                    notified.extend(sent)
                    if any(item.get("ok") for item in sent):
                        row.last_notified_asof = f"{event.get('day', '')} {event['time']}"
                    else:
                        break  # 这条没推成功就别接着推后面的，下次再从这条补起
            elif result["hit"] and row.last_notified_asof != result["asof"] and rids:
                notified = send_all(db, _format_push(row, result), recipient_ids=rids)
                if any(item.get("ok") for item in notified):
                    row.last_notified_asof = result["asof"]
        db.commit()
        db.refresh(row)
        out = to_out(row)
        out["notify_results"] = notified
        return out
    except Exception as exc:
        row.last_error = str(exc)[:240]
        row.last_checked_at = utcnow()
        db.commit()
        db.refresh(row)
        out = to_out(row)
        out["error"] = str(exc)
        return out


_prewarmed_shape_days: dict[str, str] = {}


def prewarm_shape_models() -> dict:
    """把"导数策略+形态过滤"监听要用的形态预测模型提前训练好、放进
    _shape_model_cache 缓存里。模型训练只依赖 cutoff_day 之前的历史数据，不需要
    等当天K线出现才能训，所以随时都能提前训——不用等到开盘。

    这个函数被单独一个后台任务（跟 tick_due_watches 那个不一样的 asyncio task）
    独立、按固定节奏反复调用：不管是新的一天开始，还是开发过程中 --reload 重启
    进程把内存缓存清空了，下一轮都会在这个独立任务里把模型重新训好，而不是等到
    真正该检查这只股票的那一刻，现场卡 20-30 秒重训、拖慢那一次本该实时的推送。
    两个任务并发跑（不是同一个 to_thread 调用），训练慢不会卡住轮询本身。
    """
    from zoneinfo import ZoneInfo
    from app.database import SessionLocal
    from app.user_stocks._new_strategy_probe import get_cached_shape_model

    et = ZoneInfo("America/New_York")
    today_et = datetime.now(et).date()
    today_str = today_et.isoformat()

    db = SessionLocal()
    try:
        rows = list_watches(db)
        warmed: list[str] = []
        errors: list[str] = []
        for row in rows:
            if not row.monitor or row.strategy != "deriv" or not row.fml_use_shape_filter:
                continue
            symbol = (row.symbol or "").strip().upper()
            if not symbol or _prewarmed_shape_days.get(symbol) == today_str:
                continue
            try:
                get_cached_shape_model(symbol, today_et, min_train_days=30)
                _prewarmed_shape_days[symbol] = today_str
                warmed.append(symbol)
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
        return {"warmed": warmed, "errors": errors}
    finally:
        db.close()


def tick_due_watches() -> dict:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        rows = list_watches(db)
        now = datetime.now(timezone.utc)
        ran = notified = 0
        errors = []
        for row in rows:
            if not row.monitor:
                continue
            last = row.last_checked_at
            if last and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            gap = float(row.interval_sec or 60)
            if last and (now - last).total_seconds() < gap:
                continue
            out = refresh_one(db, row, force=True)
            ran += 1
            if out.get("notify_results") and any(x.get("ok") for x in out["notify_results"]):
                notified += 1
            if out.get("error") or out.get("last_error"):
                errors.append(f"{row.symbol}: {out.get('error') or out.get('last_error')}")
        return {"ran": ran, "notified": notified, "errors": errors}
    finally:
        db.close()
