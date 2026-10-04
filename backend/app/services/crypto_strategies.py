"""虚拟币策略：指标组合监听 + 推送。"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models import CryptoCoin, CryptoStrategy
from app.services.crypto_market import CryptoMarketError
from app.services.crypto_tech import eval_combo
from app.services.notify import send_all

_log = logging.getLogger("crypto_strategies")
_tick_lock = threading.Lock()

_INDICATOR_EXPLANATIONS = {
    "rsi_cross_up_30": ("偏多", "RSI 从超卖区上穿 30，表示下跌动能缓和并出现反弹拐点；不等于趋势已经反转。"),
    "rsi_cross_down_70": ("偏空", "RSI 从超买区下穿 70，表示上涨动能降温并有回撤风险；不等于价格必然下跌。"),
    "macd_golden": ("偏多", "MACD 线上穿信号线，说明短期动能转强；横盘期可能出现假金叉。"),
    "macd_death": ("偏空", "MACD 线下穿信号线，说明短期动能转弱；横盘期可能出现假死叉。"),
    "atr_spike": ("波动", "ATR 明显高于近期均值，代表波动突然放大；ATR 只说明幅度，不判断涨跌方向。"),
    "vol_high": ("确认", "成交量高于近期均量，表示市场参与度升高；必须结合价格方向判断是承接还是抛压。"),
}


class CryptoStrategyError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(raw, default):
    try:
        return json.loads(raw or "") if raw else default
    except Exception:  # noqa: BLE001
        return default


def to_out(row: CryptoStrategy) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "coin_id": row.coin_id,
        "symbol": row.symbol,
        "binance_symbol": row.binance_symbol,
        "name": row.name or row.symbol,
        "notes": row.notes or "",
        "join": row.join or "and",
        "indicators": _load_json(row.indicators, []),
        "timeframe": row.timeframe or "1d",
        "recipient_ids": _load_json(row.recipient_ids, []),
        "enabled": bool(row.enabled),
        "allow_push": bool(row.allow_push),
        "interval_sec": row.interval_sec or 300,
        "last_asof": row.last_asof,
        "last_hit": None if row.last_hit is None else bool(row.last_hit),
        "last_price": row.last_price,
        "last_detail": _load_json(row.last_detail, None),
        "last_error": row.last_error,
        "last_notified_asof": row.last_notified_asof,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_strategies(db: Session, user_id: int) -> list[CryptoStrategy]:
    return list(
        db.scalars(
            select(CryptoStrategy)
            .where(CryptoStrategy.user_id == user_id)
            .order_by(CryptoStrategy.id.desc())
        )
    )


def get_strategy(db: Session, sid: int, user_id: int) -> CryptoStrategy | None:
    row = db.get(CryptoStrategy, sid)
    if not row or row.user_id != user_id:
        return None
    return row


def _indicator_names(ids: list) -> str:
    from app.services.crypto_tech import CRYPTO_INDICATOR_MAP

    names = []
    for iid in ids or []:
        meta = CRYPTO_INDICATOR_MAP.get(str(iid))
        names.append((meta or {}).get("name") or str(iid))
    return "、".join(names) if names else "—"


def notify_listen_start(db: Session, row: CryptoStrategy, *, reason: str = "listen") -> dict:
    """策略刚开启监听/推送时，给监听人发一条确认信息。"""
    inds = _load_json(row.indicators, [])
    join_zh = "且" if (row.join or "and") != "or" else "或"
    if reason == "push":
        title = "【虚拟币策略推送已开启】"
    else:
        title = "【虚拟币策略开始监听】"
    text = (
        f"{title}{row.name or row.symbol}\n"
        f"代码：{row.symbol}\n"
        f"周期：{row.timeframe or '1d'}\n"
        f"条件：{join_zh} · {_indicator_names(inds)}\n"
        f"检查间隔：{row.interval_sec or 300} 秒\n"
        f"监听：{'开' if row.enabled else '关'} · 命中推送：{'开' if row.allow_push else '关'}"
    )
    if row.notes:
        text += f"\n备注：{row.notes}"
    rids = _load_json(row.recipient_ids, [])
    # 未指定推送人时，发给所有已接通的推送人
    results = send_all(db, text, recipient_ids=rids or None)
    if not results:
        return {"ok": False, "error": "没有可用的推送人（请先在「推送人」里配好渠道）", "results": []}
    ok = any(item.get("ok") for item in results)
    err = "；".join(
        f"{item.get('who') or ''}{item.get('channel') or ''}:{item.get('error')}"
        for item in results
        if not item.get("ok") and item.get("error")
    )
    return {"ok": ok, "error": None if ok else (err or "发送失败"), "results": results}


def create_strategy(db: Session, user_id: int, data: dict) -> tuple[CryptoStrategy, dict | None]:
    symbol = str(data.get("symbol") or "").strip().upper()
    coin_id = data.get("coin_id")
    bn = data.get("binance_symbol")
    if coin_id:
        coin = db.get(CryptoCoin, int(coin_id))
        if not coin or coin.user_id != user_id:
            raise CryptoStrategyError("币种不存在")
        symbol = coin.symbol
        bn = coin.binance_symbol or bn
    if not symbol:
        raise CryptoStrategyError("请选择币种")
    inds = data.get("indicators") or []
    if not isinstance(inds, list) or not inds:
        raise CryptoStrategyError("请至少选一个指标")
    row = CryptoStrategy(
        user_id=user_id,
        coin_id=int(coin_id) if coin_id else None,
        symbol=symbol[:32],
        binance_symbol=(str(bn).strip().upper() if bn else f"{symbol}USDT")[:32],
        name=(str(data.get("name") or "").strip() or f"{symbol} 策略")[:64],
        notes=(str(data.get("notes") or "").strip() or None),
        join="or" if str(data.get("join") or "").lower() == "or" else "and",
        indicators=json.dumps([str(x) for x in inds], ensure_ascii=False),
        timeframe=str(data.get("timeframe") or "1d")[:8],
        recipient_ids=json.dumps([int(x) for x in (data.get("recipient_ids") or []) if x is not None]),
        enabled=1 if data.get("enabled") else 0,
        allow_push=1 if data.get("allow_push") else 0,
        interval_sec=max(60, min(int(data.get("interval_sec") or 300), 3600)),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    notify = None
    if row.enabled or row.allow_push:
        reason = "push" if row.allow_push else "listen"
        try:
            notify = notify_listen_start(db, row, reason=reason)
        except Exception as exc:  # noqa: BLE001
            _log.exception("crypto strategy listen notify failed id=%s", row.id)
            notify = {"ok": False, "error": str(exc), "results": []}
    return row, notify


def update_strategy(db: Session, row: CryptoStrategy, data: dict) -> tuple[CryptoStrategy, dict | None]:
    was_enabled = bool(row.enabled)
    was_push = bool(row.allow_push)
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()[:64] or row.name
    if "notes" in data:
        row.notes = (str(data["notes"]).strip() if data["notes"] is not None else "") or None
    if "join" in data and data["join"] is not None:
        row.join = "or" if str(data["join"]).lower() == "or" else "and"
    if "indicators" in data and data["indicators"] is not None:
        inds = data["indicators"]
        if not isinstance(inds, list) or not inds:
            raise CryptoStrategyError("请至少选一个指标")
        row.indicators = json.dumps([str(x) for x in inds], ensure_ascii=False)
    if "timeframe" in data and data["timeframe"] is not None:
        row.timeframe = str(data["timeframe"])[:8]
    if "recipient_ids" in data and data["recipient_ids"] is not None:
        row.recipient_ids = json.dumps([int(x) for x in data["recipient_ids"] if x is not None])
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = 1 if data["enabled"] else 0
    if "allow_push" in data and data["allow_push"] is not None:
        row.allow_push = 1 if data["allow_push"] else 0
    if "interval_sec" in data and data["interval_sec"] is not None:
        row.interval_sec = max(60, min(int(data["interval_sec"]), 3600))
    if "coin_id" in data and data["coin_id"]:
        coin = db.get(CryptoCoin, int(data["coin_id"]))
        if coin and coin.user_id == row.user_id:
            row.coin_id = coin.id
            row.symbol = coin.symbol
            row.binance_symbol = coin.binance_symbol
    if "symbol" in data and data["symbol"]:
        row.symbol = str(data["symbol"]).strip().upper()[:32]
    if "binance_symbol" in data and data["binance_symbol"]:
        row.binance_symbol = str(data["binance_symbol"]).strip().upper()[:32]

    listen_on = bool(row.enabled) and not was_enabled
    push_on = bool(row.allow_push) and not was_push
    db.commit()
    db.refresh(row)

    notify = None
    if listen_on or push_on:
        reason = "push" if push_on else "listen"
        try:
            notify = notify_listen_start(db, row, reason=reason)
        except Exception as exc:  # noqa: BLE001
            _log.exception("crypto strategy listen notify failed id=%s", row.id)
            notify = {"ok": False, "error": str(exc), "results": []}
    return row, notify


def delete_strategy(db: Session, row: CryptoStrategy) -> None:
    db.delete(row)
    db.commit()


def evaluate_row(row: CryptoStrategy) -> dict:
    bn = row.binance_symbol or f"{row.symbol}USDT"
    return eval_combo(
        bn,
        _load_json(row.indicators, []),
        join=row.join or "and",
        interval=row.timeframe or "1d",
    )


def backtest_row(row: CryptoStrategy, *, capital: float = 10000.0) -> dict:
    from app.services.crypto_tech import backtest_combo

    bn = row.binance_symbol or f"{row.symbol}USDT"
    out = backtest_combo(
        bn,
        _load_json(row.indicators, []),
        join=row.join or "and",
        interval=row.timeframe or "1d",
        capital=float(capital or 10000),
    )
    out["strategy"] = {
        "id": row.id,
        "name": row.name or row.symbol,
        "symbol": row.symbol,
        "binance_symbol": bn,
        "timeframe": row.timeframe or "1d",
        "join": row.join or "and",
        "indicators": _load_json(row.indicators, []),
    }
    return out


_BULLISH_IDS = {
    "golden", "align_bull", "sma20_above", "sma50_above", "sma200_above",
    "sma20_gt_sma50", "sma50_gt_sma200", "ema20_above", "ema50_above",
    "ema20_up", "macd_golden", "macd_pos", "plus_di_lead", "sar_up",
    "sar_flip_up", "rsi_cross_up_30", "rsi_cross_up_50", "rsi_bull_div",
    "macd_bull_div", "stoch_golden", "kdj_golden", "kdj_above", "roc_pos",
    "roc_up", "bb_up", "bb_above_upper", "keltner_break_up", "obv_up",
    "mfi_up", "mfi_cross_up_20", "cmf_pos", "ad_up", "vwap_above",
    "vwap_cross_up", "vwap_up", "exhaustion", "break_resistance",
    "hold_support", "donchian5_break_up", "donchian_break_up", "whale_large_buy",
}
_BEARISH_IDS = {
    "death", "align_bear", "macd_death", "macd_neg", "minus_di_lead",
    "ichimoku_below_cloud", "ichimoku_tk_death", "sar_down", "sar_flip_down",
    "rsi_cross_down_70", "rsi_cross_down_50", "rsi_bear_div", "macd_bear_div",
    "stoch_death", "kdj_death", "kdj_below", "roc_neg", "bb_down",
    "bb_below_lower", "keltner_break_down", "obv_down", "mfi_down",
    "mfi_cross_down_80", "cmf_neg", "ad_down", "vwap_below",
    "vwap_cross_down", "vwap_down", "climax_top", "donchian5_break_down",
    "donchian_break_down", "whale_large_sell",
}


def _fmt_price(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number >= 1000:
        return f"{number:,.2f}"
    if number >= 1:
        return f"{number:.4f}"
    return f"{number:.6f}"


def _strategy_direction(details: list[dict]) -> tuple[str, int, int, int]:
    hit_ids = {str(item.get("id")) for item in details if item.get("hit")}
    bullish = len(hit_ids & _BULLISH_IDS)
    bearish = len(hit_ids & _BEARISH_IDS)
    neutral = max(0, len(hit_ids) - bullish - bearish)
    if bullish and not bearish:
        direction = "偏多"
    elif bearish and not bullish:
        direction = "偏空"
    elif bullish > bearish:
        direction = "偏多但有冲突"
    elif bearish > bullish:
        direction = "偏空但有冲突"
    else:
        direction = "方向不明"
    return direction, bullish, bearish, neutral


def _price_plan(result: dict, direction: str) -> dict:
    prices = result.get("prices") or {}
    price = float(result.get("price") or prices.get("last") or 0)
    atr = float(prices.get("atr") or 0)
    support = float(prices.get("support") or prices.get("bb_lower") or 0)
    resistance = float(prices.get("resistance") or prices.get("bb_upper") or 0)
    if price <= 0:
        return {}
    if atr <= 0:
        atr = price * 0.02
    if support <= 0 or support >= price:
        support = price - atr
    if resistance <= price:
        resistance = price + atr * 2
    entry_low = max(0, support)
    entry_high = min(price, support + atr * 0.35)
    stop = max(0, support - atr * 0.5)
    target = resistance
    entry_mid = (entry_low + entry_high) / 2
    risk = max(0, entry_mid - stop)
    reward = max(0, target - entry_mid)
    rr = reward / risk if risk > 0 else 0
    actionable = direction == "偏多" and rr >= 1.5
    return {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop": stop,
        "target": target,
        "rr": rr,
        "actionable": actionable,
    }


def _combined_verdict(direction: str, plan: dict, pattern_review: dict | None) -> str:
    review_ready = bool((pattern_review or {}).get("consensus", {}).get("ready"))
    review_direction = str((pattern_review or {}).get("consensus", {}).get("direction") or "")
    if not review_ready:
        return "仅监控预警：三阶段复核未通过，保持观察"
    if direction.startswith("偏多") and review_direction == "看涨" and plan.get("actionable"):
        return "指标与三阶段历史共振均偏多，等待回撤止跌确认后再考虑现货试仓"
    if direction.startswith("偏空") and review_direction == "看跌":
        return "指标与三阶段历史共振均偏空，现货不追买，等待重新企稳"
    if (direction.startswith("偏多") and review_direction == "看跌") or (
        direction.startswith("偏空") and review_direction == "看涨"
    ):
        return "实时指标与三阶段历史方向冲突，不执行，等待下一根 K 线复核"
    return "复核虽有共振，但方向或收益风险比不足，保持观察"


def _backtest_summary(row: CryptoStrategy) -> dict | None:
    try:
        from app.services.crypto_tech import backtest_combo

        out = backtest_combo(
            row.binance_symbol or f"{row.symbol}USDT",
            _load_json(row.indicators, []),
            join=row.join or "and",
            interval=row.timeframe or "1d",
            capital=10000.0,
            max_bars=180,
        )
        summary = out.get("summary") or {}
        return {
            "bars": out.get("bars_scanned") or 0,
            "range": out.get("range") or {},
            "trades": summary.get("n_trades") or 0,
            "win_rate": summary.get("win_rate"),
            "payoff_ratio": summary.get("payoff_ratio"),
        }
    except Exception:  # noqa: BLE001
        _log.exception("crypto strategy report backtest failed id=%s", row.id)
        return None


def build_hit_report(
    row: CryptoStrategy,
    result: dict,
    backtest: dict | None = None,
    pattern_review: dict | None = None,
) -> str:
    details = list(result.get("combo_details") or [])
    hits = [item for item in details if item.get("hit")]
    misses = [item for item in details if not item.get("hit")]
    direction, bullish, bearish, neutral = _strategy_direction(details)
    plan = _price_plan(result, direction)
    price = _fmt_price(result.get("price"))
    asof = str(result.get("asof") or "—")

    review_ready = bool((pattern_review or {}).get("consensus", {}).get("ready"))
    verdict = _combined_verdict(direction, plan, pattern_review)

    report_kind = "综合策略报告" if review_ready else "指标监控预警"
    lines = [
        f"【虚拟币{report_kind}】{row.symbol} · {row.name or row.symbol}",
        f"现价：{price} USDT · 周期：{row.timeframe or '1d'} · K线：{asof}",
        "",
        f"结论：{verdict}",
        f"方向：{direction}（多 {bullish} / 空 {bearish} / 中性 {neutral}）",
        "",
        "本次实际命中：",
    ]
    for item in hits:
        bias, explanation = _INDICATOR_EXPLANATIONS.get(
            str(item.get("id")), ("观察", "该条件已命中，但尚未配置专属解释，请结合其他证据复核。")
        )
        lines.append(f"✓ {item.get('name')}（{bias}）")
        lines.append(f"  {explanation}")
    if misses:
        lines.append(f"未命中：{len(misses)} 项配置条件（不作为本次依据）")

    lines.extend(["", "价格计划："])
    if plan:
        lines.extend(
            [
                f"观察买入区：{_fmt_price(plan['entry_low'])} ～ {_fmt_price(plan['entry_high'])}",
                f"参考止盈：{_fmt_price(plan['target'])}",
                f"失效 / 止损：{_fmt_price(plan['stop'])}",
                f"收益风险比：{plan['rr']:.2f} : 1",
            ]
        )
    else:
        lines.append("价格结构数据不足，暂不提供区间")

    lines.extend(["", "指标组合回测（不是三阶段形态复核）："])
    if backtest and backtest.get("trades"):
        win_rate = backtest.get("win_rate")
        win_text = f"{float(win_rate) * 100:.1f}%" if win_rate is not None else "—"
        payoff = backtest.get("payoff_ratio")
        payoff_text = f"{float(payoff):.2f} : 1" if payoff is not None else "—"
        period = backtest.get("range") or {}
        lines.extend(
            [
                f"样本：{backtest.get('bars')} 根 K线 · {backtest.get('trades')} 笔独立交易",
                f"区间：{period.get('start') or '—'} ～ {period.get('end') or '—'}",
                f"胜率：{win_text} · 历史盈亏比：{payoff_text}",
            ]
        )
    else:
        lines.append("有效交易样本不足，本项仅作实时指标观察")

    lines.extend(["", "短 / 中 / 长三阶段形态复核："])
    if pattern_review:
        for item in pattern_review.get("windows") or []:
            lines.append(
                f"{item['label']} {item['bars']}根：{item['direction']} · "
                f"涨 {item['up']:.1f}% / 震荡 {item['flat']:.1f}% / 跌 {item['down']:.1f}% · "
                f"{item['samples']}例 / {item.get('source_count', 0)}个币种源"
            )
        consensus = pattern_review.get("consensus") or {}
        lines.append(
            f"共振：{consensus.get('direction', '—')} · {consensus.get('agreement', 0)}/3 同向 · "
            f"综合分 {consensus.get('score', 0)}% · {'通过' if review_ready else '未通过'}复核门槛"
        )
        lines.append(
            f"口径：日K 7/14/30根，观察后续 {pattern_review.get('horizon', 7)} 根；"
            f"样本源：{'、'.join(pattern_review.get('sources') or [])}"
        )
        lines.append(f"证据：{pattern_review.get('algorithm')} · {pattern_review.get('evidence_id')}")
        forecast = pattern_review.get("forecast") or {}
        if forecast:
            upside = forecast.get("upside_probe_pct") or {}
            downside = forecast.get("downside_probe_pct") or {}
            final = forecast.get("final_return_pct") or {}
            duration = forecast.get("duration_bars") or {}
            lines.extend(
                [
                    "",
                    "后续路径区间（历史匹配案例 25%～75% 分位，不是保证）：",
                    f"上探幅度：{upside.get('low', 0):+.2f}% ～ {upside.get('high', 0):+.2f}% · 中位 {upside.get('median', 0):+.2f}%",
                    f"下探幅度：{downside.get('low', 0):+.2f}% ～ {downside.get('high', 0):+.2f}% · 中位 {downside.get('median', 0):+.2f}%",
                    f"第 {forecast.get('horizon_bars', 7)} 根收盘：{final.get('low', 0):+.2f}% ～ {final.get('high', 0):+.2f}% · 中位 {final.get('median', 0):+.2f}%",
                    f"常见方向持续：第 {int(duration.get('low', 1))} ～ {int(duration.get('high', forecast.get('horizon_bars', 7)))} 根到达阶段极值",
                    f"统计依据：{forecast.get('sample_count', 0)} 个窗口案例 · {forecast.get('window_count', 0)} 个同向阶段",
                ]
            )
    else:
        lines.append("复核数据获取失败，本次只能视为监控预警，不构成综合策略结论")

    lines.extend(
        [
            "",
            "数据：现货 OHLCV + 技术/量价指标；行情容灾源会在证据中标明",
            "触发原因：新 K 线首次命中；同一事件只推送一次",
            "提示：综合报告仍要求实时指标与历史共振同向才可形成执行候选；价格进入区间后需重新复核，不承诺收益。",
        ]
    )
    return "\n".join(lines)


def _claim_notification(db: Session, row: CryptoStrategy, asof: str) -> bool:
    """Claim one candle atomically so parallel scheduler containers cannot both send it."""
    result = db.execute(
        update(CryptoStrategy)
        .where(
            CryptoStrategy.id == row.id,
            or_(
                CryptoStrategy.last_notified_asof.is_(None),
                CryptoStrategy.last_notified_asof != asof,
            ),
        )
        .values(last_notified_asof=asof)
    )
    db.commit()
    return bool(result.rowcount)


def refresh_one(db: Session, row: CryptoStrategy, *, notify: bool = True) -> CryptoStrategy:
    try:
        result = evaluate_row(row)
        hit = bool(result.get("combo_hit"))
        asof = str(result.get("asof") or "")
        row.last_hit = 1 if hit else 0
        row.last_price = result.get("price")
        row.last_asof = asof or None
        row.last_detail = json.dumps(
            {
                "combo_details": result.get("combo_details"),
                "prices": result.get("prices"),
                "hit_count": result.get("hit_count"),
            },
            ensure_ascii=False,
        )
        row.last_error = None
        row.last_checked_at = _utcnow()
        signal = None
        if row.enabled and hit and asof:
            from app.models import CryptoStrategySignal
            from app.services.crypto_strategy_records import create_signal

            signal = db.scalar(select(CryptoStrategySignal).where(
                CryptoStrategySignal.strategy_id == row.id,
                CryptoStrategySignal.signal_asof == asof,
            ))
            if signal is None:
                try:
                    from app.services.crypto_pattern_review import build_three_window_review

                    pattern_review = build_three_window_review(row.binance_symbol or f"{row.symbol}USDT")
                except Exception:  # noqa: BLE001
                    _log.exception("three-window review failed id=%s", row.id)
                    pattern_review = None
                detail = _load_json(row.last_detail, {})
                detail["three_window_review"] = pattern_review
                row.last_detail = json.dumps(detail, ensure_ascii=False)
                direction, _, _, _ = _strategy_direction(list(result.get("combo_details") or []))
                plan = _price_plan(result, direction)
                verdict = _combined_verdict(direction, plan, pattern_review)
                review_direction = str((pattern_review or {}).get("consensus", {}).get("direction") or "")
                bearish = review_direction == "看跌"
                indicator_backtest = _backtest_summary(row)
                report = build_hit_report(row, result, indicator_backtest, pattern_review)
                signal = create_signal(
                    db, row, result, pattern_review, report,
                    direction=direction,
                    verdict=verdict,
                    target_price=(plan.get("entry_low") if bearish else plan.get("target")),
                    stop_price=(plan.get("target") if bearish else plan.get("stop")),
                    indicator_backtest=indicator_backtest,
                )
        if (
            notify and row.enabled and row.allow_push and hit and asof
            and asof != (row.last_notified_asof or "") and _claim_notification(db, row, asof)
        ):
            rids = _load_json(row.recipient_ids, [])
            send_all(db, signal.report_text if signal else build_hit_report(row, result), recipient_ids=rids or None)
    except (CryptoMarketError, CryptoStrategyError, Exception) as exc:  # noqa: BLE001
        row.last_error = str(exc)[:240]
        row.last_checked_at = _utcnow()
    db.commit()
    db.refresh(row)
    return row


def refresh_user(db: Session, user_id: int) -> list[dict]:
    rows = list_strategies(db, user_id)
    return [to_out(refresh_one(db, r, notify=False)) for r in rows]


def tick_due_crypto_strategies() -> dict:
    if not _tick_lock.acquire(blocking=False):
        return {"checked": 0, "notified": 0, "skipped": True}
    try:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            rows = list(
                db.scalars(select(CryptoStrategy).where(CryptoStrategy.enabled == 1))
            )
            now = _utcnow()
            checked = 0
            notified = 0
            for row in rows:
                interval = max(60, int(row.interval_sec or 300))
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
                _log.info("crypto strategy tick checked=%s notified=%s", checked, notified)
            return {"checked": checked, "notified": notified}
        finally:
            db.close()
    except Exception:
        _log.exception("crypto strategy tick failed")
        return {"checked": 0, "notified": 0, "error": True}
    finally:
        _tick_lock.release()
