from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, NotifyRecipient, utcnow
from app.services.indicators import snapshot
from app.services.notify import maybe_notify, send_test_hit
from app.services.ohlc import OhlcError, fetch_closes
from app.services.strategy_plans import formula_library, list_strategies, name_map, normalize_side, plan_timeframe
from app.services.strategy_spec import INDICATOR_MAP, eval_formula, explain_formula, flags_from_snapshot

ET = ZoneInfo("America/New_York")
CLOSE_HOUR = 16
JOIN_OPS = {"and", "or"}
STRATEGY_PREFIX = "s:"
BAR_SECONDS = {"5m": 300, "30m": 1800, "4h": 14400}
INTERVALS = (30, 60, 300, 900, 1800, 3600)
DEFAULT_INTERVAL = 30
_log = logging.getLogger("alerts")
_tick_lock = threading.Lock()


class AlertError(Exception):
    pass


def normalize_interval(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    if value in INTERVALS:
        return value
    return min(INTERVALS, key=lambda item: abs(item - value))


def _aware(stamp: datetime | None) -> datetime | None:
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp


def interval_of(row: Alert) -> int:
    return normalize_interval(getattr(row, "interval_sec", None))


def is_due(row: Alert, now: datetime | None = None) -> bool:
    if not row.allow_push:
        return False
    last = _aware(row.last_checked_at)
    if last is None:
        return True
    stamp = now or utcnow()
    return (stamp - last).total_seconds() >= interval_of(row) - 0.5


def next_due_ts(rows, now: datetime | None = None) -> int | None:
    stamp = now or utcnow()
    soonest = None
    for row in rows or []:
        if not row.allow_push:
            continue
        last = _aware(row.last_checked_at)
        due = stamp if last is None else last + timedelta(seconds=interval_of(row))
        ts = int(due.timestamp())
        if soonest is None or ts < soonest:
            soonest = ts
    return soonest


def next_refresh_ts(rows=None, now: datetime | None = None) -> int | None:
    return next_due_ts(rows, now)


def current_asof_hint(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(ET)
    cutoff = stamp.replace(hour=CLOSE_HOUR, minute=0, second=0, microsecond=0)
    day = stamp.date() if stamp >= cutoff else (stamp - timedelta(days=1)).date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def completed_daily(ohlc: dict, now: datetime | None = None) -> dict:
    bars = [dict(row) for row in (ohlc.get("ohlc_bars") or [])]
    if not bars:
        raise OhlcError("没有日线")
    stamp = (now or datetime.now(timezone.utc)).astimezone(ET)
    cutoff = stamp.replace(hour=CLOSE_HOUR, minute=0, second=0, microsecond=0)
    last_dt = datetime.fromtimestamp(int(bars[-1].get("ts") or 0), tz=timezone.utc).astimezone(ET)
    if last_dt.date() == stamp.date() and stamp < cutoff:
        bars = bars[:-1]
    if not bars:
        raise OhlcError("还没有已收盘的日线")
    close_bar = bars[-1]
    close_dt = datetime.fromtimestamp(int(close_bar.get("ts") or 0), tz=timezone.utc).astimezone(ET)
    closes = [float(row["close"]) for row in bars]
    packed = dict(ohlc)
    packed["ohlc_bars"] = bars
    packed["closes"] = closes
    packed["price"] = closes[-1]
    packed["bars"] = len(bars)
    packed["asof"] = close_dt.date().isoformat()
    packed["timeframe"] = "1d"
    return packed


def completed_bars(ohlc: dict, now: datetime | None = None) -> dict:
    tf = ohlc.get("timeframe") or "1d"
    if tf == "1d":
        return completed_daily(ohlc, now)
    bars = [dict(row) for row in (ohlc.get("ohlc_bars") or [])]
    if not bars:
        raise OhlcError("没有K线")
    stamp = now or datetime.now(timezone.utc)
    width = BAR_SECONDS.get(tf, 86400)
    last_ts = int(bars[-1].get("ts") or 0)
    if last_ts + width > int(stamp.timestamp()):
        bars = bars[:-1]
    if not bars:
        raise OhlcError("还没有已收盘的K线")
    close_bar = bars[-1]
    close_dt = datetime.fromtimestamp(int(close_bar.get("ts") or 0), tz=timezone.utc).astimezone(ET)
    closes = [float(row["close"]) for row in bars]
    packed = dict(ohlc)
    packed["ohlc_bars"] = bars
    packed["closes"] = closes
    packed["price"] = closes[-1]
    packed["bars"] = len(bars)
    packed["asof"] = close_dt.strftime("%Y-%m-%d %H:%M")
    packed["timeframe"] = tf
    return packed


def _plan_token(sid: int) -> str:
    return f"{STRATEGY_PREFIX}{int(sid)}"


def _parse_token(raw) -> tuple[str, str | int] | None:
    cid = str(raw or "").strip()
    if not cid:
        return None
    if cid.startswith(STRATEGY_PREFIX):
        try:
            sid = int(cid[len(STRATEGY_PREFIX) :])
        except ValueError:
            return None
        if sid <= 0:
            return None
        return ("strategy", sid)
    if cid in INDICATOR_MAP:
        return ("indicator", cid)
    return None


def _load_ids(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        data = []
    if not isinstance(data, list):
        return []
    out = []
    seen = set()
    for item in data:
        parsed = _parse_token(item)
        if not parsed or parsed[0] != "strategy":
            continue
        token = _plan_token(parsed[1])
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _load_recipient_ids(raw: str | None) -> list[int]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        data = []
    if not isinstance(data, list):
        return []
    out = []
    seen = set()
    for item in data:
        try:
            rid = int(item)
        except (TypeError, ValueError):
            continue
        if rid <= 0 or rid in seen:
            continue
        seen.add(rid)
        out.append(rid)
    return out


def normalize_recipient_ids(raw, known: set[int] | None = None) -> list[int]:
    if raw is None:
        raw = []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    if not isinstance(raw, list) or not raw:
        raise AlertError("请至少选一个推送人")
    ids = []
    seen = set()
    for item in raw:
        try:
            rid = int(item)
        except (TypeError, ValueError):
            continue
        if rid <= 0 or rid in seen:
            continue
        if known is not None and rid not in known:
            raise AlertError("推送人不存在，去「推送人」看一下还在不在")
        seen.add(rid)
        ids.append(rid)
    if not ids:
        raise AlertError("请至少选一个推送人")
    return ids


def _dump_ids(ids: list[str]) -> str:
    return json.dumps(ids, ensure_ascii=False)


def _clause_names(ids: list[str], names: dict | None = None) -> list[str]:
    names = names or {}
    labels = []
    for cid in ids:
        parsed = _parse_token(cid)
        if not parsed:
            continue
        kind, value = parsed
        if kind == "strategy":
            labels.append(names.get(int(value)) or f"策略{value}")
        else:
            labels.append(INDICATOR_MAP[str(value)]["name"])
    return labels


def _load_detail(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _plans(db: Session) -> tuple[dict, dict[int, str]]:
    rows = list_strategies(db)
    return formula_library(rows), name_map(rows)


def normalize_ids(raw, known_strategies: set[int] | None = None) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list) or not raw:
        raise AlertError("请至少选一条策略")
    ids = []
    seen = set()
    for item in raw:
        cid = str(item or "").strip()
        if not cid or cid in seen:
            continue
        parsed = _parse_token(cid)
        if not parsed or parsed[0] != "strategy":
            raise AlertError("推送只能选已保存的策略")
        sid = int(parsed[1])
        if known_strategies is not None and sid not in known_strategies:
            raise AlertError("引用的策略不存在，去策略页看一下还在不在")
        token = _plan_token(sid)
        seen.add(token)
        ids.append(token)
    if not ids:
        raise AlertError("请至少选一条策略")
    return ids


def normalize_join(raw) -> str:
    join = str(raw or "and").lower()
    if join not in JOIN_OPS:
        raise AlertError("指标连接只能是且或或")
    return join


def _formula(ids: list[str], join: str) -> dict:
    clauses = []
    for cid in ids:
        parsed = _parse_token(cid)
        if not parsed:
            continue
        kind, value = parsed
        if kind == "strategy":
            clauses.append({"kind": "strategy", "id": int(value), "not": False})
        else:
            clauses.append({"kind": "indicator", "id": str(value), "not": False})
    return {
        "join": "and",
        "groups": [{"join": join, "clauses": clauses}],
        "targets": [],
    }


def evaluate_symbol(
    symbol: str,
    ids: list[str],
    join: str = "and",
    library: dict | None = None,
    names: dict | None = None,
    timeframe: str = "1d",
) -> dict:
    from app.services.strategy_calc import TIMEFRAMES

    tf = timeframe if timeframe in TIMEFRAMES else "1d"
    ohlc = completed_bars(fetch_closes(symbol, tf, apply_live=False))
    extra = {}
    for need in ("4h", "1d"):
        if ohlc.get("timeframe") == need:
            extra[need] = ohlc
            continue
        try:
            extra[need] = completed_bars(fetch_closes(symbol, need, apply_live=False))
        except OhlcError:
            extra[need] = None
    if ohlc.get("source") != "binance":
        for bench in ("SPY", "QQQ"):
            try:
                extra[bench.lower()] = completed_bars(fetch_closes(bench, tf, apply_live=False))
            except OhlcError:
                extra[bench.lower()] = None
    indicators = snapshot(ohlc, extra)
    flags = flags_from_snapshot(indicators)
    formula = _formula(ids, join)
    library = library or {}
    names = names or {}
    explain = explain_formula(formula, flags, library, names, snapshot={**indicators, "last": ohlc.get("price")})
    return {
        "symbol": ohlc["symbol"],
        "source": ohlc.get("source"),
        "price": ohlc.get("price"),
        "asof": ohlc.get("asof"),
        "bars": ohlc.get("bars"),
        "timeframe": ohlc.get("timeframe"),
        "match": bool(eval_formula(formula, flags, library)),
        "explain": explain,
        "clauses": [
            {"id": row.get("id"), "name": row.get("name"), "hit": bool(row.get("hit"))}
            for group in (explain or {}).get("groups") or []
            for row in group.get("clauses") or []
        ],
    }


def to_out(row: Alert, push_test: dict | None = None, names: dict | None = None, people: dict | None = None) -> dict:
    ids = _load_ids(row.indicators)
    rids = _load_recipient_ids(getattr(row, "recipient_ids", None))
    people = people or {}
    data = {
        "id": row.id,
        "symbol": row.symbol,
        "name": row.name or "",
        "notes": row.notes or "",
        "join": row.join or "and",
        "indicators": ids,
        "indicator_names": _clause_names(ids, names),
        "recipient_ids": rids,
        "recipient_names": [people.get(rid) or f"#{rid}" for rid in rids],
        "push": bool(row.allow_push),
        "enabled": bool(row.allow_push),
        "interval_sec": interval_of(row),
        "last_asof": row.last_asof,
        "last_hit": None if row.last_hit is None else bool(row.last_hit),
        "last_price": row.last_price,
        "last_detail": _load_detail(row.last_detail),
        "last_error": row.last_error,
        "last_notified_asof": row.last_notified_asof,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if push_test is not None:
        data["push_test"] = push_test
    return data


def list_alerts(db: Session) -> list[Alert]:
    return list(db.scalars(select(Alert).order_by(Alert.id.desc())))


def get_alert(db: Session, alert_id: int) -> Alert | None:
    return db.get(Alert, alert_id)


def _apply_eval(row: Alert, result: dict) -> None:
    row.last_asof = result.get("asof")
    row.last_hit = 1 if result.get("match") else 0
    row.last_price = result.get("price")
    row.last_detail = json.dumps(
        {
            "clauses": result.get("clauses") or [],
            "match": bool(result.get("match")),
            "price": result.get("price"),
            "asof": result.get("asof"),
            "strategy_name": result.get("strategy_name") or "",
            "strategy_side": result.get("strategy_side") or "",
            "strategy_label": result.get("strategy_label") or "",
        },
        ensure_ascii=False,
    )
    row.last_error = None
    row.last_checked_at = utcnow()


def _apply_error(row: Alert, message: str) -> None:
    row.last_error = (message or "计算失败")[:255]
    row.last_checked_at = utcnow()


def evaluate_row(db: Session, row: Alert) -> dict:
    ids = _load_ids(row.indicators)
    if not ids:
        raise AlertError("请至少选一条策略")
    rows = list_strategies(db)
    library, names = formula_library(rows), name_map(rows)
    by_id = {item.id: item for item in rows}
    tf = "1d"
    labels = []
    first_name = ""
    first_side = ""
    for token in ids:
        parsed = _parse_token(token)
        if not parsed or parsed[0] != "strategy":
            continue
        plan = by_id.get(int(parsed[1]))
        if not plan:
            continue
        if not first_name:
            first_name = plan.name
            first_side = "做空" if normalize_side(getattr(plan, "side", None)) == "short" else "做多"
            tf = plan_timeframe(plan)
        side = "做空" if normalize_side(getattr(plan, "side", None)) == "short" else "做多"
        labels.append(f"{plan.name} · {side}")
    result = evaluate_symbol(row.symbol, ids, row.join or "and", library, names, tf)
    result["strategy_name"] = first_name
    result["strategy_side"] = first_side
    result["strategy_label"] = "、".join(labels)
    return result


def _first_test(db: Session, row: Alert, turning_on: bool) -> dict | None:
    if not turning_on:
        return None
    test = send_test_hit(db, row, _load_detail(row.last_detail))
    if test.get("ok") and row.last_asof:
        row.last_notified_asof = row.last_asof
    return test


def _people_map(db: Session) -> dict[int, str]:
    return {row.id: row.name for row in list(db.scalars(select(NotifyRecipient)))}


def create_alert(
    db: Session,
    *,
    symbol: str,
    indicators,
    join: str = "and",
    name: str | None = None,
    notes: str | None = None,
    push: bool = False,
    recipient_ids=None,
    interval_sec=None,
) -> tuple[Alert, dict | None]:
    code = (symbol or "").strip().upper()
    if not code:
        raise AlertError("请输入股票代码")
    ids = normalize_ids(indicators, {row.id for row in list_strategies(db)})
    people = normalize_recipient_ids(recipient_ids, set(_people_map(db)))
    op = normalize_join(join)
    title = (name or "").strip() or None
    row = Alert(
        symbol=code[:32],
        name=(title[:64] if title else None),
        notes=(notes or "").strip() or None,
        join=op,
        indicators=_dump_ids(ids),
        recipient_ids=json.dumps(people, ensure_ascii=False),
        allow_push=1 if push else 0,
        interval_sec=normalize_interval(interval_sec),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        _apply_eval(row, evaluate_row(db, row))
    except (AlertError, OhlcError) as exc:
        _apply_error(row, str(exc))
    test = _first_test(db, row, bool(row.allow_push))
    db.commit()
    db.refresh(row)
    return row, test


def update_alert(
    db: Session,
    row: Alert,
    *,
    symbol: str,
    indicators,
    join: str = "and",
    name: str | None = None,
    notes: str | None = None,
    push: bool = False,
    recipient_ids=None,
    interval_sec=None,
) -> tuple[Alert, dict | None]:
    code = (symbol or "").strip().upper()
    if not code:
        raise AlertError("请输入股票代码")
    ids = normalize_ids(indicators, {item.id for item in list_strategies(db)})
    people = normalize_recipient_ids(recipient_ids, set(_people_map(db)))
    op = normalize_join(join)
    title = (name or "").strip() or None
    turning_on = bool(push) and not row.allow_push
    row.symbol = code[:32]
    row.name = title[:64] if title else None
    row.notes = (notes or "").strip() or None
    row.join = op
    row.indicators = _dump_ids(ids)
    row.recipient_ids = json.dumps(people, ensure_ascii=False)
    row.allow_push = 1 if push else 0
    row.interval_sec = normalize_interval(interval_sec)
    db.commit()
    try:
        _apply_eval(row, evaluate_row(db, row))
    except (AlertError, OhlcError) as exc:
        _apply_error(row, str(exc))
    test = _first_test(db, row, turning_on)
    db.commit()
    db.refresh(row)
    return row, test


def set_alert_push(
    db: Session,
    row: Alert,
    push: bool | None = None,
    interval_sec=None,
) -> tuple[Alert, dict | None]:
    turning_on = False
    if push is not None:
        turning_on = bool(push) and not row.allow_push
        row.allow_push = 1 if push else 0
    if interval_sec is not None:
        row.interval_sec = normalize_interval(interval_sec)
    test = None
    if turning_on:
        row.last_checked_at = None
        try:
            _apply_eval(row, evaluate_row(db, row))
        except (AlertError, OhlcError) as exc:
            _apply_error(row, str(exc))
        test = _first_test(db, row, True)
    db.commit()
    db.refresh(row)
    return row, test


def delete_alert(db: Session, row: Alert) -> None:
    db.delete(row)
    db.commit()


def _notify_row(db: Session, row: Alert) -> list[dict]:
    if not row.allow_push:
        return []
    return maybe_notify(db, row, _load_detail(row.last_detail))


def refresh_alerts(db: Session, force: bool = False, enabled_only: bool = False) -> tuple[list[Alert], dict]:
    now = utcnow()
    rows = list_alerts(db)
    notified = 0
    notify_errors = []
    for row in rows:
        if enabled_only and not row.allow_push:
            continue
        should_eval = force or is_due(row, now)
        if should_eval:
            try:
                _apply_eval(row, evaluate_row(db, row))
            except (AlertError, OhlcError) as exc:
                _apply_error(row, str(exc))
        results = _notify_row(db, row)
        if any(item.get("ok") for item in results):
            notified += 1
        notify_errors.extend(
            f"{row.symbol} {item['channel']}：{item['error']}"
            for item in results
            if not item.get("ok") and item.get("error")
        )
    db.commit()
    latest = list_alerts(db)
    return latest, {
        "notified": notified,
        "notify_errors": notify_errors,
        "next_refresh": next_due_ts(latest, utcnow()),
    }


def tick_due_alerts() -> dict:
    if not _tick_lock.acquire(blocking=False):
        return {"notified": 0, "notify_errors": [], "skipped": True}
    try:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            _rows, extra = refresh_alerts(db, force=False, enabled_only=True)
            if extra.get("notified") or extra.get("notify_errors"):
                _log.info("alert tick notified=%s errors=%s", extra.get("notified"), extra.get("notify_errors"))
            return extra
        finally:
            db.close()
    except Exception:
        _log.exception("alert tick failed")
        return {"notified": 0, "notify_errors": ["自动计算失败"]}
    finally:
        _tick_lock.release()
