from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.services.indicator_code import IndicatorCodeError, is_code_formula, last_signal
from app.services.indicators import snapshot
from app.services.ohlc import OhlcError, fetch_closes, fetch_closes_covering
from app.services.strategy_spec import INDICATOR_MAP, eval_formula, flags_from_snapshot

SCAN_TIMEFRAMES = ("1d", "1w", "4h", "30m", "5m")

WARMUP = 80
MAX_SCAN = {
    "1d": 800,
    "1w": 400,
    "4h": 800,
    "30m": 2000,
    "5m": 2500,
}


class ScanError(OhlcError):
    pass


def _asof(ts, timeframe: str) -> str:
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    if timeframe in {"1d", "1w"}:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M")


def _parse_day(raw: str | None, fallback: date) -> date:
    text = str(raw or "").strip()
    if not text:
        return fallback
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ScanError("日期格式不对，请用 YYYY-MM-DD") from exc


def _next_open(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _bar_day(ts) -> date:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()


def _cut(ohlc: dict | None, end_ts: int) -> dict | None:
    if not ohlc:
        return None
    bars = [row for row in (ohlc.get("ohlc_bars") or []) if row.get("ts") is not None and int(row["ts"]) <= end_ts]
    if not bars:
        return None
    closes = [float(row["close"]) for row in bars]
    return {
        **ohlc,
        "ohlc_bars": bars,
        "closes": closes,
        "price": closes[-1],
        "bars": len(closes),
    }


def _fetch_extra(symbol: str, timeframe: str, source: str) -> dict:
    extra: dict = {}
    for need in ("4h", "1d"):
        if timeframe == need:
            continue
        try:
            extra[need] = fetch_closes(symbol, need, apply_live=False)
        except OhlcError:
            extra[need] = None
    if source != "binance":
        for bench in ("SPY", "QQQ"):
            try:
                extra[bench.lower()] = fetch_closes(bench, timeframe, apply_live=False)
            except OhlcError:
                extra[bench.lower()] = None
    return extra


def _cut_extra(extra: dict, end_ts: int) -> dict:
    return {key: _cut(value, end_ts) for key, value in extra.items()}


def _eval_user_plan(plan: dict, window: dict, flags: dict) -> bool:
    formula = plan.get("formula") or {}
    if is_code_formula(formula):
        return last_signal(str(formula.get("code") or ""), window.get("ohlc_bars") or [])
    return bool(eval_formula(formula, flags))


def scan_hits(
    symbol: str,
    timeframe: str,
    start: str | None,
    end: str | None,
    indicator_ids: list[str] | None = None,
    formula: dict | None = None,
    user_plans: list[dict] | None = None,
) -> dict:
    tf = timeframe if timeframe in SCAN_TIMEFRAMES else "1d"
    ids = []
    seen = set()
    for raw in indicator_ids or []:
        cid = str(raw or "").strip()
        if not cid or cid in seen or cid not in INDICATOR_MAP:
            continue
        seen.add(cid)
        ids.append(cid)

    plans = []
    plan_seen: set[int] = set()
    for raw in user_plans or []:
        try:
            pid = int(raw.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if pid in plan_seen:
            continue
        plan_seen.add(pid)
        plans.append(
            {
                "id": pid,
                "name": str(raw.get("name") or f"指标{pid}"),
                "kind": str(raw.get("kind") or "formula"),
                "formula": raw.get("formula") or {},
            }
        )

    if not ids and not formula and not plans:
        raise ScanError("请至少选一个指标")

    today = datetime.now(timezone.utc).date()
    try:
        default_start = today.replace(year=today.year - 1)
    except ValueError:
        default_start = today.replace(year=today.year - 1, day=28)
    start_day = _parse_day(start, default_start)
    end_day = _parse_day(end, today)
    if start_day > end_day:
        raise ScanError("开始日期不能晚于结束日期")

    ohlc = fetch_closes_covering(symbol, tf, start_day)
    bars = list(ohlc.get("ohlc_bars") or [])
    if len(bars) < 30:
        raise ScanError("K 线不够，换个周期或代码再试")

    extra = _fetch_extra(ohlc["symbol"], tf, ohlc.get("source") or "")
    data_first = _bar_day(bars[0]["ts"])
    data_last = _bar_day(bars[-1]["ts"])
    indexes = [
        i
        for i, row in enumerate(bars)
        if i >= WARMUP and start_day <= _bar_day(row["ts"]) <= end_day
    ]
    cap = MAX_SCAN.get(tf, 400)
    truncated = False
    if len(indexes) > cap:
        indexes = indexes[:cap]
        truncated = True
    if not indexes:
        hint = ""
        if tf in {"5m", "30m", "4h"}:
            hint = "短周期K线只有最近几天，统计某一个月请改用日线。"
        raise ScanError(
            f"{start_day.isoformat()} 至 {end_day.isoformat()} 没有可统计的 K 线。"
            f"现在这档数据是 {data_first.isoformat()} 到 {data_last.isoformat()}。{hint}"
        )

    note = None
    actual_start = _bar_day(bars[indexes[0]]["ts"])
    actual_end = _bar_day(bars[indexes[-1]]["ts"])
    open_from = _next_open(start_day)
    if actual_start > open_from:
        note = (
            f"你选了 {start_day.isoformat()} 起，但这档 K 线从 {actual_start.isoformat()} 才有，"
            f"{start_day.isoformat()} 到那天之前没算进去。"
            f"{'要统计更早请改用日线，5 分钟只能覆盖最近一段时间。' if tf in {'5m', '30m', '4h'} else ''}"
        )
    elif start_day.weekday() >= 5:
        note = f"{start_day.isoformat()} 是周末，美股没有 K 线，从 {actual_start.isoformat()} 开盘算起。"
    if truncated:
        extra_note = (
            f"区间里 K 线太多，从 {actual_start.isoformat()} 起只算了 {len(indexes)} 根，"
            f"到 {actual_end.isoformat()}。"
        )
        note = f"{note} {extra_note}".strip() if note else extra_note

    buckets = {cid: [] for cid in ids}
    plan_buckets = {p["id"]: [] for p in plans}
    strategy_hits: list[dict] = []
    scanned = 0
    for i in indexes:
        bar = bars[i]
        end_ts = int(bar["ts"])
        window = {
            **ohlc,
            "ohlc_bars": bars[: i + 1],
            "closes": [float(row["close"]) for row in bars[: i + 1]],
            "price": float(bar["close"]),
            "bars": i + 1,
        }
        if window["bars"] < 30:
            continue
        flags = flags_from_snapshot(snapshot(window, _cut_extra(extra, end_ts)))
        scanned += 1
        hit = {
            "date": _asof(end_ts, tf),
            "price": round(float(bar["close"]), 4),
        }
        for cid in ids:
            if flags.get(cid):
                buckets[cid].append(hit)
        for plan in plans:
            try:
                ok = _eval_user_plan(plan, window, flags)
            except IndicatorCodeError as exc:
                raise ScanError(str(exc)) from exc
            if ok:
                plan_buckets[plan["id"]].append(hit)
        if formula:
            if is_code_formula(formula):
                try:
                    ok = last_signal(str(formula.get("code") or ""), window.get("ohlc_bars") or [])
                except IndicatorCodeError as exc:
                    raise ScanError(str(exc)) from exc
                if ok:
                    strategy_hits.append(hit)
            elif eval_formula(formula, flags):
                strategy_hits.append(hit)

    def _pack(cid: str, hits: list[dict]) -> dict:
        spec = INDICATOR_MAP.get(cid) or {}
        return {
            "id": cid,
            "name": spec.get("name") or cid,
            "group": spec.get("group") or "",
            "count": len(hits),
            "rate": round(len(hits) / scanned, 4) if scanned else 0,
            "last_date": hits[-1]["date"] if hits else None,
            "last_price": hits[-1]["price"] if hits else None,
            "dates": hits,
        }

    def _pack_plan(plan: dict, hits: list[dict]) -> dict:
        kind = plan.get("kind") or "formula"
        return {
            "id": f"plan:{plan['id']}",
            "plan_id": plan["id"],
            "name": plan["name"],
            "group": "我的指标",
            "kind": kind,
            "count": len(hits),
            "rate": round(len(hits) / scanned, 4) if scanned else 0,
            "last_date": hits[-1]["date"] if hits else None,
            "last_price": hits[-1]["price"] if hits else None,
            "dates": hits,
        }

    first = bars[indexes[0]]
    last = bars[indexes[-1]]
    items = [_pack(cid, buckets[cid]) for cid in ids]
    items.extend(_pack_plan(plan, plan_buckets[plan["id"]]) for plan in plans)
    return {
        "symbol": ohlc["symbol"],
        "source": ohlc["source"],
        "timeframe": tf,
        "start": _asof(first["ts"], tf),
        "end": _asof(last["ts"], tf),
        "requested_start": start_day.isoformat(),
        "requested_end": end_day.isoformat(),
        "bars": scanned,
        "truncated": truncated,
        "note": note,
        "items": items,
        "strategy_count": len(strategy_hits) if formula else None,
        "strategy_rate": round(len(strategy_hits) / scanned, 4) if formula and scanned else None,
        "strategy_dates": strategy_hits if formula else [],
    }
