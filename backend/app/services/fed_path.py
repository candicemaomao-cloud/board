"""下次 FOMC：市场隐含概率 vs 泰勒规则独立判断。"""

from __future__ import annotations

import calendar
import json
import math
from datetime import date
from typing import Any

import httpx

from app.services.market import FOMC_MEETINGS

HEADERS = {
    "User-Agent": "PnlBoard/1.0 (personal trading dashboard)",
    "Accept": "application/json",
}

R_STAR = 1.00
PI_STAR = 2.00
U_STAR = 4.20
STEP = 0.25
ZQ_MONTH = "FGHJKMNQUVXZ"
MONTH_EN = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def _round(x: float | None, n: int = 2) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), n)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def next_fomc(today: date | None = None) -> dict | None:
    today = today or date.today()
    for start_s, end_s, sep in FOMC_MEETINGS:
        end = date.fromisoformat(end_s)
        if end >= today:
            start = date.fromisoformat(start_s)
            return {
                "start": start_s,
                "end": end_s,
                "decision": end,
                "sep": sep,
                "label": f"{end.month}/{end.day}",
                "title": f"{end.year}年{end.month}月{end.day}日" + ("（会发新点阵）" if sep else ""),
            }
    return None


# 最新 SEP 点阵。下次季度会议发布后改这一份。
# 来源：https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20260617.htm
# 2026 年底：4.375×1 + 4.125×5 + 3.875×3 + 3.625×8 + 3.375×1 = 18（Warsh 未提交）
SEP_DOTS = {
    "released": "2026-06-17",
    "label": "2026年6月点阵图",
    "url": "https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20260617.htm",
    "year": 2026,
    "n": 18,
    "skipped": "主席 Warsh 未提交",
    "median": 3.8,
    "counts": (
        (4.375, 1),
        (4.125, 5),
        (3.875, 3),
        (3.625, 8),
        (3.375, 1),
    ),
}


def _sep_dots(current_rate: float | None) -> dict:
    """委员会点阵是年底适当利率，不是对下次会议的投票。"""
    mid = float(current_rate) if current_rate is not None else 3.625
    n_hike = n_hold = n_cut = 0
    buckets = []
    for level, n in SEP_DOTS["counts"]:
        buckets.append({"level": level, "n": n})
        if level > mid + 0.06:
            n_hike += n
        elif level < mid - 0.06:
            n_cut += n
        else:
            n_hold += n
    total = n_hike + n_hold + n_cut or 1
    raw = {
        "cut50": 0.0,
        "cut25": n_cut / total,
        "hold": n_hold / total,
        "hike25": n_hike / total,
        "hike50": 0.0,
    }
    packed = _pack_probs(
        raw,
        source="FOMC 点阵图",
        note=(
            f"{SEP_DOTS['label']}，{SEP_DOTS['n']} 人点 2026 年底。"
            f"{SEP_DOTS['skipped']}。"
            "点阵只在 3/6/9/12 月发布；7月议息没有新点阵。这是年底路径，不是对下次会议的投票。"
        ),
    )
    packed["call"] = f"{n_hike}人加息"
    packed["lean"] = "hawkish" if n_hike >= n_hold and n_hike >= n_cut else (
        "dovish" if n_cut > n_hike else "neutral"
    )
    packed["n"] = SEP_DOTS["n"]
    packed["n_hike"] = n_hike
    packed["n_hold"] = n_hold
    packed["n_cut"] = n_cut
    packed["median"] = SEP_DOTS["median"]
    packed["released"] = SEP_DOTS["released"]
    packed["label"] = SEP_DOTS["label"]
    packed["year"] = SEP_DOTS["year"]
    packed["skipped"] = SEP_DOTS["skipped"]
    packed["buckets"] = buckets
    packed["current_mid"] = _round(mid, 3)
    return packed


def _softmax(logits: dict[str, float]) -> dict[str, float]:
    if not logits:
        return {}
    peak = max(logits.values())
    exps = {k: math.exp(float(v) - peak) for k, v in logits.items()}
    total = sum(exps.values()) or 1.0
    return {k: exps[k] / total for k in logits}


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    clean = {k: max(0.0, float(v)) for k, v in weights.items() if v is not None}
    s = sum(clean.values())
    if s <= 0:
        return {k: 0.0 for k in weights}
    return {k: clean.get(k, 0.0) / s for k in weights}


def _pack_probs(raw: dict[str, float], *, source: str, note: str = "") -> dict:
    p = _normalize({
        "cut50": raw.get("cut50") or 0.0,
        "cut25": raw.get("cut25") or 0.0,
        "hold": raw.get("hold") or 0.0,
        "hike25": raw.get("hike25") or 0.0,
        "hike50": raw.get("hike50") or 0.0,
    })
    cut = p["cut50"] + p["cut25"]
    hike = p["hike25"] + p["hike50"]
    hold = p["hold"]
    expected_bp = (-50 * p["cut50"] - 25 * p["cut25"] + 25 * p["hike25"] + 50 * p["hike50"])
    if hold >= max(cut, hike):
        call = "维持"
        lean = "neutral"
    elif hike > cut:
        call = "加息50bp+" if p["hike50"] >= p["hike25"] and p["hike50"] >= 0.18 else "加息"
        lean = "hawkish"
    else:
        call = "降息50bp+" if p["cut50"] >= p["cut25"] and p["cut50"] >= 0.18 else "降息"
        lean = "dovish"
    return {
        "source": source,
        "note": note,
        "p_cut50": _round(p["cut50"] * 100, 1),
        "p_cut25": _round(p["cut25"] * 100, 1),
        "p_hold": _round(hold * 100, 1),
        "p_hike25": _round(p["hike25"] * 100, 1),
        "p_hike50": _round(p["hike50"] * 100, 1),
        "p_cut": _round(cut * 100, 1),
        "p_hike": _round(hike * 100, 1),
        "expected_bp": _round(expected_bp, 1),
        "call": call,
        "lean": lean,
        "net": _round((hike - cut) * 100, 1),
    }


def _tv_last(symbol: str) -> float | None:
    from app.services.ohlc import fetch_closes

    try:
        ohlc = fetch_closes(symbol, "1d", apply_live=True)
        px = ohlc.get("price")
        if px is not None:
            return float(px)
        closes = [float(x) for x in (ohlc.get("closes") or []) if x is not None]
        return closes[-1] if closes else None
    except Exception:
        return None


def _polymarket_probs(meeting: date) -> dict | None:
    month = MONTH_EN[meeting.month - 1]
    year = str(meeting.year)
    try:
        with httpx.Client(timeout=12.0, headers=HEADERS, follow_redirects=True) as client:
            res = client.get(
                "https://gamma-api.polymarket.com/public-search",
                params={"q": f"Fed Decision in {meeting.strftime('%B')}"},
            )
            res.raise_for_status()
            events = (res.json() or {}).get("events") or []
            slug = None
            for ev in events:
                title = (ev.get("title") or "").lower()
                if "fed decision" in title and month in title:
                    slug = ev.get("slug")
                    if year in (ev.get("title") or "") or year in (ev.get("slug") or "") or year in (ev.get("endDate") or ""):
                        break
                    if slug:
                        break
            if not slug:
                return None
            res = client.get("https://gamma-api.polymarket.com/events", params={"slug": slug})
            res.raise_for_status()
            payload = res.json()
            ev = payload[0] if isinstance(payload, list) and payload else payload
            if not isinstance(ev, dict):
                return None
            raw = {"cut50": 0.0, "cut25": 0.0, "hold": 0.0, "hike25": 0.0, "hike50": 0.0}
            for mkt in ev.get("markets") or []:
                q = (mkt.get("question") or mkt.get("groupItemTitle") or "").lower()
                prices = mkt.get("outcomePrices")
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except json.JSONDecodeError:
                        prices = None
                yes = _num(prices[0]) if isinstance(prices, list) and prices else None
                if yes is None:
                    continue
                if "50" in q and ("decrease" in q or "cut" in q):
                    raw["cut50"] = yes
                elif "25" in q and ("decrease" in q or "cut" in q):
                    raw["cut25"] = yes
                elif "no change" in q or "unchanged" in q:
                    raw["hold"] = yes
                elif "50" in q and ("increase" in q or "hike" in q):
                    raw["hike50"] = yes
                elif "25" in q and ("increase" in q or "hike" in q):
                    raw["hike25"] = yes
            if sum(raw.values()) < 0.3:
                return None
            return _pack_probs(
                raw,
                source="Polymarket",
                note=f"预测市场 · {ev.get('title') or slug}",
            )
    except Exception:
        return None


def _kalshi_yes(row: dict) -> float | None:
    bid = _num(row.get("yes_bid_dollars"))
    ask = _num(row.get("yes_ask_dollars"))
    last = _num(row.get("last_price_dollars"))
    if bid is not None and ask is not None and ask >= bid:
        mid = (bid + ask) / 2.0
        if ask - bid >= 0.4:
            return last if last is not None else mid
        return mid
    return last


def _kalshi_probs(meeting: date, current_upper: float | None) -> dict | None:
    ticker = f"KXFED-{meeting:%y}{meeting.strftime('%b').upper()}"
    try:
        with httpx.Client(timeout=12.0, headers=HEADERS, follow_redirects=True) as client:
            res = client.get(
                "https://api.elections.kalshi.com/trade-api/v2/markets",
                params={"event_ticker": ticker, "limit": 40, "status": "open"},
            )
            res.raise_for_status()
            rows = (res.json() or {}).get("markets") or []
        ladder = []
        for row in rows:
            strike = _num(row.get("floor_strike"))
            yes = _kalshi_yes(row)
            if strike is None or yes is None:
                continue
            ladder.append((strike, _clip(yes, 0.0, 1.0)))
        if len(ladder) < 3 or current_upper is None:
            return None
        by_k = {k: v for k, v in ladder}

        def p_above(level: float) -> float:
            if level in by_k:
                return by_k[level]
            below = [k for k in by_k if k <= level]
            if not below:
                return 0.0
            return by_k[max(below)]

        hold_level = round(float(current_upper), 2)
        raw = {
            "hike25": max(0.0, p_above(hold_level) - p_above(hold_level + 0.25)),
            "hike50": p_above(hold_level + 0.25),
            "hold": max(0.0, p_above(hold_level - 0.25) - p_above(hold_level)),
            "cut25": max(0.0, p_above(hold_level - 0.50) - p_above(hold_level - 0.25)),
            "cut50": max(0.0, 1.0 - p_above(hold_level - 0.50)),
        }
        return _pack_probs(raw, source="Kalshi", note=f"利率上限阶梯 · {ticker}")
    except Exception:
        return None


def _zq_probs(meeting: date, current_rate: float | None) -> dict | None:
    if current_rate is None:
        return None
    code = ZQ_MONTH[meeting.month - 1]
    symbol = f"CBOT:ZQ{code}{meeting.year}"
    px = _tv_last(symbol)
    if px is None:
        px = _tv_last("CBOT:ZQ1!")
        symbol = "CBOT:ZQ1!"
    if px is None:
        return None
    implied = 100.0 - float(px) if float(px) > 50 else float(px)
    days = calendar.monthrange(meeting.year, meeting.month)[1]
    d_before = max(1, meeting.day - 1)
    d_after = max(1, days - d_before)
    r_after = (implied * days - float(current_rate) * d_before) / d_after
    shift = r_after - float(current_rate)
    hike = _clip(shift / STEP, 0.0, 1.0)
    cut = _clip(-shift / STEP, 0.0, 1.0)
    leftover = max(0.0, 1.0 - hike - cut)
    if hike >= 1.0:
        raw = {"hike25": 0.65, "hike50": 0.35, "hold": 0.0, "cut25": 0.0, "cut50": 0.0}
    elif cut >= 1.0:
        raw = {"cut25": 0.65, "cut50": 0.35, "hold": 0.0, "hike25": 0.0, "hike50": 0.0}
    else:
        raw = {
            "hike25": hike * 0.9,
            "hike50": hike * 0.1,
            "cut25": cut * 0.9,
            "cut50": cut * 0.1,
            "hold": leftover,
        }
    packed = _pack_probs(
        raw,
        source="ZQ 期货",
        note=f"{symbol} 报价 {px:.3f} → 隐含 {implied:.3f}% ；会后 {r_after:.3f}%",
    )
    packed["zq_price"] = _round(px, 4)
    packed["implied_rate"] = _round(implied, 3)
    packed["post_meeting_rate"] = _round(r_after, 3)
    packed["d_before"] = d_before
    packed["d_after"] = d_after
    return packed


def _model_probs(
    *,
    pi: float | None,
    pi_source: str | None = None,
    unemployment: float | None,
    current_rate: float | None,
    credit_risk: float | None,
    nfci: float | None,
    wage_yoy: float | None,
    pmi: float | None,
    inflation_risk: float | None,
    growth_regime: str | None,
) -> dict:
    pi_used = float(pi) if pi is not None else PI_STAR
    u = float(unemployment) if unemployment is not None else U_STAR
    i = float(current_rate) if current_rate is not None else None
    i_star = R_STAR + pi_used + 0.5 * (pi_used - PI_STAR) + 1.0 * (U_STAR - u)
    if wage_yoy is not None and wage_yoy >= 4.0:
        i_star += 0.15
    if pmi is not None:
        i_star += _clip((pmi - 50.0) * 0.03, -0.20, 0.20)
    if nfci is not None and nfci > 0:
        i_star -= min(0.40, nfci * 0.6)
    if credit_risk is not None and credit_risk >= 60:
        i_star -= min(0.35, (credit_risk - 60) / 80.0)
    if inflation_risk is not None and inflation_risk >= 70:
        i_star += 0.15
    if growth_regime == "recession":
        i_star -= 0.25
    elif growth_regime == "overheating":
        i_star += 0.20

    gap = 0.0 if i is None else (i_star - i)
    infl_gap = pi_used - PI_STAR
    emp_heat = U_STAR - u
    fci_tight = 0.0
    if credit_risk is not None:
        fci_tight += (credit_risk - 40.0) / 30.0
    if nfci is not None:
        fci_tight += (nfci + 0.4) / 0.35
    pressure = 0.50 * infl_gap + 0.30 * emp_heat - 0.20 * fci_tight
    gap_eff = 0.70 * gap + 0.30 * pressure
    # 下次会议不会一次性补上全部政策缺口；按约 40% 的缺口、单次最多 50bp 映射概率
    pace = _clip(0.40 * gap_eff, -0.50, 0.50)

    logits = {
        "cut50": 1.8 * ((-pace) - 0.38),
        "cut25": 2.4 * ((-pace) - 0.10),
        "hold": 2.35,
        "hike25": 2.4 * (pace - 0.10),
        "hike50": 1.8 * (pace - 0.38),
    }
    packed = _pack_probs(
        _softmax(logits),
        source="泰勒规则",
        note=(
            f"i*={i_star:.2f}% · r*={R_STAR:.2f} + {pi_source or 'π'}={pi_used:.2f} + 0.5(π-2) + (u*-u)；"
            "下次会议只按缺口约 40% 映射，不会一次性加满。"
        ),
    )
    packed["i_star"] = _round(i_star, 3)
    packed["gap_bp"] = _round(gap * 100, 1)
    packed["pressure"] = _round(pressure, 3)
    packed["pi"] = _round(pi_used, 3)
    packed["pi_source"] = pi_source or "通胀"
    packed["unemployment"] = _round(u, 3)
    packed["pace_bp"] = _round(pace * 100, 1)
    packed["r_star"] = R_STAR
    packed["pi_star"] = PI_STAR
    packed["u_star"] = U_STAR
    packed["formula"] = "i* = r* + π + 0.5(π-π*) + (u*-u)"
    return packed


def _divergence(market: dict | None, model: dict | None) -> dict:
    if not market or not model:
        return {
            "key": "na",
            "label": "缺少对照",
            "detail": "市场或模型一侧暂缺，无法比较。",
            "spread_pp": None,
        }
    spread = float(model.get("net") or 0) - float(market.get("net") or 0)
    abs_s = abs(spread)
    if abs_s < 12:
        return {
            "key": "agree",
            "label": "一致",
            "emoji": "🟢",
            "detail": f"模型与市场净倾向差 {spread:+.0f}pp，基本同向。",
            "spread_pp": _round(spread, 1),
        }
    if spread > 0:
        return {
            "key": "model_hawkish",
            "label": "模型更偏鹰",
            "emoji": "🟠",
            "detail": (
                f"泰勒规则比市场更偏加息 {spread:.0f}pp。"
                "要么市场在低估通胀/过热，要么模型权重偏鹰，值得复核。"
            ),
            "spread_pp": _round(spread, 1),
        }
    return {
        "key": "model_dovish",
        "label": "模型更偏鸽",
        "emoji": "🟠",
        "detail": (
            f"泰勒规则比市场更偏降息 {abs_s:.0f}pp。"
            "要么市场在定价金融条件/前瞻指引，要么模型低估就业降温。"
        ),
        "spread_pp": _round(spread, 1),
    }


def build_rate_call(
    *,
    pi: float | None,
    pi_source: str | None = None,
    unemployment: float | None,
    credit_risk: float | None,
    nfci: float | None,
    wage_yoy: float | None,
    pmi: float | None,
    inflation_risk: float | None,
    growth_regime: str | None,
    ust2: float | None = None,
) -> dict:
    meeting = next_fomc()
    meeting_out = None
    if meeting:
        meeting_out = dict(meeting)
        dec = meeting["decision"]
        meeting_out["decision"] = dec.isoformat() if hasattr(dec, "isoformat") else dec
    dff = _tv_last("DFF")
    target_upper = _tv_last("DFEDTARU")
    target_lower = _tv_last("DFEDTARL")
    current = dff if dff is not None else (
        (target_upper + target_lower) / 2.0 if target_upper is not None and target_lower is not None else None
    )

    poly = _polymarket_probs(meeting["decision"]) if meeting else None
    kalshi = _kalshi_probs(meeting["decision"], target_upper) if meeting else None
    zq = _zq_probs(meeting["decision"], current) if meeting else None
    market = poly or kalshi or zq
    model = _model_probs(
        pi=pi,
        pi_source=pi_source,
        unemployment=unemployment,
        current_rate=current,
        credit_risk=credit_risk,
        nfci=nfci,
        wage_yoy=wage_yoy,
        pmi=pmi,
        inflation_risk=inflation_risk,
        growth_regime=growth_regime,
    )
    dots = _sep_dots(current)
    div = _divergence(market, model)

    path_note = None
    if current is not None and ust2 is not None:
        path_bp = (float(ust2) - float(current)) * 100
        path_note = f"2Y {ust2:.2f}% vs 联邦基金 {current:.2f}%（路径 {path_bp:+.0f}bp，含期限溢价）"

    headline = "维持"
    if market:
        headline = market.get("call") or headline
    elif model:
        headline = model.get("call") or headline

    return {
        "ok": True,
        "meeting": meeting_out,
        "current_rate": _round(current, 3),
        "target_upper": _round(target_upper, 3),
        "target_lower": _round(target_lower, 3),
        "market": market,
        "dots": dots,
        "model": model,
        "kalshi": kalshi,
        "polymarket": poly,
        "futures": zq,
        "divergence": div,
        "headline": headline,
        "path_note": path_note,
        "note": (
            "市场隐含=下次会议的交易员定价（Polymarket / Kalshi / ZQ）；"
            "点阵图=委员会对今年年底适当利率的点（6月 SEP），不是对下次会议的投票；"
            "模型=泰勒规则。背离本身是信号，不是交易指令。"
        ),
    }
