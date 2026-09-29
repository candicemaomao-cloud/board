"""
每日观察 · 分析页面
====================
把「每日观察」列表里手动维护的股票，一次性跑 4 块分析：
  1. 涨跌幅分析——日模式看锚定日 vs 前一交易日，周模式看锚定日前最近两周
  2. 回归线分析——现价在回归线上方还是下方（用每日观察里已经存好的数字，
     不现场重算——现场对几十只票逐个跑 Theil-Sen 回归太慢）
  3. Max Pain 分析——现价有没有"到达" Max Pain（±1% 以内算到达）
  4. 行业分析——按板块把涨跌幅分组算平均，看哪个板块涨得最猛

涨跌幅这块每日观察表里没有历史数据（只存"当前这一份快照"），所以现场去拉
每只股票的日线，跟"回归线"页面用的是同一套 fetch_closes_covering。为了别让
几十只票的网络请求排队等，用线程池并发拉。

回归线/Max Pain 这两块不区分日/周——它们本身就是"现在这一刻"的状态量
（回归线是趋势线现在的拟合值，Max Pain 挂在最近一期到期日上），套"本周 vs
上周"这种历史对比没有意义，所以固定用每日观察里最新存的数字，两种模式下
展示的是同一份。

可通过 asof 指定锚定日：涨跌幅/行业按该日回看；不传则用今天（日模式排除
未收盘的今日 K 线）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.services.daily_watch import DailyWatchError, list_watches
from app.services.ohlc import OhlcError, fetch_closes_covering

MAX_PAIN_REACHED_PCT = 0.01  # 现价跟 Max Pain 差距在 ±1% 以内算"已到达"


def _parse_asof(raw: str | date | None) -> date:
    if raw is None or raw == "":
        return date.today()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError as exc:
        raise DailyWatchError("日期格式不对，请用 YYYY-MM-DD") from exc


def _daily_closes(symbol: str, *, asof: date, lookback_days: int = 40) -> list[tuple[date, float]]:
    fetch_start = asof - timedelta(days=lookback_days)
    raw = fetch_closes_covering(symbol, "1d", start=fetch_start)
    bars = raw.get("ohlc_bars") or []
    seen: set = set()
    rows: list[tuple[date, float]] = []
    for b in bars:
        d = datetime.fromtimestamp(int(b["ts"]), tz=timezone.utc).date()
        if d in seen or d > asof:
            continue
        # 锚定今天时：今日 K 线可能未收盘，先排除
        if asof >= date.today() and d >= date.today():
            continue
        seen.add(d)
        rows.append((d, float(b["close"])))
    rows.sort(key=lambda r: r[0])
    return rows


def _period_change(rows: list[tuple[date, float]], mode: str, asof: date) -> dict | None:
    """日模式：锚定日（或之前最近交易日）相对前一交易日。
    周模式：按 ISO 周取每周最后收盘，拿锚定日前最近两个完整周比；
    若锚定日是今天，本周未走完则跳过本周。"""
    if not rows:
        return None
    today = date.today()
    complete = [r for r in rows if r[0] <= asof]
    if asof >= today:
        complete = [r for r in complete if r[0] < today] or complete

    if mode == "week":
        skip_week = today.isocalendar()[:2] if asof >= today else None
        weekly: dict[tuple[int, int], tuple[date, float]] = {}
        for d, c in complete:
            key = d.isocalendar()[:2]
            if skip_week and key == skip_week:
                continue
            if key not in weekly or d > weekly[key][0]:
                weekly[key] = (d, c)
        points = [v for _, v in sorted(weekly.items(), key=lambda kv: kv[0])]
    else:
        points = complete

    if len(points) < 2:
        return None
    (d0, c0), (d1, c1) = points[-2], points[-1]
    pct = (c1 - c0) / c0 if c0 else None
    return {
        "prev_date": d0.isoformat(),
        "prev_close": c0,
        "latest_date": d1.isoformat(),
        "latest_close": c1,
        "pct_change": pct,
    }


def _fetch_change(symbol: str, mode: str, asof: date) -> tuple[dict | None, str | None]:
    lookback = 120 if mode == "week" else 60
    try:
        closes = _daily_closes(symbol, asof=asof, lookback_days=lookback)
    except OhlcError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, str(exc)
    change = _period_change(closes, mode, asof)
    if change is None:
        return None, "数据不够，算不出涨跌幅"
    return change, None


def analyze(
    db: Session,
    symbols: list[str] | None,
    mode: str = "day",
    asof: str | date | None = None,
) -> dict:
    mode = mode if mode in ("day", "week") else "day"
    asof_d = _parse_asof(asof)
    rows = list_watches(db)
    if symbols:
        wanted = {s.strip().upper() for s in symbols if s and s.strip()}
        rows = [r for r in rows if r.symbol in wanted]
    if not rows:
        raise DailyWatchError("没有可分析的股票，先在每日观察里勾几只，或者先加几条记录")

    warnings: list[str] = []

    # --- 1. 涨跌幅分析：并发拉每只票的日线，避免几十只票排队等网络 ---
    change_by_symbol: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_fetch_change, row.symbol, mode, asof_d): row.symbol for row in rows
        }
        for fut in as_completed(futures):
            symbol = futures[fut]
            change, err = fut.result()
            change_by_symbol[symbol] = change
            if err:
                warnings.append(f"{symbol}：{err}")

    price_rows = []
    for row in rows:
        change = change_by_symbol.get(row.symbol)
        if not change:
            continue
        price_rows.append({"symbol": row.symbol, "sector": row.sector or "其他", **change})
    price_rows.sort(key=lambda r: r["pct_change"] if r["pct_change"] is not None else 0, reverse=True)

    # --- 2. 回归线分析：直接用每日观察里存好的数字 ---
    regression_rows = []
    above = below = unknown = 0
    for row in rows:
        if row.regression_line is None or row.current_price is None:
            regression_rows.append({
                "symbol": row.symbol, "sector": row.sector or "其他",
                "current_price": row.current_price, "regression_line": row.regression_line,
                "position": "unknown", "distance_pct": None,
            })
            unknown += 1
            continue
        distance_pct = (row.current_price - row.regression_line) / row.regression_line if row.regression_line else None
        position = "above" if row.current_price >= row.regression_line else "below"
        if position == "above":
            above += 1
        else:
            below += 1
        regression_rows.append({
            "symbol": row.symbol, "sector": row.sector or "其他",
            "current_price": row.current_price, "regression_line": row.regression_line,
            "position": position, "distance_pct": distance_pct,
        })
    regression_rows.sort(key=lambda r: r["distance_pct"] if r["distance_pct"] is not None else 0, reverse=True)

    # --- 3. Max Pain 分析：同样用每日观察里存好的数字 ---
    max_pain_rows = []
    reached = not_reached = unknown_mp = 0
    for row in rows:
        if row.max_pain is None or row.current_price is None:
            max_pain_rows.append({
                "symbol": row.symbol, "sector": row.sector or "其他",
                "current_price": row.current_price, "max_pain": row.max_pain,
                "reached": None, "distance_pct": None,
            })
            unknown_mp += 1
            continue
        distance_pct = (row.current_price - row.max_pain) / row.max_pain if row.max_pain else None
        is_reached = abs(distance_pct) <= MAX_PAIN_REACHED_PCT if distance_pct is not None else False
        if is_reached:
            reached += 1
        else:
            not_reached += 1
        max_pain_rows.append({
            "symbol": row.symbol, "sector": row.sector or "其他",
            "current_price": row.current_price, "max_pain": row.max_pain,
            "reached": is_reached, "distance_pct": distance_pct,
        })
    max_pain_rows.sort(key=lambda r: abs(r["distance_pct"]) if r["distance_pct"] is not None else 999)

    # --- 4. 行业分析：复用第 1 块已经拉到的涨跌幅，按板块分组算平均 ---
    sector_map: dict[str, list[float]] = {}
    for r in price_rows:
        if r["pct_change"] is None:
            continue
        sector_map.setdefault(r["sector"], []).append(r["pct_change"])
    sector_rows = [
        {"sector": sec, "avg_pct_change": sum(vals) / len(vals), "count": len(vals)}
        for sec, vals in sector_map.items()
    ]
    sector_rows.sort(key=lambda r: r["avg_pct_change"], reverse=True)

    sample = next((r for r in price_rows if r.get("latest_date") and r.get("prev_date")), None)
    if sample:
        period_label = f"{sample['latest_date']} vs {sample['prev_date']}"
    elif mode == "week":
        period_label = "最近两周"
    else:
        period_label = f"锚定 {asof_d.isoformat()}"

    return {
        "mode": mode,
        "asof": asof_d.isoformat(),
        "period_label": period_label,
        "symbols_analyzed": [row.symbol for row in rows],
        "price_change": {"period_label": period_label, "rows": price_rows},
        "regression": {"rows": regression_rows, "above_count": above, "below_count": below, "unknown_count": unknown},
        "max_pain": {
            "rows": max_pain_rows, "reached_count": reached, "not_reached_count": not_reached,
            "unknown_count": unknown_mp, "reached_threshold_pct": MAX_PAIN_REACHED_PCT,
        },
        "sector": {"period_label": period_label, "rows": sector_rows},
        "warnings": warnings,
    }
