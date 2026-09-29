"""宏观事件统计：某标的在宏观 Surprise 冲击后的 1/5/20 日收益与波动。"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any

from app.services.macro import NFP_HEADLINE, build_group
from app.services.tradingview import _run_async, _tv_history, resolve_ticker

HORIZONS = (1, 5, 20)
# 宏观发布日 ± 几天内有财报，视为可能重叠
EARNINGS_NEAR_DAYS = 3
# 事件后持仓窗口内有财报（按日历日近似 20 交易日≈28 自然日）
EARNINGS_HOLD_CAL_DAYS = 28

_ETF_LIKE = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "IVV", "ARKK", "XLF", "XLE", "XLK", "TLT", "GLD", "SLV",
    "SOXX", "SMH", "XLI", "XLY", "XLP", "XLB", "XLU", "XLRE", "XLC", "HYG", "USO", "KRE",
}

INDICATORS: dict[str, dict[str, Any]] = {
    "cpi": {
        "label": "CPI 总同比",
        "group": "cpi",
        "field": "yoy",
        "pick": "headline",
        "release_day": 13,
    },
    "cpi_core": {
        "label": "核心 CPI 同比",
        "group": "cpi",
        "field": "yoy",
        "pick": "core",
        "release_day": 13,
    },
    "pce": {
        "label": "PCE 总同比",
        "group": "pce",
        "field": "yoy",
        "pick": "headline",
        "release_day": 28,
    },
    "pce_core": {
        "label": "核心 PCE 同比",
        "group": "pce",
        "field": "yoy",
        "pick": "core",
        "release_day": 28,
    },
    "ppi": {
        "label": "PPI 总同比",
        "group": "ppi",
        "field": "yoy",
        "pick": "headline",
        "release_day": 13,
    },
    "nfp": {
        "label": "非农月增（千人）",
        "group": "nfp",
        "field": "mom",
        "pick": "nfp",
        "release_day": 7,
    },
    "pmi": {
        "label": "制造业 PMI",
        "group": "pmi",
        "field": "value",
        "pick": "pmi",
        "release_day": 1,
    },
    "ust5": {
        "label": "5年期美债日变动",
        "group": "treasury",
        "field": "mom",
        "pick": "RIFLGFCY05_N.B",
        "release_day": 0,  # 日频：当天即事件日
        "freq": "daily",
    },
    "ust10": {
        "label": "10年期美债日变动",
        "group": "treasury",
        "field": "mom",
        "pick": "RIFLGFCY10_N.B",
        "release_day": 0,
        "freq": "daily",
    },
    "ust30": {
        "label": "30年期美债日变动",
        "group": "treasury",
        "field": "mom",
        "pick": "RIFLGFCY30_N.B",
        "release_day": 0,
        "freq": "daily",
    },
}

EVENT_BUCKETS = {
    "hot": {"label": "超预期 Z≥+1σ", "test": lambda z: z >= 1.0},
    "near_hot": {"label": "约 +1σ [0.75,1.25]", "test": lambda z: 0.75 <= z <= 1.25},
    "cold": {"label": "不及预期 Z≤−1σ", "test": lambda z: z <= -1.0},
    "near": {"label": "接近预期 |Z|<0.5", "test": lambda z: abs(z) < 0.5},
    "all": {"label": "全部发布（对照）", "test": lambda z: True},
}


class MacroStudyError(Exception):
    pass


def _round(x: float | None, n: int = 4) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), n)


def list_study_options() -> dict:
    return {
        "indicators": [
            {"id": k, "label": v["label"], "group": v["group"]} for k, v in INDICATORS.items()
        ],
        "buckets": [{"id": k, "label": v["label"]} for k, v in EVENT_BUCKETS.items()],
        "horizons": list(HORIZONS),
        "lookbacks": [
            {"id": 3, "label": "近 3 年"},
            {"id": 5, "label": "近 5 年"},
            {"id": 10, "label": "近 10 年"},
            {"id": 20, "label": "近 20 年"},
        ],
        "default_lookback": 5,
        "presets": ["QQQ", "SPY", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META"],
        "note": (
            "Expected=近6期均值（无调查共识）；发布日为日历近似；"
            "1/5/20 日 = 事件后持有交易日数（不是年份）；"
            "回看区间只筛事件样本，Z 仍用更长宏观史估计；"
            "美债用日变动（百分点）做 Surprise。"
        ),
    }


def _pick_series(payload: dict, pick: str, group: str) -> dict | None:
    rows = payload.get("series") or []
    if group == "treasury" or str(pick or "").startswith("RIFLGFCY"):
        for row in rows:
            if row.get("id") == pick:
                return row
        return rows[0] if rows else None
    if pick == "nfp":
        for row in rows:
            if row.get("id") == NFP_HEADLINE:
                return row
        return rows[0] if rows else None
    if pick == "pmi":
        for row in rows:
            if row.get("kind") == "headline" and "制造" in (row.get("name") or ""):
                return row
        for row in rows:
            if row.get("kind") == "headline":
                return row
        return rows[0] if rows else None

    want_core = pick == "core"
    for row in rows:
        name = row.get("name") or ""
        if want_core and "核心" in name:
            return row
        if not want_core and ("总" in name or name.startswith("总")):
            return row
    # id heuristics
    if group == "cpi":
        key = "SA0L1E" if want_core else "SA0"
        for row in rows:
            sid = row.get("id") or ""
            item = row.get("item") or ""
            if sid.endswith(key) or item == key:
                return row
    if group == "pce":
        sid_want = "DPCCRG-M" if want_core else "DPCERG-M"
        for row in rows:
            if row.get("id") == sid_want:
                return row
    if group == "ppi":
        sid_want = "WPSFD49104" if want_core else "WPSFD4"
        for row in rows:
            if row.get("id") == sid_want:
                return row
    return rows[0] if rows else None


def _metric_series(row: dict, field: str) -> list[dict]:
    out = []
    for p in row.get("points") or []:
        v = p.get(field)
        if v is None and field == "mom":
            continue
        if v is None and field != "value":
            continue
        if v is None:
            v = p.get("value")
        if v is None:
            continue
        out.append({"date": p.get("date"), "value": float(v)})
    # NFP fallback: diff levels
    if field == "mom" and len(out) < 12:
        levels = []
        for p in row.get("points") or []:
            if p.get("value") is None:
                continue
            levels.append({"date": p["date"], "value": float(p["value"])})
        out = []
        for i in range(1, len(levels)):
            out.append({
                "date": levels[i]["date"],
                "value": levels[i]["value"] - levels[i - 1]["value"],
            })
    return out


def _surprise_events(hist: list[dict], *, lookback: int = 6, min_hist: int = 36) -> list[dict]:
    if len(hist) < min_hist + 2:
        raise MacroStudyError("宏观历史样本不足")
    ys = [h["value"] for h in hist]
    out = []
    for i in range(min_hist, len(hist)):
        expected = mean(ys[i - lookback : i])
        surps = [ys[j] - mean(ys[j - lookback : j]) for j in range(lookback, i)]
        if len(surps) < 24:
            continue
        sig = pstdev(surps) or 1e-6
        surprise = ys[i] - expected
        out.append({
            "period": hist[i]["date"],
            "actual": _round(ys[i], 4),
            "expected": _round(expected, 4),
            "surprise": _round(surprise, 4),
            "z": _round(surprise / sig, 3),
            "sigma": _round(sig, 4),
        })
    return out


def _period_to_release(period: str, release_day: int) -> date:
    parts = str(period or "").split("-")
    # 日频：period 已是 YYYY-MM-DD
    if len(parts) >= 3:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    y, m = map(int, parts[:2])
    y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
    day = max(1, int(release_day or 13))
    for d in (day, 28, 15, 7, 1):
        try:
            return date(y2, m2, d)
        except ValueError:
            continue
    return date(y2, m2, 1)


def _parse_mdy(raw: str | None) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _earnings_dates(symbol: str) -> tuple[list[date], str | None]:
    """返回 (财报公布日列表, 提示)。ETF/指数通常无个股财报。"""
    code = (symbol or "").strip().upper()
    if not code or code.endswith(("USDT", "USDC")):
        return [], "无财报日历"
    if code in _ETF_LIKE:
        return [], "ETF/指数无个股财报"
    try:
        import httpx
        from urllib.parse import quote

        from app.services.fundamentals import NASDAQ_HEADERS, _get, _surprise_rows

        with httpx.Client(timeout=20.0, headers=NASDAQ_HEADERS, follow_redirects=True) as client:
            raw = _get(client, f"/api/company/{quote(code, safe='')}/earnings-surprise")
        rows = _surprise_rows(raw)
        dates = []
        for row in rows:
            d = _parse_mdy(row.get("reported"))
            if d:
                dates.append(d)
        dates = sorted(set(dates))
        if not dates:
            return [], "未拉到财报日"
        return dates, None
    except Exception:
        return [], "财报日历暂不可用"


def _tag_earnings(trade_iso: str | None, earn_dates: list[date]) -> dict:
    if not trade_iso:
        return {
            "earnings": False,
            "earnings_label": "—",
            "earnings_date": None,
            "earnings_gap_days": None,
            "earnings_in_hold": False,
        }
    td = datetime.fromisoformat(trade_iso).date()
    near = None
    near_gap = None
    for ed in earn_dates:
        gap = (ed - td).days
        if abs(gap) <= EARNINGS_NEAR_DAYS:
            if near is None or abs(gap) < abs(near_gap):
                near, near_gap = ed, gap
    hold = None
    for ed in earn_dates:
        gap = (ed - td).days
        if 0 <= gap <= EARNINGS_HOLD_CAL_DAYS:
            if hold is None or gap < (hold - td).days:
                hold = ed
    if near is not None:
        return {
            "earnings": True,
            "earnings_label": f"是 · {near.isoformat()}",
            "earnings_date": near.isoformat(),
            "earnings_gap_days": near_gap,
            "earnings_in_hold": True,
        }
    if hold is not None:
        return {
            "earnings": False,
            "earnings_label": f"持仓内 · {hold.isoformat()}",
            "earnings_date": hold.isoformat(),
            "earnings_gap_days": (hold - td).days,
            "earnings_in_hold": True,
        }
    return {
        "earnings": False,
        "earnings_label": "否",
        "earnings_date": None,
        "earnings_gap_days": None,
        "earnings_in_hold": False,
    }


def _load_closes(symbol: str, n_bars: int = 5000) -> tuple[dict[date, float], str]:
    raw = (symbol or "").strip().upper()
    if not raw:
        raise MacroStudyError("请填写股票或指数代码")
    ticker = resolve_ticker(raw)
    try:
        bars = _run_async(_tv_history(ticker, "1D", max(800, min(int(n_bars), 5000))))
    except Exception as exc:
        raise MacroStudyError(f"拉不到 {raw} 日线：{exc}") from exc
    if not bars or len(bars) < 80:
        raise MacroStudyError(f"{raw} 日线样本不足")
    closes: dict[date, float] = {}
    for b in bars:
        ts = b.get("ts")
        c = b.get("close")
        if ts is None or c is None:
            continue
        d = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        closes[d] = float(c)
    if len(closes) < 80:
        raise MacroStudyError(f"{raw} 有效交易日不足")
    return closes, ticker


def _next_trading(d: date, idx: dict[date, int]) -> date | None:
    for i in range(0, 12):
        x = d + timedelta(days=i)
        if x in idx:
            return x
    return None


def _fwd_stats(release: date, days: list[date], closes: dict[date, float], idx: dict[date, int]) -> dict | None:
    d0 = _next_trading(release, idx)
    if d0 is None:
        return None
    i0 = idx[d0]
    p0 = closes[d0]
    out: dict[str, Any] = {"trade_date": d0.isoformat()}
    if i0 >= 21:
        rr = [math.log(closes[days[k]] / closes[days[k - 1]]) for k in range(i0 - 20, i0)]
        out["vol20_pre"] = pstdev(rr) * math.sqrt(252)
    else:
        out["vol20_pre"] = None
    for h in HORIZONS:
        if i0 + h >= len(days):
            out[f"ret_{h}"] = None
            out[f"vol_{h}"] = None
            continue
        out[f"ret_{h}"] = closes[days[i0 + h]] / p0 - 1.0
        if h >= 2:
            rr = [
                math.log(closes[days[k]] / closes[days[k - 1]])
                for k in range(i0 + 1, i0 + h + 1)
            ]
            out[f"vol_{h}"] = pstdev(rr) * math.sqrt(252) if len(rr) >= 2 else None
        else:
            out[f"vol_{h}"] = None
    return out


def _summarize(rows: list[dict], label: str) -> dict:
    def avg(key: str) -> float | None:
        xs = [r[key] for r in rows if r.get(key) is not None]
        return mean(xs) if xs else None

    def med(key: str) -> float | None:
        xs = sorted(r[key] for r in rows if r.get(key) is not None)
        return xs[len(xs) // 2] if xs else None

    def pos(key: str) -> float | None:
        xs = [r[key] for r in rows if r.get(key) is not None]
        return sum(1 for x in xs if x > 0) / len(xs) if xs else None

    ch = []
    for r in rows:
        if r.get("vol_20") is not None and r.get("vol20_pre"):
            ch.append(r["vol_20"] / r["vol20_pre"] - 1.0)

    body: dict[str, Any] = {
        "label": label,
        "n": len(rows),
        "from": rows[0]["period"] if rows else None,
        "to": rows[-1]["period"] if rows else None,
        "mean_z": _round(avg("z"), 3),
    }
    for h in HORIZONS:
        body[f"ret_{h}_mean"] = _round(avg(f"ret_{h}"), 6)
        body[f"ret_{h}_med"] = _round(med(f"ret_{h}"), 6)
        body[f"ret_{h}_pos"] = _round(pos(f"ret_{h}"), 4)
        body[f"vol_{h}_mean"] = _round(avg(f"vol_{h}"), 6)
    body["vol20_pre_mean"] = _round(avg("vol20_pre"), 6)
    body["vol20_rel_change"] = _round(mean(ch) if ch else None, 4)
    return body


def run_macro_event_study(
    *,
    symbol: str,
    indicator: str = "cpi",
    bucket: str = "hot",
    compare_all: bool = True,
    lookback_years: int = 5,
    n_bars: int = 5000,
    max_events: int = 80,
) -> dict:
    ind = INDICATORS.get(indicator)
    if not ind:
        raise MacroStudyError(f"不支持指标 {indicator}")
    if bucket not in EVENT_BUCKETS:
        raise MacroStudyError(f"不支持事件桶 {bucket}")

    years = max(1, min(int(lookback_years or 5), 25))
    cutoff = date.today() - timedelta(days=int(years * 365.25))

    closes, ticker = _load_closes(symbol, n_bars=n_bars)
    days = sorted(closes.keys())
    idx = {d: i for i, d in enumerate(days)}

    payload = build_group(ind["group"], detail=False)
    series = _pick_series(payload, ind["pick"], ind["group"])
    if not series:
        raise MacroStudyError("宏观序列为空")
    hist = _metric_series(series, ind["field"])
    events = _surprise_events(hist)

    def collect(test_fn) -> list[dict]:
        rows = []
        for e in events:
            if not test_fn(float(e["z"])):
                continue
            st = _fwd_stats(_period_to_release(e["period"], ind["release_day"]), days, closes, idx)
            if not st or st.get("ret_1") is None:
                continue
            td = datetime.fromisoformat(st["trade_date"]).date()
            if td < cutoff:
                continue
            if td < days[0] + timedelta(days=40):
                continue
            rows.append({**e, **st})
        return rows

    selected = collect(EVENT_BUCKETS[bucket]["test"])
    summary = _summarize(selected, EVENT_BUCKETS[bucket]["label"])

    baseline = None
    excess = None
    if compare_all and bucket != "all":
        all_rows = collect(EVENT_BUCKETS["all"]["test"])
        baseline = _summarize(all_rows, EVENT_BUCKETS["all"]["label"])
        excess = {}
        for h in HORIZONS:
            a = summary.get(f"ret_{h}_mean")
            b = baseline.get(f"ret_{h}_mean")
            excess[f"ret_{h}"] = _round(None if a is None or b is None else a - b, 6)
            if h >= 5:
                va = summary.get(f"vol_{h}_mean")
                vb = baseline.get(f"vol_{h}_mean")
                excess[f"vol_{h}"] = _round(None if va is None or vb is None else va - vb, 6)

    # 明细：按交易日从近到远
    selected_sorted = sorted(
        selected,
        key=lambda r: (r.get("trade_date") or r.get("period") or ""),
        reverse=True,
    )
    show = selected_sorted[:max_events]
    earn_dates, earn_note = _earnings_dates((symbol or "").strip().upper())
    event_rows = []
    earn_near_n = 0
    earn_hold_n = 0
    for r in show:
        tag = _tag_earnings(r.get("trade_date"), earn_dates)
        if tag["earnings"]:
            earn_near_n += 1
        if tag["earnings_in_hold"]:
            earn_hold_n += 1
        event_rows.append({
            "period": r["period"],
            "trade_date": r.get("trade_date"),
            "z": r.get("z"),
            "actual": r.get("actual"),
            "expected": r.get("expected"),
            "ret_1": _round(r.get("ret_1"), 6),
            "ret_5": _round(r.get("ret_5"), 6),
            "ret_20": _round(r.get("ret_20"), 6),
            "vol_5": _round(r.get("vol_5"), 6),
            "vol_20": _round(r.get("vol_20"), 6),
            "vol20_pre": _round(r.get("vol20_pre"), 6),
            **tag,
        })

    # verdict text
    r5 = summary.get("ret_5_mean")
    r20 = summary.get("ret_20_mean")
    vchg = summary.get("vol20_rel_change")
    bits = []
    if summary["n"] == 0:
        bits.append("该回看区间内样本为 0，可加长回看或换指标/事件桶。")
    else:
        if r5 is not None:
            bits.append(f"5 交易日均收益 {r5 * 100:+.2f}%")
        if r20 is not None:
            bits.append(f"20 交易日均收益 {r20 * 100:+.2f}%")
        if vchg is not None:
            bits.append(f"事后/事前 20 日波动 {vchg * 100:+.1f}%")
        if excess and excess.get("ret_20") is not None:
            bits.append(f"相对全部发布 20 日超额 {excess['ret_20'] * 100:+.2f}pp")
        if earn_dates:
            bits.append(
                f"明细中宏观±{EARNINGS_NEAR_DAYS}日重叠财报 {earn_near_n}/{len(event_rows)}，"
                f"持仓窗内有财报 {earn_hold_n}/{len(event_rows)}"
            )
        elif earn_note:
            bits.append(earn_note)

    sample_from = min((r["trade_date"] for r in selected), default=None)
    sample_to = max((r["trade_date"] for r in selected), default=None)

    return {
        "ok": True,
        "symbol": (symbol or "").strip().upper(),
        "ticker": ticker,
        "price_from": days[0].isoformat(),
        "price_to": days[-1].isoformat(),
        "price_bars": len(days),
        "lookback_years": years,
        "sample_from": sample_from,
        "sample_to": sample_to,
        "cutoff": cutoff.isoformat(),
        "indicator": indicator,
        "indicator_label": ind["label"],
        "series_name": series.get("name"),
        "bucket": bucket,
        "bucket_label": EVENT_BUCKETS[bucket]["label"],
        "summary": summary,
        "baseline": baseline,
        "excess_vs_all": excess,
        "events": event_rows,
        "earnings_note": earn_note,
        "earnings_near_n": earn_near_n,
        "earnings_hold_n": earn_hold_n,
        "verdict": "；".join(bits) if bits else "",
        "method": list_study_options()["note"]
        + f" 财报：Nasdaq 公布日；「是」=宏观日±{EARNINGS_NEAR_DAYS}天；「持仓内」=之后约{EARNINGS_HOLD_CAL_DAYS}个自然日内。",
    }
