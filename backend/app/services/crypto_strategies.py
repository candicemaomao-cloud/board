"""虚拟币策略：指标组合监听 + 推送。"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CryptoCoin, CryptoStrategy
from app.services.crypto_market import CryptoMarketError
from app.services.crypto_tech import eval_combo
from app.services.notify import send_all

_log = logging.getLogger("crypto_strategies")
_tick_lock = threading.Lock()


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
        if (
            notify
            and row.enabled
            and row.allow_push
            and hit
            and asof
            and asof != (row.last_notified_asof or "")
        ):
            text = (
                f"【虚拟币策略命中】{row.name or row.symbol}\n"
                f"{row.symbol} @ {result.get('price')}\n"
                f"周期 {row.timeframe} · {'且' if row.join != 'or' else '或'} 条件成立\n"
                + "\n".join(
                    f"{'✓' if d.get('hit') else '·'} {d.get('name')}"
                    for d in (result.get("combo_details") or [])
                )
            )
            rids = _load_json(row.recipient_ids, [])
            send_all(db, text, recipient_ids=rids or None)
            row.last_notified_asof = asof
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
