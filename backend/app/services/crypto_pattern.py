from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import CryptoPaperTrade, CryptoPatternStudy
from app.services.crypto_market import klines, spot_price


INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
    "1M": 2592000,
}


def study_out(row: CryptoPatternStudy) -> dict:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "interval": row.interval,
        "slice_start_ts": row.slice_start_ts,
        "slice_end_ts": row.slice_end_ts,
        "horizon": row.horizon,
        "direction": row.direction,
        "probability": row.probability,
        "composite_score": row.composite_score,
        "sample_symbols": json.loads(row.sample_symbols or "[]"),
        "sample_count": row.sample_count,
        "entry_price": row.entry_price,
        "payload": json.loads(row.payload or "{}"),
        "status": row.status,
        "actual_return_pct": row.actual_return_pct,
        "success": None if row.success is None else bool(row.success),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def paper_trade_out(row: CryptoPaperTrade) -> dict:
    return {
        "id": row.id,
        "study_id": row.study_id,
        "symbol": row.symbol,
        "interval": row.interval,
        "trade_type": row.trade_type or "spot",
        "leverage": row.leverage or 1.0,
        "side": row.side,
        "quantity": row.quantity,
        "remaining_quantity": row.remaining_quantity if row.remaining_quantity is not None else row.quantity,
        "notional": row.notional,
        "entry_price": row.entry_price,
        "target_price": row.target_price,
        "exit_after_bars": row.exit_after_bars,
        "entry_ts": row.entry_ts,
        "due_ts": row.due_ts,
        "status": row.status,
        "exit_price": row.exit_price,
        "pnl_pct": row.pnl_pct,
        "pnl_amount": row.pnl_amount,
        "realized_pnl": row.realized_pnl or 0.0,
        "fee_paid": row.fee_paid or 0.0,
        "exit_reason": row.exit_reason,
        "opened_at": row.opened_at,
        "closed_at": row.closed_at,
    }


def close_paper_trade(row: CryptoPaperTrade, exit_price: float, reason: str = "到期市价卖出") -> None:
    direction = 1 if row.side == "long" else -1
    leverage = max(1.0, float(row.leverage or 1.0)) if row.trade_type == "leverage" else 1.0
    row.exit_price = float(exit_price)
    row.pnl_pct = (row.exit_price / row.entry_price - 1) * 100 * direction * leverage
    remaining = row.remaining_quantity if row.remaining_quantity is not None else row.quantity
    fee = remaining * row.exit_price * 0.001 if row.trade_type != "leverage" else 0.0
    row.realized_pnl = (row.realized_pnl or 0.0) + remaining * (row.exit_price - row.entry_price) * direction * leverage - fee
    row.fee_paid = (row.fee_paid or 0.0) + fee
    row.remaining_quantity = 0.0
    row.pnl_amount = row.realized_pnl
    row.exit_reason = reason
    row.status = "closed"
    row.closed_at = datetime.now(timezone.utc)


def tick_pattern_paper_trades() -> None:
    now = int(time.time())
    with SessionLocal() as db:
        rows = list(db.scalars(select(CryptoPaperTrade).where(
            CryptoPaperTrade.status == "open",
        )))
        changed = False
        for row in rows:
            try:
                price = spot_price(row.symbol)
            except Exception:
                continue
            if price <= 0:
                continue
            target_hit = bool(
                row.target_price
                and ((row.side == "long" and price >= row.target_price)
                     or (row.side == "short" and price <= row.target_price))
            )
            if not target_hit and row.due_ts > now:
                continue
            close_paper_trade(row, price, "达到目标价自动卖出" if target_hit else "到期市价卖出")
            if row.study_id:
                study = db.get(CryptoPatternStudy, row.study_id)
                if study:
                    study.actual_return_pct = row.pnl_pct
                    study.status = "已验证"
                    study.success = int(
                        (study.direction == "看涨" and row.pnl_pct > 0)
                        or (study.direction == "看跌" and row.pnl_pct < 0)
                        or (study.direction == "震荡" and abs(row.pnl_pct) <= 1)
                    )
            changed = True
        if changed:
            db.commit()
    tick_pattern_studies()


def tick_pattern_studies() -> None:
    now = int(time.time())
    with SessionLocal() as db:
        rows = list(db.scalars(select(CryptoPatternStudy).where(CryptoPatternStudy.status == "待验证")))
        changed = False
        for row in rows:
            due_ts = row.slice_end_ts + INTERVAL_SECONDS.get(row.interval, 86400) * max(1, row.horizon)
            if due_ts > now:
                continue
            try:
                future = [bar for bar in klines(row.symbol, row.interval, 1000) if int(bar.get("ts", 0)) > row.slice_end_ts and bar.get("closed") is not False]
            except Exception:
                continue
            if len(future) < row.horizon:
                continue
            actual = future[:row.horizon]
            actual_return = (float(actual[-1]["close"]) / max(0.00000001, float(row.entry_price)) - 1) * 100
            payload = json.loads(row.payload or "{}")
            payload["actual_bars"] = actual
            payload["verified_at"] = now
            row.payload = json.dumps(payload, ensure_ascii=False)
            row.actual_return_pct = actual_return
            row.status = "已验证"
            row.success = int(
                (row.direction == "看涨" and actual_return > 1)
                or (row.direction == "看跌" and actual_return < -1)
                or (row.direction == "震荡" and abs(actual_return) <= 1)
            )
            changed = True
        if changed:
            db.commit()


def due_timestamp(interval: str, bars: int) -> int:
    return int(time.time()) + INTERVAL_SECONDS.get(interval, 86400) * max(1, bars)
