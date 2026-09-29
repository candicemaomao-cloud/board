"""宏观因素宏观风险指数（Fed Macro Risk Engine v3）。

五大模块：通胀 22% · 就业/经济 23% · 美债/利率 25% · 外汇/日元 20% · 信用/流动性 10%。
日元不是普通汇率子项：JPY Carry Unwind 独立评分；日元升值本身 ≠ 利空。
FinalRisk = clip(BaseRisk + ResonancePenalty, 0, 100)。
"""

from __future__ import annotations

import copy
import json
import math
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from app.services.macro import (
    NFP_HEADLINE,
    TREASURY_CATALOG,
    _bls_series_map,
    _now,
    _pack,
    build_group,
)

CACHE_DIR = Path("data/macro")
CONSENSUS_PATH = CACHE_DIR / "consensus_overrides.json"
CACHE_NAME = "fed_risk_v6"
RAW_CACHE = "fed_risk_v5_raw"

# 五大模块权重
PILLAR_W = {
    "inflation": 0.22,
    "growth": 0.23,
    "treasury": 0.25,
    "fx": 0.20,
    "credit": 0.10,
}

# 叶子权重（占总分，合计 1.00）
LEAF_W = {
    "cpi": 0.08,
    "core_cpi": 0.04,
    "core_pce": 0.06,
    "ppi": 0.02,
    "oil": 0.02,  # 从 ppi 里匀出一半权重给石油，通胀模块(22%)总权重不变
    "nfp": 0.10,
    "unemployment": 0.05,
    "wage": 0.03,
    "pmi": 0.05,
    "ust10": 0.09,
    "ust2": 0.06,
    "real10": 0.05,
    "spread_2s10s": 0.02,
    "yield_vol": 0.03,
    "dxy": 0.05,
    "usdjpy": 0.06,
    "jpy_carry": 0.06,
    "dollar_liq": 0.03,
    "hy": 0.06,
    "fci": 0.04,
}

INNER = {
    "cpi": (0.20, 0.50, 0.30),
    "pce": (0.20, 0.50, 0.30),
    "ppi": (0.20, 0.40, 0.40),
    "nfp": (0.30, 0.40, 0.30),
    "pmi": (0.25, 0.35, 0.40),
    "rates": (0.25, 0.40, 0.35),
    "fx": (0.25, 0.40, 0.35),
}

FRED_TV = {
    "real10": "DFII10",
    "hy": "HYOAS",
    "nfci": "NFCI",
    "jp10": "JP10Y",
    "mort30": "MORTGAGE30US",
    "home": "SPCS20RSA",
    "delinq": "DRSFRMACBS",
    "tdsp": "TDSP",
    "cre_delinq": "DRCRELEXFACBS",
    "ig": "IGOAS",
}

MARKET_SYMBOLS = ("DXY", "USDJPY", "VIX", "QQQ", "SPY", "HYG", "KRE", "CL=F")
JP10_FALLBACK = 0.80


class FedRiskError(Exception):
    pass


def _round(x: float | None, n: int = 2) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), n)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _z_star(z: float | None) -> float:
    if z is None:
        return 0.0
    return _clip(abs(float(z)), 0.0, 3.0)


def _z_to_score(z: float | None) -> float:
    return 33.333333 * _z_star(z)


def _safe_std(xs: list[float], floor: float = 1e-6) -> float:
    clean = [float(x) for x in xs if x is not None and not math.isnan(float(x))]
    if len(clean) < 3:
        return max(floor, 1.0)
    return max(floor, float(pstdev(clean)))


def _load_consensus() -> dict:
    if not CONSENSUS_PATH.exists():
        return {}
    try:
        return json.loads(CONSENSUS_PATH.read_text()) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _series_by_id(payload: dict | None, sid: str) -> dict | None:
    for row in (payload or {}).get("series") or []:
        if row.get("id") == sid:
            return row
    return None


def _find_named(payload: dict | None, *needles: str) -> dict | None:
    rows = (payload or {}).get("series") or []
    for needle in needles:
        for row in rows:
            name = row.get("name") or ""
            if needle in name:
                return row
    return None


def _metric_history(row: dict | None, field: str = "yoy") -> list[dict]:
    out = []
    for p in (row or {}).get("points") or []:
        v = p.get(field)
        if v is None and field != "value":
            continue
        if v is None:
            v = p.get("value")
        if v is None:
            continue
        out.append({"date": p.get("date"), "value": float(v)})
    return out


def _quasi_expected(history: list[float], lookback: int = 6) -> float | None:
    if len(history) < 2:
        return history[-1] if history else None
    prior = history[-(lookback + 1) : -1]
    if not prior:
        prior = history[:-1]
    return float(mean(prior))


def _level_cpi_like(yoy: float | None) -> float:
    if yoy is None:
        return 50.0
    y = float(yoy)
    if y <= 2.5:
        return 20.0
    if y <= 3.0:
        return 50.0
    if y <= 3.5:
        return 75.0
    return 100.0


def _level_pmi(pmi: float | None) -> float:
    if pmi is None:
        return 50.0
    x = float(pmi)
    if x >= 53:
        return 15.0
    if x >= 51:
        return 25.0
    if x >= 50:
        return 35.0
    if x >= 49:
        return 55.0
    if x >= 47:
        return 75.0
    if x >= 45:
        return 90.0
    return 100.0


def _level_nfp_mom(mom_k: float | None) -> float:
    if mom_k is None:
        return 50.0
    m = float(mom_k)
    if 100 <= m <= 250:
        return 25.0
    if 50 <= m < 100 or 250 < m <= 350:
        return 45.0
    if 0 <= m < 50 or 350 < m <= 450:
        return 65.0
    if -100 <= m < 0 or m > 450:
        return 80.0
    return 95.0


def _level_yield(y: float | None) -> float:
    if y is None:
        return 50.0
    x = float(y)
    if x <= 2.0:
        return 20.0
    if x <= 3.0:
        return 35.0
    if x <= 4.0:
        return 50.0
    if x <= 5.0:
        return 70.0
    if x <= 6.0:
        return 85.0
    return 95.0


def _level_real_yield(y: float | None) -> float:
    if y is None:
        return 50.0
    x = float(y)
    if x <= 0:
        return 22.0
    if x <= 1.0:
        return 40.0
    if x <= 1.5:
        return 55.0
    if x <= 2.0:
        return 70.0
    if x <= 2.5:
        return 85.0
    return 95.0


def _level_dxy(x: float | None) -> float:
    if x is None:
        return 50.0
    v = float(x)
    if v <= 95:
        return 18.0
    if v <= 100:
        return 35.0
    if v <= 104:
        return 55.0
    if v <= 108:
        return 75.0
    return 90.0


def _level_usdjpy(x: float | None) -> float:
    """水平本身不是风险；极端高位意味着 Carry 堆积，极端低位表示已大幅平仓。"""
    if x is None:
        return 50.0
    v = float(x)
    if v >= 160:
        return 70.0
    if v >= 155:
        return 58.0
    if v >= 145:
        return 42.0
    if v >= 135:
        return 38.0
    return 50.0


def _level_hy_oas(x: float | None) -> float:
    """HY OAS：FRED/TV 多为百分数（3.5=350bp）。"""
    if x is None:
        return 50.0
    v = float(x)
    if v > 30:
        v = v / 100.0
    if v < 3.0:
        return 18.0
    if v < 3.5:
        return 32.0
    if v < 4.0:
        return 48.0
    if v < 5.0:
        return 68.0
    if v < 6.0:
        return 82.0
    return 95.0


def _level_nfci(x: float | None) -> float:
    if x is None:
        return 50.0
    v = float(x)
    if v <= -0.6:
        return 15.0
    if v <= -0.3:
        return 28.0
    if v <= 0.0:
        return 45.0
    if v <= 0.3:
        return 68.0
    if v <= 0.7:
        return 85.0
    return 96.0


def _downsample_weekly(values: list[float], step: int = 5) -> list[float]:
    if len(values) <= step:
        return values
    sampled = values[::step]
    if sampled[-1] != values[-1]:
        sampled = sampled + [values[-1]]
    return sampled


def _compute_l_s_t(
    history: list[float],
    *,
    level_fn,
    expected_override: float | None = None,
    inner_w: tuple[float, float, float] = (0.2, 0.5, 0.3),
) -> dict:
    if len(history) < 3:
        raise FedRiskError("历史样本不足")
    actual = float(history[-1])
    previous = float(history[-2])
    expected = float(expected_override) if expected_override is not None else _quasi_expected(history)
    if expected is None:
        expected = previous

    surprises = []
    trends = []
    for i in range(2, len(history)):
        a = history[i]
        p = history[i - 1]
        e = _quasi_expected(history[: i + 1])
        if e is None:
            e = p
        surprises.append(a - e)
        trends.append(a - p)

    sig_s = _safe_std(surprises)
    sig_t = _safe_std(trends)
    z_s = (actual - expected) / sig_s
    z_t = (actual - previous) / sig_t
    s_score = _z_to_score(z_s)
    t_score = _z_to_score(z_t)
    l_score = float(level_fn(actual))
    wl, ws, wt = inner_w
    risk = _clip(wl * l_score + ws * s_score + wt * t_score, 0.0, 100.0)
    return {
        "actual": _round(actual, 4),
        "expected": _round(expected, 4),
        "previous": _round(previous, 4),
        "surprise": _round(actual - expected, 4),
        "trend_delta": _round(actual - previous, 4),
        "z_surprise": _round(z_s, 3),
        "z_trend": _round(z_t, 3),
        "sigma_surprise": _round(sig_s, 4),
        "sigma_trend": _round(sig_t, 4),
        "level_score": _round(l_score, 1),
        "surprise_score": _round(s_score, 1),
        "trend_score": _round(t_score, 1),
        "risk": _round(risk, 1),
        "expected_source": "override" if expected_override is not None else "trailing_mean",
    }


def _blend_risk(parts: list[tuple[float, float | None]]) -> float:
    num = sum(w * r for w, r in parts if r is not None)
    den = sum(w for w, r in parts if r is not None) or 1.0
    return _clip(num / den, 0.0, 100.0)


def _leaf(lid: str, label: str, body: dict | None, weight: float, extra: dict | None = None) -> dict | None:
    if not body or body.get("risk") is None:
        return None
    out = {
        "id": lid,
        "label": label,
        "weight": weight,
        "risk": body.get("risk"),
        "detail": body,
    }
    if extra:
        out.update(extra)
    return out


def _try_lst(history: list[float], **kwargs) -> dict | None:
    if len(history) < 3:
        return None
    try:
        return _compute_l_s_t(history, **kwargs)
    except FedRiskError:
        return None


def _pct_n(xs: list[float], n: int) -> float | None:
    if len(xs) <= n or xs[-1 - n] in (None, 0):
        return None
    return (float(xs[-1]) / float(xs[-1 - n]) - 1.0) * 100.0


def _hv20(closes: list[float]) -> float | None:
    if len(closes) < 22:
        return None
    rets = []
    for i in range(len(closes) - 20, len(closes)):
        prev = closes[i - 1]
        if prev in (None, 0):
            continue
        rets.append(math.log(float(closes[i]) / float(prev)))
    if len(rets) < 10:
        return None
    return float(pstdev(rets)) * math.sqrt(252) * 100.0


def _rolling_hv20(closes: list[float]) -> list[float]:
    out = []
    for end in range(21, len(closes) + 1):
        hv = _hv20(closes[:end])
        if hv is not None:
            out.append(hv)
    return out


def _percentile_rank(xs: list[float], x: float) -> float:
    if not xs:
        return 50.0
    n = sum(1 for v in xs if v <= x)
    return 100.0 * n / len(xs)


def _to_bps(x: float | None) -> float | None:
    if x is None:
        return None
    v = float(x)
    return v * 100.0 if abs(v) < 30 else v


def _map_linear(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def _jpy_momentum_score(pct_5d: float | None) -> float:
    """USD/JPY 5 日涨跌：负数=日元升值。0/+ → 低风险；-3% → 高风险。"""
    if pct_5d is None:
        return 50.0
    x = float(pct_5d)
    if x >= 0:
        return 20.0
    if x >= -1:
        return _map_linear(x, 0, 20, -1, 40)
    if x >= -2:
        return _map_linear(x, -1, 40, -2, 65)
    if x >= -3:
        return _map_linear(x, -2, 65, -3, 85)
    if x >= -4:
        return _map_linear(x, -3, 85, -4, 100)
    return 100.0


def _band(score: float) -> dict:
    s = float(score)
    if s < 20:
        return {"key": "very_low", "label": "极低", "emoji": "🟢", "action": "正常风险"}
    if s < 40:
        return {"key": "low", "label": "低", "emoji": "🟢", "action": "正常"}
    if s < 60:
        return {"key": "mid", "label": "中性", "emoji": "🟡", "action": "提高警惕"}
    if s < 80:
        return {"key": "high", "label": "高", "emoji": "🟠", "action": "降低仓位"}
    return {"key": "extreme", "label": "极高", "emoji": "🔴", "action": "强风险控制"}


def _dir_band(d: float) -> dict:
    x = float(d)
    if x <= -60:
        return {"key": "strong_bear", "label": "极度利空股票", "emoji": "🔴"}
    if x <= -20:
        return {"key": "bear", "label": "偏空股票", "emoji": "🟠"}
    if x < 20:
        return {"key": "neutral", "label": "中性", "emoji": "🟡"}
    if x < 60:
        return {"key": "bull", "label": "偏多股票", "emoji": "🟢"}
    return {"key": "strong_bull", "label": "极度利好股票", "emoji": "🟢"}


def _resolve_infl_series(payload: dict | None, head_ids: list[str], core_ids: list[str]):
    head = None
    core = None
    for sid in head_ids:
        head = _series_by_id(payload, sid)
        if head:
            break
    for sid in core_ids:
        core = _series_by_id(payload, sid)
        if core:
            break
    if not head:
        head = _find_named(payload, "总 CPI", "总 PCE", "总 PPI", "总")
    if not core:
        core = _find_named(payload, "核心 CPI", "核心 PCE", "核心 PPI", "核心")
    return head, core


def _yoy_leaf_from_row(
    row: dict | None,
    *,
    lid: str,
    label: str,
    inner_w: tuple[float, float, float],
    consensus: dict,
    consensus_key: str,
) -> dict | None:
    hist = [x["value"] for x in _metric_history(row, "yoy")]
    body = _try_lst(
        hist,
        level_fn=_level_cpi_like,
        expected_override=consensus.get(consensus_key),
        inner_w=inner_w,
    )
    if not body:
        return None
    body["series"] = (row or {}).get("name") or label
    body["as_of"] = (_metric_history(row, "yoy") or [{}])[-1].get("date")
    body["unit"] = "% YoY"
    return _leaf(lid, label, body, LEAF_W[lid])


def _pressure_z(body: dict | None) -> float:
    if not body:
        return 0.0
    s = body.get("surprise")
    sig = body.get("sigma_surprise") or 0.2
    if s is None:
        return 0.0
    return _clip(float(s) / max(0.05, float(sig)), -3, 3)


def _pmi_leaf(pmi_payload: dict | None, consensus: dict) -> dict | None:
    man = None
    for row in (pmi_payload or {}).get("series") or []:
        if row.get("kind") == "headline" and "制造" in (row.get("name") or ""):
            man = row
            break
    if man is None:
        for row in (pmi_payload or {}).get("series") or []:
            if row.get("kind") == "headline":
                man = row
                break
    hist = [x["value"] for x in _metric_history(man, "value")]
    body = _try_lst(
        hist,
        level_fn=_level_pmi,
        expected_override=consensus.get("pmi"),
        inner_w=INNER["pmi"],
    )
    if not body:
        return None
    body["series"] = (man or {}).get("name") or "制造业 PMI"
    body["as_of"] = (_metric_history(man, "value") or [{}])[-1].get("date")
    body["unit"] = "指数"
    leaf = _leaf("pmi", "PMI", body, LEAF_W["pmi"], extra={"growth_z": body.get("z_trend"), "level": body.get("actual")})
    return leaf


def _fetch_labor_extras() -> dict[str, list[dict]]:
    try:
        return _bls_series_map(["LNS14000000", "CES0500000003"])
    except Exception:
        return {}


def _ohlc_to_points(ohlc: dict) -> list[dict]:
    from datetime import datetime, timezone

    out = []
    for bar in ohlc.get("ohlc_bars") or []:
        ts = bar.get("ts")
        close = bar.get("close")
        if ts is None or close is None:
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        out.append({"date": day, "value": float(close)})
    if out:
        return out
    for v in ohlc.get("closes") or []:
        if v is None:
            continue
        out.append({"date": None, "value": float(v)})
    return out


def _fetch_symbol_ohlc(sym: str) -> dict:
    from app.services.ohlc import fetch_closes

    try:
        ohlc = fetch_closes(sym, "1d", apply_live=True)
        closes = [float(x) for x in (ohlc.get("closes") or []) if x is not None]
        return {
            "closes": closes,
            "price": ohlc.get("price"),
            "ok": bool(closes),
            "points": _ohlc_to_points(ohlc),
        }
    except Exception as exc:  # noqa: BLE001
        return {"closes": [], "price": None, "ok": False, "error": str(exc), "points": []}


def _fetch_with_retry(sym: str) -> dict:
    first = _fetch_symbol_ohlc(sym)
    if first.get("ok"):
        return first
    second = _fetch_symbol_ohlc(sym)
    return second if second.get("ok") else first


def _fetch_fred_bundle() -> dict[str, dict]:
    """实际利率 / HY OAS / NFCI / 日本10Y：走 TradingView 的 FRED/TVC 代码。串行拉取，避免 TV websocket 并发打架。"""
    out: dict[str, dict] = {}
    for key, sym in FRED_TV.items():
        payload = _fetch_with_retry(sym)
        payload["id"] = sym
        out[key] = payload
    return out


def _fetch_market_bundle() -> dict[str, dict]:
    return {sym: _fetch_with_retry(sym) for sym in MARKET_SYMBOLS}


def _ensure_treasury_row(
    treasury_payload: dict | None, sid: str, name: str, cutoff: str | None = None
) -> dict | None:
    row = _series_by_id(treasury_payload, sid)
    if row and _metric_history(row, "value"):
        return row
    from app.services.macro import _fetch_one_safe, _points

    _sid, doc = _fetch_one_safe("FED", "H15", sid)
    pts = _points(doc, start="1990-01-01") if doc else []
    if cutoff:
        pts = _slice_points(pts, cutoff)
    if not pts:
        return None
    packed = _pack(sid, name, pts, extra={"featured": True, "item": sid, "kind": "yield"}, pct=False)
    return packed


def _values(row: dict | None) -> list[float]:
    return [x["value"] for x in _metric_history(row, "value")]


def _employment_regime(
    *,
    nfp_mom: float | None,
    nfp_surprise: float | None,
    ur_trend: float | None,
    pmi: float | None,
    cpi_risk: float,
) -> tuple[str, float]:
    infl_hot = cpi_risk >= 55
    pmi_exp = pmi is not None and pmi >= 50
    pmi_con = pmi is not None and pmi < 50
    strong_jobs = nfp_mom is not None and nfp_mom >= 180
    weak_jobs = nfp_mom is not None and nfp_mom <= 50
    ur_up = ur_trend is not None and ur_trend > 0.05

    if infl_hot and pmi_exp and strong_jobs:
        z = float(nfp_surprise or 0.0)
        return "overheating", _clip(-20 - 15 * _clip(z / 80.0, -3, 3), -100, 100)
    if (not infl_hot) and pmi_exp and (nfp_mom is not None and 50 <= nfp_mom <= 220):
        if weak_jobs and not ur_up:
            return "soft_landing", 25.0
        if strong_jobs:
            return "soft_landing", -15.0
        return "soft_landing", 10.0
    if pmi_con and weak_jobs and (ur_up or (nfp_mom is not None and nfp_mom < 0)):
        extra = 0 if nfp_mom is None else max(0.0, (80 - nfp_mom) / 5)
        return "recession", _clip(-30 - extra, -100, 40)
    direction = 0.0
    if nfp_surprise is not None and infl_hot:
        direction = -10 if nfp_surprise > 0 else 5
    return "neutral", direction


def _growth_leaves(nfp_payload, labor, pmi_payload, consensus, cpi_risk: float) -> tuple[list[dict], dict]:
    head = _series_by_id(nfp_payload, NFP_HEADLINE)
    mom_hist = [x["value"] for x in _metric_history(head, "mom")]
    if len(mom_hist) < 6:
        levels = [x["value"] for x in _metric_history(head, "value")]
        mom_hist = [levels[i] - levels[i - 1] for i in range(1, len(levels))]
    nfp_body = _try_lst(
        mom_hist,
        level_fn=_level_nfp_mom,
        expected_override=consensus.get("nfp"),
        inner_w=INNER["nfp"],
    )
    leaves: list[dict] = []
    if nfp_body:
        nfp_body["series"] = (head or {}).get("name") or "非农总计"
        nfp_body["unit"] = "千人（月增）"
        nfp_body["as_of"] = ((head or {}).get("latest") or {}).get("date")
        leaf = _leaf("nfp", "NFP", nfp_body, LEAF_W["nfp"])
        if leaf:
            leaves.append(leaf)

    ur_pts = (labor or {}).get("LNS14000000") or []
    wage_pts = (labor or {}).get("CES0500000003") or []
    ur_pack = _pack("LNS14000000", "失业率", ur_pts, pct=False) if ur_pts else None
    wage_pack = _pack("CES0500000003", "平均时薪", wage_pts, pct=True) if wage_pts else None
    ur_body = _try_lst(
        [x["value"] for x in _metric_history(ur_pack, "value")],
        level_fn=lambda u: (
            20 if u is not None and u < 4.0 else
            (50 if u is not None and u < 4.5 else
             (75 if u is not None and u < 5.0 else 95))
        ),
        expected_override=consensus.get("unemployment"),
        inner_w=(0.25, 0.45, 0.30),
    )
    if ur_body:
        ur_body["series"] = "失业率"
        ur_body["unit"] = "%"
        leaf = _leaf("unemployment", "失业率", ur_body, LEAF_W["unemployment"])
        if leaf:
            leaves.append(leaf)
    wage_body = _try_lst(
        [x["value"] for x in _metric_history(wage_pack, "yoy")],
        level_fn=_level_cpi_like,
        expected_override=consensus.get("wage"),
        inner_w=(0.2, 0.5, 0.3),
    )
    if wage_body:
        wage_body["series"] = "平均时薪同比"
        wage_body["unit"] = "% YoY"
        leaf = _leaf("wage", "平均时薪", wage_body, LEAF_W["wage"])
        if leaf:
            leaves.append(leaf)

    pmi_leaf = _pmi_leaf(pmi_payload, consensus)
    if pmi_leaf:
        leaves.append(pmi_leaf)

    pmi_level = ((pmi_leaf or {}).get("detail") or {}).get("actual")
    regime, direction_nfp = _employment_regime(
        nfp_mom=(nfp_body or {}).get("actual"),
        nfp_surprise=(nfp_body or {}).get("surprise"),
        ur_trend=(ur_body or {}).get("trend_delta") if ur_body else None,
        pmi=pmi_level,
        cpi_risk=cpi_risk,
    )
    meta = {
        "regime": regime,
        "equity_direction_hint": direction_nfp,
        "pmi_level": pmi_level,
        "nfp_mom": (nfp_body or {}).get("actual"),
        "wage_hot": bool(wage_body and (wage_body.get("actual") or 0) >= 4.0),
        "unemployment": (ur_body or {}).get("actual") if ur_body else None,
        "wage_yoy": (wage_body or {}).get("actual") if wage_body else None,
        "ur_up": bool(ur_body and (ur_body.get("trend_delta") or 0) > 0.05),
    }
    return leaves, meta


def _yield_vol_score(daily: list[float]) -> dict | None:
    if len(daily) < 22:
        return None
    chg5 = (daily[-1] - daily[-6]) * 100  # bp
    diffs = [daily[i] - daily[i - 1] for i in range(len(daily) - 20, len(daily))]
    vol_bp = float(pstdev(diffs)) * 100
    speed = abs(chg5)
    if speed < 10:
        s1 = 20.0
    elif speed < 20:
        s1 = _map_linear(speed, 10, 20, 20, 45)
    elif speed < 30:
        s1 = _map_linear(speed, 20, 45, 30, 70)
    elif speed < 50:
        s1 = _map_linear(speed, 30, 70, 50, 90)
    else:
        s1 = 95.0
    if vol_bp < 3:
        s2 = 20.0
    elif vol_bp < 6:
        s2 = _map_linear(vol_bp, 3, 20, 6, 50)
    elif vol_bp < 10:
        s2 = _map_linear(vol_bp, 6, 50, 10, 80)
    else:
        s2 = 92.0
    risk = 0.6 * s1 + 0.4 * s2
    if chg5 < 0:
        risk *= 0.72
    return {
        "actual": _round(chg5, 2),
        "previous": _round((daily[-6] - daily[-11]) * 100 if len(daily) >= 12 else None, 2),
        "expected": 0.0,
        "unit": "bp / 5d",
        "series": "10Y 变化速度 / 波动",
        "vol_bp_daily": _round(vol_bp, 2),
        "chg5_bp": _round(chg5, 2),
        "risk": _round(_clip(risk, 0, 100), 1),
        "note": "上行加速权重大于同等幅度下行。",
    }


def _spread_2s10s_score(ust2: list[float], ust10: list[float]) -> dict | None:
    n = min(len(ust2), len(ust10))
    if n < 8:
        return None
    a2, a10 = ust2[-n:], ust10[-n:]
    spread = [a10[i] - a2[i] for i in range(n)]
    last = spread[-1]
    prev = spread[-2]
    chg20 = spread[-1] - spread[-21] if n > 21 else spread[-1] - spread[0]
    if last < 0:
        lv = 88.0
    elif last < 0.20:
        lv = 72.0
    elif last < 0.50:
        lv = 52.0
    elif last < 1.00:
        lv = 32.0
    else:
        lv = 18.0
    tr = _clip(50 - chg20 / 0.25 * 25, 10, 95)
    risk = 0.6 * lv + 0.4 * tr
    return {
        "actual": _round(last, 4),
        "previous": _round(prev, 4),
        "expected": 0.0,
        "trend_delta": _round(last - prev, 4),
        "unit": "pp",
        "series": "2s10s 期限利差",
        "chg20": _round(chg20, 4),
        "risk": _round(_clip(risk, 0, 100), 1),
        "note": "倒挂或快速走平抬升风险。",
    }


def _treasury_leaves(treasury_payload, fred, consensus, cutoff: str | None = None) -> tuple[list[dict], dict]:
    name_by_id = {sid: name for sid, name, _f in TREASURY_CATALOG}
    row10 = _ensure_treasury_row(
        treasury_payload, "RIFLGFCY10_N.B", name_by_id.get("RIFLGFCY10_N.B") or "10年期美债", cutoff
    )
    row2 = _ensure_treasury_row(
        treasury_payload, "RIFLGFCY02_N.B", name_by_id.get("RIFLGFCY02_N.B") or "2年期美债", cutoff
    )
    daily10 = _values(row10)
    daily2 = _values(row2)
    leaves: list[dict] = []
    pressure: list[float] = []

    def _tenor(row, lid, label, key):
        hist = _downsample_weekly(_values(row))
        body = _try_lst(
            hist,
            level_fn=_level_yield,
            expected_override=consensus.get(key) or consensus.get((row or {}).get("id") or ""),
            inner_w=INNER["rates"],
        )
        if not body:
            return
        body["series"] = (row or {}).get("name") or label
        body["as_of"] = (_metric_history(row, "value") or [{}])[-1].get("date")
        body["unit"] = "%"
        leaf = _leaf(lid, label, body, LEAF_W[lid])
        if leaf:
            leaves.append(leaf)
        zs, zt = body.get("z_surprise"), body.get("z_trend")
        if zs is not None:
            pressure.append(float(zs))
        if zt is not None:
            pressure.append(float(zt) * 0.8)

    _tenor(row10, "ust10", "10Y Treasury", "ust10")
    _tenor(row2, "ust2", "2Y Treasury", "ust2")

    real_pts = ((fred or {}).get("real10") or {}).get("points") or []
    real_hist = _downsample_weekly([p["value"] for p in real_pts])
    real_body = _try_lst(
        real_hist,
        level_fn=_level_real_yield,
        expected_override=consensus.get("real10"),
        inner_w=INNER["rates"],
    )
    if real_body:
        # 成长股对实际利率的【水平】更敏感，不能只看周度 Surprise
        lvl = float(real_body.get("level_score") or 50)
        real_body["risk"] = _round(_clip(0.62 * lvl + 0.38 * float(real_body["risk"]), 0, 100), 1)
        real_body["series"] = "10Y 实际利率 (TIPS)"
        real_body["unit"] = "%"
        real_body["as_of"] = (real_pts[-1] or {}).get("date") if real_pts else None
        leaf = _leaf("real10", "10Y Real Yield", real_body, LEAF_W["real10"])
        if leaf:
            leaves.append(leaf)
        if real_body.get("z_trend") is not None:
            pressure.append(float(real_body["z_trend"]))

    spr = _spread_2s10s_score(daily2, daily10)
    if spr:
        leaf = _leaf("spread_2s10s", "2Y–10Y Spread", spr, LEAF_W["spread_2s10s"])
        if leaf:
            leaves.append(leaf)

    yv = _yield_vol_score(daily10)
    if yv:
        leaf = _leaf("yield_vol", "Yield Volatility", yv, LEAF_W["yield_vol"])
        if leaf:
            leaves.append(leaf)

    meta = {
        "ust10": daily10[-1] if daily10 else None,
        "ust2": daily2[-1] if daily2 else None,
        "real10": real_hist[-1] if real_hist else None,
        "ust10_chg20": (daily10[-1] - daily10[-21]) if len(daily10) > 21 else None,
        "rates_pressure_z": _round(_clip(mean(pressure) if pressure else 0.0, -3, 3), 3),
        "daily10": daily10,
        "daily2": daily2,
    }
    return leaves, meta


def _ret_score(pct: float | None, *, down_is_risk: bool = True) -> float:
    if pct is None:
        return 50.0
    x = float(pct)
    if not down_is_risk:
        x = -x
    if x >= 3:
        return 12.0
    if x >= 0:
        return _map_linear(x, 0, 40, 3, 12)
    if x >= -3:
        return _map_linear(x, 0, 40, -3, 75)
    if x >= -6:
        return _map_linear(x, -3, 75, -6, 95)
    return 98.0


def _vix_score(level: float | None, chg5: float | None) -> float:
    if level is None:
        return 50.0
    v = float(level)
    if v < 15:
        lv = 12.0
    elif v < 20:
        lv = _map_linear(v, 15, 20, 20, 48)
    elif v < 30:
        lv = _map_linear(v, 20, 48, 30, 82)
    else:
        lv = min(98.0, 82 + (v - 30) * 1.2)
    spike = 0.0
    if chg5 is not None and chg5 > 0:
        spike = _clip(chg5 / 20.0 * 25.0, 0, 30)
    return _clip(0.75 * lv + 0.25 * (48 + spike), 0, 100)


def _oil_score(chg5: float | None, chg20: float | None) -> float:
    """WTI 原油短期急涨是典型的输入型通胀信号——直接传导到 CPI/PPI 能源分项，
    这里只关心"涨得有多快"：温和涨跌给中性偏低分，短期/中期急涨给高分。
    大跌不额外加分（油价暴跌更多是需求塌方/衰退信号，不是通胀模块要捕捉的
    维度，别让它被系统误读成"通胀风险很低"）。5 日、20 日两个窗口各自打分，
    取较高的那个——只要有一个窗口触发了急涨，就该报警，不需要两个都触发。
    """
    if chg5 is None and chg20 is None:
        return 50.0
    score_5 = 20.0
    if chg5 is not None:
        if chg5 >= 15:
            score_5 = 100.0
        elif chg5 >= 8:
            score_5 = _map_linear(chg5, 8, 70, 15, 100)
        elif chg5 >= 3:
            score_5 = _map_linear(chg5, 3, 40, 8, 70)
        elif chg5 >= -3:
            score_5 = _map_linear(chg5, -3, 25, 3, 40)
        else:
            score_5 = 15.0
    score_20 = 20.0
    if chg20 is not None:
        if chg20 >= 25:
            score_20 = 95.0
        elif chg20 >= 12:
            score_20 = _map_linear(chg20, 12, 55, 25, 95)
        elif chg20 >= 0:
            score_20 = _map_linear(chg20, 0, 25, 12, 55)
        else:
            score_20 = 15.0
    return _clip(max(score_5, score_20), 0, 100)


def _fx_and_credit(mkt: dict, fred: dict, tsy_meta: dict, consensus: dict) -> tuple[list[dict], list[dict], dict]:
    dxy = (mkt or {}).get("DXY") or {}
    jpy = (mkt or {}).get("USDJPY") or {}
    vix = (mkt or {}).get("VIX") or {}
    qqq = (mkt or {}).get("QQQ") or {}
    spy = (mkt or {}).get("SPY") or {}
    hyg = (mkt or {}).get("HYG") or {}

    dxy_c = dxy.get("closes") or []
    jpy_c = jpy.get("closes") or []
    vix_c = vix.get("closes") or []
    qqq_c = qqq.get("closes") or []
    spy_c = spy.get("closes") or []
    hyg_c = hyg.get("closes") or []

    vix_lv = float(vix.get("price") or (vix_c[-1] if vix_c else 0) or 0) or None
    vix_5d = _pct_n(vix_c, 5)
    qqq_5d = _pct_n(qqq_c, 5)
    spy_5d = _pct_n(spy_c, 5)
    dxy_5d = _pct_n(dxy_c, 5)
    dxy_20d = _pct_n(dxy_c, 20)
    jpy_5d = _pct_n(jpy_c, 5)
    hyg_5d = _pct_n(hyg_c, 5)
    hyg_20d = _pct_n(hyg_c, 20)

    fx_leaves: list[dict] = []
    credit_leaves: list[dict] = []

    dxy_body = _try_lst(
        _downsample_weekly(dxy_c),
        level_fn=_level_dxy,
        expected_override=consensus.get("dxy"),
        inner_w=INNER["fx"],
    )
    dxy_risk_raw = float((dxy_body or {}).get("risk") or 50)
    # DXY 语境：与利率齐升=收紧；利率下行+股市上涨=避险美元，削弱风险
    ust10_up = (tsy_meta.get("ust10_chg20") or 0) > 0.05
    real10 = tsy_meta.get("real10")
    real_hot = real10 is not None and real10 >= 1.8
    eq_up = (spy_5d or 0) > 0 and (qqq_5d or 0) > 0
    dxy_up = (dxy_5d or 0) > 0.3
    dxy_context = "neutral"
    dxy_mult = 1.0
    if dxy_up and ust10_up and real_hot:
        dxy_context = "tightening"
        dxy_mult = 1.12
    elif dxy_up and not ust10_up and eq_up:
        dxy_context = "safe_haven"
        dxy_mult = 0.55
    if dxy_body:
        dxy_body["series"] = "美元指数 DXY"
        dxy_body["unit"] = "指数"
        dxy_body["chg5"] = _round(dxy_5d, 3)
        dxy_body["context"] = dxy_context
        dxy_body["risk"] = _round(_clip(dxy_risk_raw * dxy_mult, 0, 100), 1)
        leaf = _leaf("dxy", "DXY", dxy_body, LEAF_W["dxy"], extra={"context": dxy_context})
        if leaf:
            fx_leaves.append(leaf)

    jpy_body = _try_lst(
        _downsample_weekly(jpy_c),
        level_fn=_level_usdjpy,
        expected_override=consensus.get("usdjpy"),
        inner_w=INNER["fx"],
    )
    # USDJPY 叶子：动量（日元升值）权重大于水平
    mom = _jpy_momentum_score(jpy_5d)
    if jpy_body:
        blended = 0.35 * float(jpy_body["risk"]) + 0.65 * mom
        jpy_body["series"] = "USD/JPY"
        jpy_body["unit"] = "汇率"
        jpy_body["chg5"] = _round(jpy_5d, 3)
        jpy_body["momentum_score"] = _round(mom, 1)
        jpy_body["risk"] = _round(_clip(blended, 0, 100), 1)
        leaf = _leaf("usdjpy", "USD/JPY", jpy_body, LEAF_W["usdjpy"])
        if leaf:
            fx_leaves.append(leaf)

    hv = _hv20(jpy_c)
    hv_hist = _rolling_hv20(jpy_c)
    hv_pct = _percentile_rank(hv_hist, hv) if hv is not None else 50.0
    vol_score = _clip(hv_pct, 0, 100)

    jp10_pts = ((fred or {}).get("jp10") or {}).get("points") or []
    jp10 = jp10_pts[-1]["value"] if jp10_pts else JP10_FALLBACK
    us10 = tsy_meta.get("ust10")
    us2 = tsy_meta.get("ust2")
    spread_usjp = None
    spread_src = "fallback"
    if us2 is not None and jp10 is not None:
        spread_usjp = float(us2) - float(jp10)
        spread_src = "US2Y-JP10Y"
    if us10 is not None and jp10 is not None:
        spread_usjp = float(us10) - float(jp10)
        spread_src = "US10Y-JP10Y"
    # 利差收窄 → 风险升。近年美日 10Y 利差约 3–4pp
    if spread_usjp is None:
        spread_score = 50.0
    elif spread_usjp >= 4.0:
        spread_score = 22.0
    elif spread_usjp >= 3.0:
        spread_score = _map_linear(spread_usjp, 4.0, 22, 3.0, 45)
    elif spread_usjp >= 2.0:
        spread_score = _map_linear(spread_usjp, 3.0, 45, 2.0, 72)
    else:
        spread_score = 88.0
    if len(jp10_pts) > 21 and us10 is not None and len(tsy_meta.get("daily10") or []) > 21:
        old_jp = jp10_pts[-21]["value"]
        old_us = tsy_meta["daily10"][-21]
        chg = (us10 - jp10) - (old_us - old_jp)
        spread_score = _clip(0.55 * spread_score + 0.45 * _clip(50 - chg / 0.25 * 30, 10, 95), 0, 100)

    hy_pts = ((fred or {}).get("hy") or {}).get("points") or []
    hy_last = hy_pts[-1]["value"] if hy_pts else None
    hy_prev5 = hy_pts[-6]["value"] if len(hy_pts) > 6 else None
    hy_widen_bps = None
    if hy_last is not None and hy_prev5 is not None:
        a, b = _to_bps(hy_last), _to_bps(hy_prev5)
        if a is not None and b is not None:
            hy_widen_bps = a - b
    elif hyg_5d is not None:
        hy_widen_bps = -hyg_5d * 8  # HYG 跌 ≈ 利差走阔（bp 粗糙映射）

    stress = _blend_risk(
        [
            (0.30, _ret_score(qqq_5d)),
            (0.20, _ret_score(spy_5d)),
            (0.30, _vix_score(vix_lv, vix_5d)),
            (0.20, _clip(50 + (hy_widen_bps or 0) / 20.0 * 25, 10, 95) if hy_widen_bps is not None else 50.0),
        ]
    )

    carry_raw = _clip(0.35 * mom + 0.25 * vol_score + 0.20 * spread_score + 0.20 * stress, 0, 100)
    yen_up = (jpy_5d or 0) < -0.5
    risk_on = (vix_5d or 0) < 0 and (spy_5d or 0) > 0 and (qqq_5d or 0) > 0
    risk_off = (vix_lv or 0) >= 20 and (qqq_5d or 0) < 0
    carry_mult = 1.0
    carry_note = "日元升值本身不等于利空；需与风险资产压力同时出现才计 Carry Unwind。"
    if yen_up and risk_on:
        carry_mult = 0.45
        carry_note = "日元升值但 VIX 回落、股市上涨，更像正常汇率波动，Carry 风险下调。"
    elif yen_up and risk_off:
        carry_mult = 1.15
        carry_note = "日元升值同时风险资产承压，Carry Unwind 通道打开。"
    carry = _clip(carry_raw * carry_mult, 0, 100)

    carry_body = {
        "series": "JPY Carry / Unwind Risk",
        "actual": _round(jpy_c[-1] if jpy_c else None, 3),
        "unit": "USDJPY",
        "chg5": _round(jpy_5d, 3),
        "hv20": _round(hv, 2),
        "hv_percentile": _round(hv_pct, 1),
        "usjp_spread": _round(spread_usjp, 3),
        "spread_source": spread_src,
        "momentum_score": _round(mom, 1),
        "vol_score": _round(vol_score, 1),
        "spread_score": _round(spread_score, 1),
        "stress_score": _round(stress, 1),
        "raw": _round(carry_raw, 1),
        "multiplier": _round(carry_mult, 2),
        "risk": _round(carry, 1),
        "note": carry_note,
        "formula": "0.35×Momentum + 0.25×Vol + 0.20×RateSpread + 0.20×RiskAssetStress",
    }
    leaf = _leaf("jpy_carry", "JPY Carry", carry_body, LEAF_W["jpy_carry"])
    if leaf:
        fx_leaves.append(leaf)

    # 全球美元流动性：美元快速升值 + 短端利率上行 = 流动性收紧
    liq = 50.0
    if dxy_20d is not None:
        if dxy_20d >= 4:
            liq = 90.0
        elif dxy_20d >= 2:
            liq = _map_linear(dxy_20d, 2, 68, 4, 90)
        elif dxy_20d >= 0:
            liq = _map_linear(dxy_20d, 0, 42, 2, 68)
        elif dxy_20d >= -2:
            liq = _map_linear(dxy_20d, 0, 42, -2, 22)
        else:
            liq = 18.0
    if tsy_meta.get("ust2") is not None and len(tsy_meta.get("daily2") or []) > 21:
        d2 = tsy_meta["daily2"][-1] - tsy_meta["daily2"][-21]
        liq = _clip(0.7 * liq + 0.3 * _clip(50 + d2 / 0.25 * 25, 10, 95), 0, 100)
    liq_body = {
        "series": "全球美元流动性",
        "actual": _round(dxy_20d, 3),
        "unit": "DXY 20d %",
        "risk": _round(liq, 1),
        "note": "美元快速升值叠加短端利率上行 → 全球美元流动性收紧。",
    }
    leaf = _leaf("dollar_liq", "Dollar Liquidity", liq_body, LEAF_W["dollar_liq"])
    if leaf:
        fx_leaves.append(leaf)

    hy_hist = _downsample_weekly([p["value"] for p in hy_pts]) if hy_pts else []
    hy_body = _try_lst(hy_hist, level_fn=_level_hy_oas, inner_w=(0.35, 0.35, 0.30))
    if not hy_body and hyg_c:
        hy_body = {
            "series": "HY 利差（HYG 代理）",
            "actual": _round(hyg_20d, 3),
            "unit": "HYG 20d %",
            "risk": _round(_ret_score(hyg_20d), 1),
            "note": "FRED HY OAS 不可用时用 HYG 回报倒置代理。",
        }
    elif hy_body:
        hy_body["series"] = "HY OAS (ICE BofA)"
        hy_body["unit"] = "bps"
        hy_body["as_of"] = hy_pts[-1]["date"] if hy_pts else None
        if hy_widen_bps is not None and hy_widen_bps > 15:
            hy_body["risk"] = _round(_clip(float(hy_body["risk"]) + min(12, hy_widen_bps / 5), 0, 100), 1)
    if hy_body:
        leaf = _leaf("hy", "HY Spread", hy_body, LEAF_W["hy"])
        if leaf:
            credit_leaves.append(leaf)

    nfci_pts = ((fred or {}).get("nfci") or {}).get("points") or []
    nfci_hist = _downsample_weekly([p["value"] for p in nfci_pts]) if nfci_pts else []
    fci_body = _try_lst(nfci_hist, level_fn=_level_nfci, inner_w=(0.40, 0.30, 0.30))
    if not fci_body:
        fci_proxy = _blend_risk([(0.5, _vix_score(vix_lv, vix_5d)), (0.5, (hy_body or {}).get("risk"))])
        fci_body = {
            "series": "金融条件（VIX+HY 代理）",
            "actual": _round(vix_lv, 2),
            "unit": "VIX",
            "risk": _round(fci_proxy, 1),
            "note": "NFCI 不可用时用 VIX 与 HY 合成。",
        }
    else:
        fci_body["series"] = "NFCI 金融条件"
        fci_body["unit"] = "指数"
        fci_body["as_of"] = nfci_pts[-1]["date"] if nfci_pts else None
    leaf = _leaf("fci", "FCI", fci_body, LEAF_W["fci"])
    if leaf:
        credit_leaves.append(leaf)

    # 日元警报
    vix_rising = (vix_5d or 0) > 8 or ((vix_lv or 0) >= 20 and (vix_5d or 0) > 0)
    hy_widening = (hy_widen_bps or 0) > 8 or (hyg_5d is not None and hyg_5d < -1.5)
    alert = None
    if (
        (jpy_5d or 0) < -3
        and vix_rising
        and (qqq_5d or 0) < -3
        and hy_widening
    ):
        alert = {
            "level": "red",
            "key": "unwind",
            "emoji": "🔴",
            "label": "JPY Carry Unwind",
            "detail": "USD/JPY 5 日急跌、VIX 上升、QQQ 回撤、HY 利差走阔同时出现，日元套利平仓通道打开。",
        }
    elif (jpy_5d or 0) < -2 and hv_pct > 80 and (vix_lv or 0) > 20:
        alert = {
            "level": "orange",
            "key": "carry_risk",
            "emoji": "🟠",
            "label": "Carry Risk",
            "detail": "日元 5 日升值超过 2%，USD/JPY 波动率位于 80 分位以上，且 VIX>20。",
        }

    meta = {
        "jpy_5d": _round(jpy_5d, 3),
        "dxy_5d": _round(dxy_5d, 3),
        "qqq_5d": _round(qqq_5d, 3),
        "spy_5d": _round(spy_5d, 3),
        "vix": _round(vix_lv, 2),
        "vix_5d": _round(vix_5d, 2),
        "hv_percentile": _round(hv_pct, 1),
        "usdjpy": _round(jpy_c[-1] if jpy_c else None, 3),
        "dxy": _round(dxy_c[-1] if dxy_c else None, 3),
        "hy_oas": _round(hy_last, 2),
        "hy_widen_5d": _round(hy_widen_bps, 2),
        "dxy_context": dxy_context,
        "carry_context": "risk_on_yen_up" if yen_up and risk_on else ("unwind_like" if yen_up and risk_off else "normal"),
        "jpy_alert": alert,
        "yen_up_not_bearish": bool(yen_up and risk_on),
    }
    return fx_leaves, credit_leaves, meta


def _module(mid: str, label: str, emoji: str, weight: float, leaves: list[dict]) -> dict:
    risk = _blend_risk([(lf["weight"], lf.get("risk")) for lf in leaves]) if leaves else None
    return {
        "id": mid,
        "label": label,
        "emoji": emoji,
        "weight": weight,
        "risk": _round(risk, 1) if risk is not None else None,
        "children": leaves,
    }


def _resonance(modules: list[dict], sources: dict) -> dict:
    checks = [
        ("通胀", sources.get("inflation"), 70),
        ("美债", sources.get("treasury"), 75),
        ("实际利率", sources.get("real10"), 75),
        ("美元", sources.get("usd"), 70),
        ("日元Carry", sources.get("jpy_carry"), 70),
        ("信用", sources.get("credit"), 70),
    ]
    hot = [name for name, val, th in checks if val is not None and val >= th]
    n = len(hot)
    penalty = 0.0
    if n >= 2:
        penalty = min(18.0, 5.0 * (n - 1) + (3.0 if n >= 4 else 0.0))
    return {
        "hot": hot,
        "count": n,
        "penalty": _round(penalty, 1),
        "note": "多支柱同时偏热时追加共振惩罚，封顶 18 分。" if n >= 2 else "尚未出现跨模块共振。",
    }


def _source_rank(sources: dict) -> tuple[str | None, float, float]:
    ranked = sorted(
        ((k, v) for k, v in (sources or {}).items() if k != "real10" and v is not None),
        key=lambda kv: -kv[1],
    )
    if not ranked:
        return None, 0.0, 0.0
    top_k, top_v = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    return top_k, float(top_v), float(second)


def _rates_alert(tsy_meta: dict, sources: dict, fsi: dict | None = None) -> dict | None:
    """名义/实际利率水平预警。周度 Surprise 不大时，高利率仍应出现在预警里。"""
    ust10 = tsy_meta.get("ust10")
    ust2 = tsy_meta.get("ust2")
    real10 = tsy_meta.get("real10")
    chg20 = tsy_meta.get("ust10_chg20")
    tsy = float(sources.get("treasury") or 0)
    real_risk = float(sources.get("real10") or 0)
    top_k, top_v, second = _source_rank(sources)
    dominant = top_k == "treasury" and top_v >= 55 and (top_v - second) >= 12

    high_real = real10 is not None and real10 >= 2.0
    high_nom = ust10 is not None and ust10 >= 4.5
    elevated = tsy >= 60 or real_risk >= 60 or high_real or high_nom or dominant
    if not elevated:
        return None

    extreme = (real10 is not None and real10 >= 2.5 and tsy >= 70) or (
        chg20 is not None and chg20 >= 0.40 and (high_real or high_nom)
    )
    if extreme:
        level, key, emoji, label = "red", "real_extreme", "🔴", "实际利率极高"
    elif high_real or real_risk >= 70:
        level, key, emoji, label = "orange", "high_real_yield", "🟠", "美债实际利率过高"
    else:
        level, key, emoji, label = "orange", "high_yield", "🟠", "美债利率过高"

    bits = []
    if ust10 is not None:
        bits.append(f"10Y {float(ust10):.2f}%")
    if real10 is not None:
        bits.append(f"实际利率 {float(real10):.2f}%")
    if ust2 is not None:
        bits.append(f"2Y {float(ust2):.2f}%")
    if chg20 is not None:
        bits.append(f"10Y 20日 {float(chg20) * 100:+.0f}bp")
    head = "、".join(bits) + "。" if bits else ""

    if dominant:
        detail = (
            f"{head}当前宏观风险几乎全部来自美债/实际利率（{tsy:.0f}），"
            "通胀、就业、日元、信用均未过热。"
            "成长股估值对实际利率更敏感；高利率本身不等于次贷危机。"
        )
    else:
        detail = (
            f"{head}名义与实际利率偏高，对久期资产和成长股构成估值压力。"
            "高利率本身不等于次贷危机，要看住房/信用是否共振。"
        )
    if fsi and fsi.get("dampen_high_rate_only"):
        detail += "FSI 显示住房与信用仍稳，尚未进入金融危机通道。"
    return {
        "level": level,
        "key": key,
        "emoji": emoji,
        "label": label,
        "detail": detail,
        "dominant": dominant,
        "ust10": _round(ust10, 3),
        "real10": _round(real10, 3),
        "ust2": _round(ust2, 3),
    }


def _collect_alerts(
    *,
    jpy_alert: dict | None,
    rates_alert: dict | None,
    fsi: dict | None,
    resonance: dict | None,
    pillar_alerts: list[dict] | None = None,
) -> list[dict]:
    alerts: list[dict] = []
    seen: set[str] = set()

    def _add(row: dict | None):
        if not row or not row.get("key") or row["key"] in seen:
            return
        seen.add(row["key"])
        alerts.append(row)

    if jpy_alert and jpy_alert.get("key") == "unwind":
        _add(jpy_alert)
    if fsi and fsi.get("ok") and fsi.get("regime") == "financial_crisis":
        _add({
            "level": "red",
            "key": "financial_crisis",
            "emoji": fsi.get("emoji") or "🔴",
            "label": fsi.get("call") or "金融危机预警",
            "detail": fsi.get("answer") or "",
        })
    _add(rates_alert)
    if jpy_alert and jpy_alert.get("key") != "unwind":
        _add(jpy_alert)
    for row in pillar_alerts or []:
        _add(row)
    if fsi and fsi.get("ok") and fsi.get("regime") == "credit_contraction":
        _add({
            "level": "orange",
            "key": "credit_contraction",
            "emoji": fsi.get("emoji") or "🟠",
            "label": fsi.get("call") or "信用/住房压力",
            "detail": fsi.get("answer") or "",
        })
    if resonance and (resonance.get("count") or 0) >= 2:
        hot = "、".join(resonance.get("hot") or [])
        _add({
            "level": "orange",
            "key": "resonance",
            "emoji": "🟠",
            "label": "跨模块共振",
            "detail": f"{hot}同时偏热，共振 +{resonance.get('penalty') or 0}。",
        })
    return alerts


PILLAR_ALERT_SPEC = (
    {
        "id": "inflation",
        "hot": 70,
        "red": 80,
        "label_hot": "通胀偏热",
        "label_red": "通胀过热",
        "detail": "通胀支柱 {score}。粘性通胀会抬高中性利率，对久期和成长股估值不利。",
        "dominant": "当前宏观风险主要来自通胀（{score}），其他支柱相对不热。",
    },
    {
        "id": "growth",
        "hot": 70,
        "red": 80,
        "label_hot": "就业/经济转弱",
        "label_red": "衰退压力",
        "detail": "就业/经济支柱 {score}。NFP 要和失业率、时薪一起看，不是单独利空。",
        "dominant": "当前宏观风险主要来自就业/经济（{score}），其他支柱相对不热。",
    },
    {
        "id": "usd",
        "hot": 70,
        "red": 85,
        "label_hot": "美元过强",
        "label_red": "美元流动性收紧",
        "detail": "美元/流动性支柱 {score}。强美元抬高海外融资成本，风险资产承压。",
        "dominant": "当前宏观风险主要来自美元（{score}），其他支柱相对不热。",
    },
    {
        "id": "jpy_carry",
        "hot": 70,
        "red": 80,
        "label_hot": "日元 Carry 升温",
        "label_red": "日元套利平仓风险",
        "detail": "日元 Carry {score}。日元升值本身不等于利空，需同时看到风险资产压力才按 Unwind 计价。",
        "dominant": "当前宏观风险主要来自日元套利交易（{score}）。",
    },
    {
        "id": "credit",
        "hot": 70,
        "red": 80,
        "label_hot": "信用压力",
        "label_red": "信用收缩",
        "detail": "信用/流动性支柱 {score}。利差走阔会收紧企业再融资和银行风险偏好。",
        "dominant": "当前宏观风险主要来自信用（{score}），其他支柱相对不热。",
    },
)


def _pillar_alerts(sources: dict, *, jpy_alert: dict | None = None) -> list[dict]:
    """通胀 / 就业 / 美元 / 日元 / 信用：过热或成为主导来源时出预警。美债走独立的 rates_alert。"""
    top_k, top_v, second = _source_rank(sources)
    out: list[dict] = []
    for spec in PILLAR_ALERT_SPEC:
        sid = spec["id"]
        if sid == "jpy_carry" and jpy_alert:
            continue
        score = sources.get(sid)
        if score is None:
            continue
        score_f = float(score)
        dominant = top_k == sid and top_v >= 55 and (top_v - second) >= 12
        hot = score_f >= spec["hot"]
        if not hot and not dominant:
            continue
        red = score_f >= spec["red"]
        tmpl = spec["dominant"] if dominant else spec["detail"]
        out.append({
            "level": "red" if red else "orange",
            "key": f"pillar_{sid}",
            "emoji": "🔴" if red else "🟠",
            "label": spec["label_red"] if red else spec["label_hot"],
            "detail": tmpl.format(score=f"{score_f:.0f}"),
            "dominant": dominant,
        })
    return out


def _narrative(
    sources: dict,
    *,
    regime_label: str,
    alert: dict | None,
    yen_not_bearish: bool,
    rate_call: dict | None = None,
    fsi: dict | None = None,
    rates_alert: dict | None = None,
) -> str:
    labels = {
        "inflation": "通胀",
        "growth": "就业/经济",
        "treasury": "美债/实际利率",
        "usd": "美元",
        "jpy_carry": "日元套利交易",
        "credit": "信用/流动性",
    }
    ranked = sorted(
        ((k, v) for k, v in sources.items() if k in labels and v is not None),
        key=lambda kv: -kv[1],
    )
    if not ranked:
        return "宏观数据不足，暂无法判断主导风险。"
    top = [(k, v) for k, v in ranked if v >= 60][:2]
    top_ids = {k for k, _ in (top or ranked[:1])}
    quiet = [(k, v) for k, v in ranked if v < 45 and k not in top_ids][:2]
    bits = [f"当前宏观状态：{regime_label}。"]
    if alert:
        bits.append(f"{alert['emoji']} {alert['label']}：{alert['detail']}")
    elif yen_not_bearish:
        bits.append("日元有所升值，但风险资产同步走强，不按 Carry Unwind 计价。")
    if rates_alert:
        bits.append(f"{rates_alert['emoji']} {rates_alert['label']}：{rates_alert['detail']}")
    if top:
        names = "和".join(labels[k] for k, _ in top)
        bits.append(f"宏观风险主要来自{names}（" + "、".join(f"{labels[k]} {v:.0f}" for k, v in top) + "）。")
        if "Recession" in regime_label or regime_label.startswith("🔴"):
            bits.append("就业也偏弱，但尚未与日元/信用形成共振。")
    elif "Recession" in regime_label or regime_label.startswith("🔴"):
        bits.append("就业已经偏弱，但通胀、利率、日元尚未形成共振，所以综合冲击分数仍不高。")
    else:
        bits.append("各支柱均未明显过热，宏观冲击中性。")
    if quiet:
        bits.append("相对不是主因：" + "、".join(f"{labels[k]} {v:.0f}" for k, v in quiet[:2]) + "。")
    if rate_call and rate_call.get("ok"):
        mkt = rate_call.get("market") or {}
        mdl = rate_call.get("model") or {}
        dots = rate_call.get("dots") or {}
        meet = (rate_call.get("meeting") or {}).get("title") or "下次 FOMC"
        bits.append(
            f"{meet}：市场定价{mkt.get('call') or '—'}（维持 {mkt.get('p_hold')}% / 加息 {mkt.get('p_hike')}% / 降息 {mkt.get('p_cut')}%）"
            f"，模型{mdl.get('call') or '—'}（维持 {mdl.get('p_hold')}% / 加息 {mdl.get('p_hike')}% / 降息 {mdl.get('p_cut')}%）。"
        )
        if dots.get("n_hike") is not None:
            bits.append(
                f"最新点阵（{dots.get('released')}）{dots.get('n')}人里{dots.get('n_hike')}人把年底点在加息、"
                f"{dots.get('n_hold')}人维持、{dots.get('n_cut')}人降息，中位 {dots.get('median')}%。这是年底路径，不是下次会议投票。"
            )
        div = rate_call.get("divergence") or {}
        if div.get("detail"):
            bits.append(div["detail"])
    if fsi and fsi.get("ok"):
        bits.append(f"次贷/金融危机：{fsi.get('emoji')} {fsi.get('call')}（FSI {fsi.get('score')}）。{fsi.get('answer')}")
    bits.append("这是风险环境分数，不是买卖信号。")
    return "".join(bits)


def _macro_direction(*, infl_z: float, growth_meta: dict, rates_z: float, fx_meta: dict, credit_risk: float, jpy_carry: float) -> dict:
    d_infl = -_clip(infl_z, -3, 3) * 18
    pmi_lv = growth_meta.get("pmi_level")
    d_growth = 0.0
    if pmi_lv is not None:
        d_growth = _clip((float(pmi_lv) - 50.0) * 6.0, -40, 40)
    d_emp = float(growth_meta.get("equity_direction_hint") or 0)
    d_rates = -_clip(rates_z, -3, 3) * 16
    d_jpy = 0.0
    alert = fx_meta.get("jpy_alert") or {}
    if alert.get("key") == "unwind":
        d_jpy = -35.0
    elif alert.get("key") == "carry_risk":
        d_jpy = -18.0
    elif jpy_carry >= 70 and not fx_meta.get("yen_up_not_bearish"):
        d_jpy = -12.0
    d_credit = -_clip((credit_risk - 50) / 10.0, -3, 3) * 8
    d_dxy = 0.0
    if fx_meta.get("dxy_context") == "tightening":
        d_dxy = -10.0
    elif fx_meta.get("dxy_context") == "safe_haven":
        d_dxy = 4.0
    total = _clip(
        0.28 * d_infl + 0.18 * d_growth + 0.14 * d_emp + 0.18 * d_rates + 0.12 * d_jpy + 0.06 * d_credit + 0.04 * d_dxy,
        -100,
        100,
    )
    return {
        "score": _round(total, 1),
        "band": _dir_band(total),
        "components": {
            "inflation": _round(d_infl, 1),
            "growth": _round(d_growth, 1),
            "employment": _round(d_emp, 1),
            "rates": _round(d_rates, 1),
            "jpy_carry": _round(d_jpy, 1),
            "credit": _round(d_credit, 1),
            "dxy": _round(d_dxy, 1),
        },
    }


def _equity_risk_proxy(macro_risk: float, direction: float) -> dict:
    vol_base = 0.16
    vol = vol_base * (1.0 + 0.6 * (macro_risk / 100.0))
    skew = -0.35 if direction < 0 else 0.1
    return {
        "label": "Equity Risk（宏观映射）",
        "implied_vol_annual": _round(vol, 4),
        "skew_hint": _round(skew, 3),
        "note": "由 Macro Risk / Direction 映射；高 Beta 成长股对实际利率和日元 Carry 更敏感。",
    }


def _mc_scenario(macro_risk: float, direction: float) -> dict:
    r = macro_risk / 100.0
    d = direction / 100.0
    return {
        "vol_mult": _round(1.0 + 0.55 * r, 3),
        "drift_shift_annual": _round(d * 0.10, 4),
        "left_tail_mult": _round(1.0 + 0.45 * r * (1.0 if d < 0 else 0.35), 3),
        "gap_down": _round(-0.01 - 0.04 * r * (1.0 if d < 0 else 0.4), 4),
        "note": "接入 Monte Carlo：波动×vol_mult，漂移+=drift_shift，左尾×left_tail_mult。",
    }


def _drivers(leaves: list[dict], tsy_meta: dict | None = None, rates_alert: dict | None = None) -> list[str]:
    items = []
    meta = tsy_meta or {}
    real10 = meta.get("real10")
    ust10 = meta.get("ust10")
    chg20 = meta.get("ust10_chg20")
    if rates_alert and rates_alert.get("dominant"):
        items.append(
            f"主导风险是美债/实际利率（{rates_alert.get('label')}："
            + "、".join(
                x for x in [
                    f"10Y {float(ust10):.2f}%" if ust10 is not None else None,
                    f"实际利率 {float(real10):.2f}%" if real10 is not None else None,
                ] if x
            )
            + "）"
        )
    if real10 is not None and float(real10) >= 2.0:
        items.append(f"10Y 实际利率 {float(real10):.2f}% 偏高（成长股估值压力）")
    if ust10 is not None and float(ust10) >= 4.0:
        items.append(f"10Y Treasury {float(ust10):.2f}% 处于高位")
    if chg20 is not None and abs(float(chg20)) >= 0.25:
        items.append(f"10Y 近20日变动 {float(chg20) * 100:+.0f}bp")
    for lf in leaves:
        body = lf.get("detail") or {}
        name = body.get("series") or lf.get("label")
        zs = body.get("z_surprise")
        if zs is not None and abs(float(zs)) >= 1.0:
            sign = "高于" if (body.get("surprise") or 0) > 0 else "低于"
            items.append(f"{name} {sign}预期 {abs(float(zs)):.1f}σ")
        zt = body.get("z_trend")
        if zt is not None and abs(float(zt)) >= 1.5:
            items.append(f"{name} 趋势 {float(zt):+.1f}σ")
        if lf.get("id") == "jpy_carry" and (lf.get("risk") or 0) >= 70:
            items.append(f"日元 Carry 风险 {lf.get('risk'):.0f}（{body.get('note') or 'Unwind 通道'}）")
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out[:8]


def _leaf_actual(leaves: list[dict], lid: str):
    for lf in leaves:
        if lf.get("id") == lid:
            return (lf.get("detail") or {}).get("actual")
    return None


def _leaf_risk(leaves: list[dict], lid: str) -> float | None:
    for lf in leaves:
        if lf.get("id") == lid:
            return lf.get("risk")
    return None


def parse_as_of(raw: str | None) -> tuple[str | None, str | None]:
    """返回 (YYYY-MM, YYYY-MM-DD 月末)。空值表示用最新。"""
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s or s in {"latest", "now", "max"}:
        return None, None
    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            y, m, d = int(s[:4]), int(s[5:7]), int(s[8:10])
            if not (1 <= m <= 12 and 1 <= d <= 31):
                raise ValueError
            return f"{y:04d}-{m:02d}", f"{y:04d}-{m:02d}-{d:02d}"
        if len(s) >= 7 and s[4] == "-":
            y, m = int(s[:4]), int(s[5:7])
            if not (1 <= m <= 12):
                raise ValueError
            last = monthrange(y, m)[1]
            return f"{y:04d}-{m:02d}", f"{y:04d}-{m:02d}-{last:02d}"
    except ValueError:
        pass
    raise FedRiskError("回测月份请用 YYYY-MM，例如 2024-03")


def _point_on_or_before(pt_date, cutoff: str) -> bool:
    if not pt_date:
        return False
    p = str(pt_date).strip()
    if len(p) == 7:
        return p <= cutoff[:7]
    if len(p) >= 10:
        return p[:10] <= cutoff
    return p <= cutoff


def _slice_points(points, cutoff: str) -> list:
    return [p for p in (points or []) if isinstance(p, dict) and _point_on_or_before(p.get("date"), cutoff)]


def _slice_group(payload: dict | None, cutoff: str) -> dict | None:
    if not payload:
        return payload
    out = dict(payload)
    series = []
    for row in payload.get("series") or []:
        item = dict(row)
        pts = _slice_points(row.get("points"), cutoff)
        item["points"] = pts
        item["latest"] = pts[-1] if pts else None
        item["end"] = (pts[-1] or {}).get("date") if pts else None
        series.append(item)
    out["series"] = series
    return out


def _slice_labor(labor: dict | None, cutoff: str) -> dict:
    if not labor:
        return {}
    out = {}
    for key, pts in labor.items():
        if isinstance(pts, list):
            out[key] = _slice_points(pts, cutoff)
        else:
            out[key] = pts
    return out


def _slice_tv_bundle(bundle: dict | None, cutoff: str) -> dict:
    out = {}
    for key, payload in (bundle or {}).items():
        if not isinstance(payload, dict):
            out[key] = payload
            continue
        row = dict(payload)
        pts = _slice_points(payload.get("points"), cutoff)
        closes = [p["value"] for p in pts if p.get("value") is not None]
        row["points"] = pts
        row["closes"] = closes
        row["price"] = closes[-1] if closes else None
        row["ok"] = bool(closes)
        out[key] = row
    return out


def _load_raw_inputs(*, force: bool = False) -> dict:
    from app.services.macro import _read_cache, _write_cache

    if not force:
        hit = _read_cache(RAW_CACHE)
        if hit and (hit.get("cpi") or hit.get("treasury")):
            return hit

    payloads: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=9) as pool:
        futs = {
            "cpi": pool.submit(build_group, "cpi"),
            "ppi": pool.submit(build_group, "ppi"),
            "pce": pool.submit(build_group, "pce"),
            "nfp": pool.submit(build_group, "nfp", False),
            "pmi": pool.submit(build_group, "pmi"),
            "treasury": pool.submit(build_group, "treasury"),
            "labor": pool.submit(_fetch_labor_extras),
            "prices": pool.submit(lambda: (_fetch_market_bundle(), _fetch_fred_bundle())),
        }
        errors = []
        for key, fut in futs.items():
            try:
                payloads[key] = fut.result()
            except Exception as exc:  # noqa: BLE001
                payloads[key] = None
                if key not in {"labor", "prices"}:
                    errors.append(f"{key}:{exc}")
        if errors and not payloads.get("cpi"):
            raise FedRiskError("宏观数据拉取失败：" + "; ".join(errors[:3]))

    mkt, fred = payloads.pop("prices", None) or ({}, {})
    packed = {
        "cpi": payloads.get("cpi"),
        "ppi": payloads.get("ppi"),
        "pce": payloads.get("pce"),
        "nfp": payloads.get("nfp"),
        "pmi": payloads.get("pmi"),
        "treasury": payloads.get("treasury"),
        "labor": payloads.get("labor") or {},
        "mkt": mkt or {},
        "fred": fred or {},
        "fetched_at": _now(),
    }
    _write_cache(RAW_CACHE, packed)
    return packed


def build_fed_risk(*, force: bool = False, as_of: str | None = None) -> dict:
    from app.services.macro import _read_cache, _write_cache

    month_key, cutoff = parse_as_of(as_of)
    result_cache = CACHE_NAME if not month_key else f"{CACHE_NAME}_{month_key.replace('-', '')}"
    if not force:
        cached = _read_cache(result_cache)
        if cached:
            return cached

    raw = _load_raw_inputs(force=force)
    if cutoff:
        payloads = {
            "cpi": _slice_group(copy.deepcopy(raw.get("cpi")), cutoff),
            "ppi": _slice_group(copy.deepcopy(raw.get("ppi")), cutoff),
            "pce": _slice_group(copy.deepcopy(raw.get("pce")), cutoff),
            "nfp": _slice_group(copy.deepcopy(raw.get("nfp")), cutoff),
            "pmi": _slice_group(copy.deepcopy(raw.get("pmi")), cutoff),
            "treasury": _slice_group(copy.deepcopy(raw.get("treasury")), cutoff),
            "labor": _slice_labor(copy.deepcopy(raw.get("labor") or {}), cutoff),
        }
        mkt = _slice_tv_bundle(copy.deepcopy(raw.get("mkt") or {}), cutoff)
        fred = _slice_tv_bundle(copy.deepcopy(raw.get("fred") or {}), cutoff)
        consensus = {}
    else:
        payloads = {
            "cpi": raw.get("cpi"),
            "ppi": raw.get("ppi"),
            "pce": raw.get("pce"),
            "nfp": raw.get("nfp"),
            "pmi": raw.get("pmi"),
            "treasury": raw.get("treasury"),
            "labor": raw.get("labor") or {},
        }
        mkt = raw.get("mkt") or {}
        fred = raw.get("fred") or {}
        consensus = _load_consensus()

    today_m = date.today().strftime("%Y-%m")
    historical = bool(month_key and month_key < today_m)

    cpi_h, cpi_c = _resolve_infl_series(payloads.get("cpi"), ["CUSR0000SA0", "SA0"], ["CUSR0000SA0L1E", "SA0L1E"])
    pce_h, pce_c = _resolve_infl_series(payloads.get("pce"), ["DPCERG-M"], ["DPCCRG-M"])
    ppi_h, ppi_c = _resolve_infl_series(payloads.get("ppi"), ["WPSFD4"], ["WPSFD49104"])

    # 石油：走市场行情（TradingView/CL=F），不是 BLS 月度发布，没有"预期值"这个概念，
    # 所以不走 _compute_l_s_t 那套 Level-Surprise-Trend 引擎，改用短期涨跌幅打分
    # （_oil_score）——跟 DXY/VIX 这些市场型叶子是同一个风格。
    oil = (mkt or {}).get("CL=F") or {}
    oil_c = oil.get("closes") or []
    oil_price = float(oil.get("price") or (oil_c[-1] if oil_c else 0) or 0) or None
    oil_5d = _pct_n(oil_c, 5)
    oil_20d = _pct_n(oil_c, 20)
    oil_leaf = None
    if oil_price is not None:
        oil_leaf = _leaf(
            "oil", "WTI 原油",
            {
                "series": "WTI Crude Oil", "actual": oil_price, "unit": "$/桶",
                "chg5": oil_5d, "chg20": oil_20d,
                "risk": _oil_score(oil_5d, oil_20d),
                "note": "油价短期急涨是输入型通胀信号，会直接传导到 CPI/PPI 能源分项；下跌不额外降低风险评分。",
            },
            LEAF_W["oil"],
        )

    infl_leaves = [
        x for x in [
            _yoy_leaf_from_row(cpi_h, lid="cpi", label="CPI", inner_w=INNER["cpi"], consensus=consensus, consensus_key="cpi_headline"),
            _yoy_leaf_from_row(cpi_c, lid="core_cpi", label="Core CPI", inner_w=INNER["cpi"], consensus=consensus, consensus_key="cpi_core"),
            _yoy_leaf_from_row(pce_c or pce_h, lid="core_pce", label="Core PCE", inner_w=INNER["pce"], consensus=consensus, consensus_key="pce_core"),
            _yoy_leaf_from_row(ppi_h or ppi_c, lid="ppi", label="PPI", inner_w=INNER["ppi"], consensus=consensus, consensus_key="ppi_headline"),
            oil_leaf,
        ] if x
    ]
    infl_mod = _module("inflation", "通胀", "🔥", PILLAR_W["inflation"], infl_leaves)
    cpi_risk = float(infl_mod.get("risk") or 50)

    growth_leaves, growth_meta = _growth_leaves(
        payloads.get("nfp"), payloads.get("labor") or {}, payloads.get("pmi"), consensus, cpi_risk
    )
    growth_mod = _module("growth", "就业/经济", "👷", PILLAR_W["growth"], growth_leaves)

    tsy_leaves, tsy_meta = _treasury_leaves(payloads.get("treasury"), fred or {}, consensus, cutoff)
    tsy_mod = _module("treasury", "美债/利率", "🏦", PILLAR_W["treasury"], tsy_leaves)

    fx_leaves, credit_leaves, fx_meta = _fx_and_credit(
        mkt or {}, fred or {}, tsy_meta, consensus
    )
    fx_mod = _module("fx", "外汇/日元", "💵", PILLAR_W["fx"], fx_leaves)
    credit_mod = _module("credit", "信用/流动性", "💧", PILLAR_W["credit"], credit_leaves)

    modules = [infl_mod, growth_mod, tsy_mod, fx_mod, credit_mod]
    all_leaves = [lf for m in modules for lf in (m.get("children") or [])]

    sources = {
        "inflation": infl_mod.get("risk"),
        "growth": growth_mod.get("risk"),
        "treasury": (
            max(x for x in [tsy_mod.get("risk"), _leaf_risk(tsy_leaves, "real10")] if x is not None)
            if any(x is not None for x in [tsy_mod.get("risk"), _leaf_risk(tsy_leaves, "real10")])
            else None
        ),
        "usd": _blend_risk([
            (LEAF_W["dxy"], _leaf_risk(fx_leaves, "dxy")),
            (LEAF_W["dollar_liq"], _leaf_risk(fx_leaves, "dollar_liq")),
        ]) if any(_leaf_risk(fx_leaves, k) is not None for k in ("dxy", "dollar_liq")) else None,
        "jpy_carry": _leaf_risk(fx_leaves, "jpy_carry"),
        "credit": credit_mod.get("risk"),
        "real10": _leaf_risk(tsy_leaves, "real10"),
        "oil": _leaf_risk(infl_leaves, "oil"),
    }
    sources_ui = [
        {"id": "inflation", "label": "Inflation", "zh": "通胀", "score": _round(sources["inflation"], 1)},
        {"id": "growth", "label": "Growth", "zh": "就业/经济", "score": _round(sources["growth"], 1)},
        {"id": "treasury", "label": "Treasury", "zh": "美债/利率", "score": _round(sources["treasury"], 1)},
        {"id": "usd", "label": "USD", "zh": "美元", "score": _round(sources["usd"], 1)},
        {"id": "jpy_carry", "label": "JPY Carry", "zh": "日元套利", "score": _round(sources["jpy_carry"], 1)},
        {"id": "oil", "label": "Oil", "zh": "石油", "score": _round(sources["oil"], 1)},
        {"id": "credit", "label": "Credit", "zh": "信用/流动性", "score": _round(sources["credit"], 1)},
    ]

    base_risk = _blend_risk([(m["weight"], m.get("risk")) for m in modules])
    reso = _resonance(modules, sources)
    macro_risk = _clip(base_risk + float(reso.get("penalty") or 0), 0, 100)

    infl_z = mean([
        _pressure_z((lf.get("detail") or {}))
        for lf in infl_leaves
    ] or [0.0])
    direction = _macro_direction(
        infl_z=infl_z,
        growth_meta=growth_meta,
        rates_z=float(tsy_meta.get("rates_pressure_z") or 0),
        fx_meta=fx_meta,
        credit_risk=float(credit_mod.get("risk") or 50),
        jpy_carry=float(sources.get("jpy_carry") or 50),
    )

    # 宏观状态：金融压力 / 滞胀 覆盖就业状态
    regime = growth_meta.get("regime") or "neutral"
    if (fx_meta.get("jpy_alert") or {}).get("key") == "unwind" or (sources.get("credit") or 0) >= 75 or (
        (sources.get("treasury") or 0) >= 75 and (fx_mod.get("risk") or 0) >= 70
    ):
        regime = "financial_stress"
    elif (sources.get("inflation") or 0) >= 65 and (sources.get("growth") or 0) >= 60:
        regime = "stagflation"
    elif (sources.get("inflation") or 0) >= 70 and regime == "overheating":
        regime = "overheating"

    nfci_pts = ((fred or {}).get("nfci") or {}).get("points") or []
    nfci_lv = nfci_pts[-1]["value"] if nfci_pts else None
    try:
        from app.services.financial_stress import build_financial_stress

        fsi = build_financial_stress(
            tsy_meta=tsy_meta,
            fred=fred or {},
            mkt=mkt or {},
            jpy_carry=sources.get("jpy_carry"),
            hy_risk=_leaf_risk(credit_leaves, "hy"),
            nfci=nfci_lv,
        )
    except Exception:
        fsi = {"ok": False}

    if (fsi or {}).get("regime") == "financial_crisis":
        regime = "financial_crisis"
    elif (fsi or {}).get("regime") == "credit_contraction" and regime not in {"financial_stress", "financial_crisis"}:
        regime = "credit_contraction"

    regime_label = {
        "overheating": "🔥 Inflation / Overheating",
        "soft_landing": "🟢 Soft Landing",
        "recession": "🔴 Recession",
        "stagflation": "🟣 Stagflation",
        "financial_stress": "💥 Financial Stress",
        "credit_contraction": "🟠 Credit / Housing Stress",
        "financial_crisis": "🔴 Financial Crisis",
        "neutral": "🟡 Neutral",
    }.get(regime, regime)

    d_score = float(direction["score"] or 0)
    jpy_hot = (sources.get("jpy_carry") or 0) >= 70
    real_hot = (sources.get("real10") or 0) >= 70
    qqq_hint = "正常"
    if jpy_hot and real_hot:
        qqq_hint = "风险 ↑↑（利率 + 日元 Carry）"
    elif jpy_hot:
        qqq_hint = "风险 ↑↑（日元 Carry 传导）"
    elif d_score < -20 and (real_hot or (sources.get("inflation") or 0) >= 60):
        qqq_hint = "风险 ↑↑"
    elif macro_risk >= 55:
        qqq_hint = "风险 ↑"
    asset_hints = {
        "SPY": "风险 ↑" if macro_risk >= 55 and d_score < 0 else ("波动 ↑" if macro_risk >= 55 else "正常"),
        "QQQ": qqq_hint,
        "NVDA / SOXX": "高 Beta：对实际利率与日元平仓更敏感" if (jpy_hot or real_hot) else ("波动 ↑" if macro_risk >= 55 else "正常"),
        "XLF / KRE": "银行/区域行承压" if (fsi or {}).get("score", 0) >= 60 else (
            "观察 CRE / 再融资" if (fsi or {}).get("score", 0) >= 45 else "正常"
        ),
        "TLT": "风险 ↑↑" if (sources.get("inflation") or 0) >= 65 or (sources.get("treasury") or 0) >= 70 else (
            "承压" if (sources.get("treasury") or 0) >= 55 else "中性"
        ),
    }

    pi = None
    pi_source = None
    for lid, label in (("core_pce", "Core PCE"), ("core_cpi", "Core CPI"), ("cpi", "CPI")):
        val = _leaf_actual(infl_leaves, lid)
        if val is not None:
            pi, pi_source = val, label
            break
    if historical:
        rate_call = {
            "ok": False,
            "skipped": True,
            "note": f"历史回测 {month_key} 不使用当前市场隐含的降息/加息定价。",
        }
    else:
        try:
            from app.services.fed_path import build_rate_call

            rate_call = build_rate_call(
                pi=pi,
                pi_source=pi_source,
                unemployment=growth_meta.get("unemployment"),
                credit_risk=credit_mod.get("risk"),
                nfci=nfci_lv,
                wage_yoy=growth_meta.get("wage_yoy"),
                pmi=growth_meta.get("pmi_level"),
                inflation_risk=infl_mod.get("risk"),
                growth_regime=growth_meta.get("regime"),
                ust2=tsy_meta.get("ust2"),
            )
        except Exception as exc:
            rate_call = {"ok": False, "error": str(exc)}

    rates_alert = _rates_alert(tsy_meta, sources, fsi if (fsi or {}).get("ok") else None)
    pillar_alerts = _pillar_alerts(sources, jpy_alert=fx_meta.get("jpy_alert"))
    alerts = _collect_alerts(
        jpy_alert=fx_meta.get("jpy_alert"),
        rates_alert=rates_alert,
        fsi=fsi if (fsi or {}).get("ok") else None,
        resonance=reso,
        pillar_alerts=pillar_alerts,
    )

    equity = _equity_risk_proxy(macro_risk, d_score)
    mc = _mc_scenario(macro_risk, d_score)
    narrative = _narrative(
        sources,
        regime_label=regime_label,
        alert=fx_meta.get("jpy_alert"),
        yen_not_bearish=bool(fx_meta.get("yen_up_not_bearish")),
        rate_call=rate_call if rate_call.get("ok") else None,
        fsi=fsi if fsi.get("ok") else None,
        rates_alert=rates_alert,
    )

    out = {
        "ok": True,
        "updated_at": _now(),
        "version": 5,
        "as_of": month_key,
        "as_of_cutoff": cutoff,
        "as_of_mode": "month" if month_key else "latest",
        "as_of_label": f"{month_key} 月末" if month_key else "最新",
        "definition": (
            "Macro Risk = 通胀22% + 就业/经济23% + 美债/利率25% + 外汇/日元20% + 信用10%；"
            "外汇拆成 DXY5% + USDJPY6% + JPY Carry6% + 美元流动性3%；"
            "JPY Carry = 0.35动量 + 0.25波动 + 0.20美日利差 + 0.20风险资产压力；"
            "日元升值本身不等于利空。Final = clip(Base + 共振惩罚, 0, 100)。"
            "利率路径：市场隐含（Polymarket/Kalshi/ZQ）对照泰勒规则反应函数。"
            "FSI=住房/信用/银行/利率持续/日元Carry/利率×杠杆；10Y 高不等于次贷危机。"
        ),
        "macro_risk": _round(macro_risk, 1),
        "base_risk": _round(base_risk, 1),
        "macro_risk_band": _band(macro_risk),
        "macro_direction": direction,
        "resonance": reso,
        "sources": sources_ui,
        "modules": modules,
        "pillars": {
            "inflation_risk": infl_mod.get("risk"),
            "growth_risk": growth_mod.get("risk"),
            "rates_risk": tsy_mod.get("risk"),
            "fx_risk": fx_mod.get("risk"),
            "jpy_carry_risk": sources.get("jpy_carry"),
            "credit_risk": credit_mod.get("risk"),
        },
        "jpy_alert": fx_meta.get("jpy_alert"),
        "rates_alert": rates_alert,
        "alerts": alerts,
        "rate_call": rate_call,
        "financial_stress": fsi,
        "market_snapshot": {k: v for k, v in fx_meta.items() if k != "jpy_alert"},
        "regime": regime,
        "regime_label": regime_label,
        "narrative": narrative,
        "drivers": _drivers(all_leaves, tsy_meta=tsy_meta, rates_alert=rates_alert),
        "equity_risk": equity,
        "mc_scenario": mc,
        "asset_hints": asset_hints,
        "weights": {**PILLAR_W, "leaves": LEAF_W},
        "consensus_overrides": bool(consensus),
        "note": (
            f"回测截止 {cutoff}（数据所属期，不是公布日）。历史月份不混入当前 FOMC 市场定价。"
            if historical else
            "风险环境分数，非买卖信号。不要把日元升值或 DXY 上涨单独解读为利空。"
        ),
        "growth_note": (
            "NFP 不能单独解释：NFP↓+工资↓+失业率↑ 与 NFP↓+工资仍强+失业率稳定 是两种宏观环境。"
        ),
    }
    _write_cache(result_cache, out)
    return out
