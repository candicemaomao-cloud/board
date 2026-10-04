"""Persistent signal audit, delayed review, and value statistics."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CryptoStrategy, CryptoStrategySignal
from app.services.crypto_market import klines, spot_price

_log = logging.getLogger("crypto_strategy_records")


def _json(raw, default):
    try:
        return json.loads(raw or "") if raw else default
    except Exception:  # noqa: BLE001
        return default


def signal_out(row: CryptoStrategySignal) -> dict:
    evidence = _json(row.evidence, {})
    consensus = evidence.get("consensus") or {}
    windows = evidence.get("windows") or []
    backtest = evidence.get("indicator_backtest") or {}
    hits = _json(row.hit_indicators, [])
    sources = evidence.get("sources") or []
    audit = {
        "algorithm_version": evidence.get("audit_version") or "strategy-evidence-v1",
        "dimensions": [
            {"key": "monitor", "name": "监控事件", "status": "passed" if hits else "missing", "detail": f"{len(hits)} 个指标实际命中"},
            {"key": "three_window", "name": "短中长形态", "status": "passed" if consensus.get("ready") and len(windows) == 3 else "insufficient", "detail": f"{consensus.get('agreement', 0)}/3 同向，综合分 {consensus.get('score', 0)}%"},
            {"key": "sample", "name": "历史样本", "status": "passed" if sum(int(x.get("samples") or 0) for x in windows) >= 30 and len(sources) >= 3 else "insufficient", "detail": f"{sum(int(x.get('samples') or 0) for x in windows)} 个窗口样本，{len(sources)} 个数据源"},
            {"key": "price_risk", "name": "价格与风险", "status": "passed" if row.target_price is not None and row.stop_price is not None else "insufficient", "detail": "目标价与失效位均已冻结" if row.target_price is not None and row.stop_price is not None else "缺少完整目标价或失效位"},
            {"key": "backtest", "name": "指标回测", "status": "passed" if int(backtest.get("trades") or 0) >= 20 else "insufficient", "detail": f"{int(backtest.get('trades') or 0)} 笔独立交易"},
            {"key": "outcome", "name": "真实走势", "status": "passed" if row.review_status == "completed" else "pending", "detail": "三天真实走势已复核" if row.review_status == "completed" else "等待三天到期复核"},
        ],
        "excluded": ["新闻与宏观目前只作背景说明，未通过时间对齐的增量回测前不计入交易概率"],
    }
    forecast = evidence.get("forecast") or None
    if forecast:
        forecast = dict(forecast)
        entry = float(row.entry_price or 0)
        for key in ("final_return_pct", "upside_probe_pct", "downside_probe_pct"):
            values = forecast.get(key) or {}
            forecast[key] = values
            forecast[key.replace("_pct", "_price")] = {
                name: round(entry * (1 + float(value) / 100), 10)
                for name, value in values.items()
                if value is not None
            }
    return {
        "id": row.id, "strategy_id": row.strategy_id, "strategy_name": row.strategy_name,
        "symbol": row.symbol, "binance_symbol": row.binance_symbol,
        "monitor_timeframe": row.monitor_timeframe, "signal_asof": row.signal_asof,
        "entry_price": row.entry_price, "direction": row.direction, "verdict": row.verdict,
        "target_price": row.target_price, "stop_price": row.stop_price,
        "evidence_id": row.evidence_id, "hit_indicators": hits,
        "evidence": evidence, "forecast": forecast, "audit": audit, "report_text": row.report_text,
        "review_due_at": row.review_due_at.isoformat() if row.review_due_at else None,
        "review_status": row.review_status, "review_price": row.review_price,
        "actual_return_pct": row.actual_return_pct,
        "max_favorable_pct": row.max_favorable_pct, "max_adverse_pct": row.max_adverse_pct,
        "outcome": row.outcome, "success": None if row.success is None else bool(row.success),
        "review_error": row.review_error,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_signal(
    db: Session,
    strategy: CryptoStrategy,
    result: dict,
    review: dict | None,
    report_text: str,
    *,
    direction: str,
    verdict: str,
    target_price: float | None,
    stop_price: float | None,
    indicator_backtest: dict | None = None,
) -> CryptoStrategySignal:
    existing = db.scalar(select(CryptoStrategySignal).where(
        CryptoStrategySignal.strategy_id == strategy.id,
        CryptoStrategySignal.signal_asof == str(result.get("asof") or ""),
    ))
    if existing:
        return existing
    now = datetime.now(timezone.utc)
    hits = [item for item in result.get("combo_details") or [] if item.get("hit")]
    review_direction = str((review or {}).get("consensus", {}).get("direction") or "")
    evaluable = review_direction in {"看涨", "看跌"} and bool((review or {}).get("consensus", {}).get("ready"))
    evidence = dict(review or {})
    evidence.update({
        "audit_version": "strategy-evidence-v2",
        "monitor": {"timeframe": strategy.timeframe or "1h", "join": strategy.join or "or", "hits": hits},
        "price_risk": {"target_price": target_price, "stop_price": stop_price},
        "indicator_backtest": indicator_backtest or {},
    })
    row = CryptoStrategySignal(
        user_id=strategy.user_id, strategy_id=strategy.id, strategy_name=strategy.name or strategy.symbol,
        symbol=strategy.symbol, binance_symbol=strategy.binance_symbol or f"{strategy.symbol}USDT",
        monitor_timeframe=strategy.timeframe or "1h", signal_asof=str(result.get("asof") or ""),
        entry_price=float(result.get("price") or 0), direction=review_direction if evaluable else direction,
        verdict=verdict, target_price=target_price, stop_price=stop_price,
        evidence_id=(review or {}).get("evidence_id"), hit_indicators=json.dumps(hits, ensure_ascii=False),
        evidence=json.dumps(evidence, ensure_ascii=False), report_text=report_text,
        review_due_at=now + timedelta(days=3), review_status="pending" if evaluable else "not_evaluable",
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.scalar(select(CryptoStrategySignal).where(
            CryptoStrategySignal.strategy_id == strategy.id,
            CryptoStrategySignal.signal_asof == str(result.get("asof") or ""),
        ))
    db.refresh(row)
    return row


def review_signal(db: Session, row: CryptoStrategySignal, *, now: datetime | None = None) -> CryptoStrategySignal:
    now = now or datetime.now(timezone.utc)
    bars = klines(row.binance_symbol, interval="1h", limit=1000)
    created_at = row.created_at
    if created_at and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    start_ts = int(created_at.timestamp()) if created_at else int((now - timedelta(days=3)).timestamp())
    observed = [bar for bar in bars if int(bar.get("ts") or 0) >= start_ts]
    exit_price = float(observed[-1]["close"]) if observed else float(spot_price(row.binance_symbol))
    entry = max(float(row.entry_price), 1e-12)
    highs = [float(bar["high"]) for bar in observed] or [exit_price]
    lows = [float(bar["low"]) for bar in observed] or [exit_price]
    actual = (exit_price / entry - 1) * 100
    if row.direction == "看涨":
        stop_index = next((i for i, bar in enumerate(observed) if row.stop_price and float(bar["low"]) <= row.stop_price), None)
        target_index = next((i for i, bar in enumerate(observed) if row.target_price and float(bar["high"]) >= row.target_price), None)
        stop_first = stop_index is not None and (target_index is None or stop_index <= target_index)
        success = not stop_first and actual > 0
        outcome = "target_hit" if target_index is not None and not stop_first else "stop_hit" if stop_first else "direction_right" if success else "direction_wrong"
        favorable, adverse = (max(highs) / entry - 1) * 100, (min(lows) / entry - 1) * 100
    elif row.direction == "看跌":
        invalid_index = next((i for i, bar in enumerate(observed) if row.stop_price and float(bar["high"]) >= row.stop_price), None)
        target_index = next((i for i, bar in enumerate(observed) if row.target_price and float(bar["low"]) <= row.target_price), None)
        invalid_first = invalid_index is not None and (target_index is None or invalid_index <= target_index)
        success = not invalid_first and actual < 0
        outcome = "avoided_decline" if success else "invalidated" if invalid_first else "direction_wrong"
        favorable, adverse = (1 - min(lows) / entry) * 100, (1 - max(highs) / entry) * 100
    else:
        row.review_status = "not_evaluable"
        db.commit(); db.refresh(row); return row
    row.review_price = exit_price
    row.actual_return_pct = actual
    row.max_favorable_pct = favorable
    row.max_adverse_pct = adverse
    row.outcome = outcome
    row.success = 1 if success else 0
    row.review_status = "completed"
    row.review_error = None
    row.reviewed_at = now
    db.commit(); db.refresh(row)
    return row


def tick_due_signal_reviews() -> dict:
    from app.database import SessionLocal
    db = SessionLocal(); reviewed = errors = 0
    try:
        now = datetime.now(timezone.utc)
        rows = list(db.scalars(select(CryptoStrategySignal).where(
            CryptoStrategySignal.review_status == "pending",
            CryptoStrategySignal.review_due_at <= now,
        ).limit(20)))
        for row in rows:
            try:
                review_signal(db, row, now=now); reviewed += 1
            except Exception as exc:  # noqa: BLE001
                db.rollback(); errors += 1
                current = db.get(CryptoStrategySignal, row.id)
                if current:
                    current.review_error = str(exc)[:255]; db.commit()
        return {"due": len(rows), "reviewed": reviewed, "errors": errors}
    finally:
        db.close()


def summary(db: Session, user_id: int) -> dict:
    rows = list(db.scalars(select(CryptoStrategySignal).where(CryptoStrategySignal.user_id == user_id).order_by(CryptoStrategySignal.id.desc())))
    completed = [row for row in rows if row.review_status == "completed" and row.success is not None]
    wins = sum(row.success == 1 for row in completed)
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    monthly = [row for row in completed if row.reviewed_at and row.reviewed_at.strftime("%Y-%m") == month_key]
    monthly_wins = sum(row.success == 1 for row in monthly)
    month_groups: dict[str, list[CryptoStrategySignal]] = {}
    for row in completed:
        key = row.reviewed_at.strftime("%Y-%m") if row.reviewed_at else "未知"
        month_groups.setdefault(key, []).append(row)
    month_series = []
    for key in sorted(month_groups, reverse=True):
        items = month_groups[key]
        item_wins = sum(row.success == 1 for row in items)
        returns = [float(row.actual_return_pct) for row in items if row.actual_return_pct is not None]
        month_series.append({
            "month": key, "completed": len(items), "successes": item_wins,
            "success_rate": round(item_wins / len(items) * 100, 1),
            "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
        })
    return {
        "total": len(rows), "pending": sum(row.review_status == "pending" for row in rows),
        "not_evaluable": sum(row.review_status == "not_evaluable" for row in rows),
        "completed": len(completed), "successes": wins,
        "success_rate": round(wins / len(completed) * 100, 1) if completed else None,
        "month": month_key, "monthly_completed": len(monthly), "monthly_successes": monthly_wins,
        "monthly_success_rate": round(monthly_wins / len(monthly) * 100, 1) if monthly else None,
        "monthly": month_series,
    }
