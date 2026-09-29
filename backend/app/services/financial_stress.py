"""金融危机预警：住房压力 × 信用压力 × 利率持续，而不是「10Y 超过某阈值」。

美债利率上升本身不会自动造成次贷危机。
2008 的核心是房地产泡沫 + 次级按揭 + 高杠杆证券化 + 金融机构资产负债表脆弱。
今天存量房贷多为长期固定利率，更该看：高利率持续多久、新增贷款、商业地产、企业再融资、银行。
"""

from __future__ import annotations

import math
from typing import Any


def _round(x: float | None, n: int = 2) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), n)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _last(payload: dict | None) -> float | None:
    if not payload:
        return None
    pts = payload.get("points") or []
    if pts:
        try:
            return float(pts[-1]["value"])
        except (TypeError, ValueError, KeyError):
            pass
    closes = payload.get("closes") or []
    if closes:
        try:
            return float(closes[-1])
        except (TypeError, ValueError):
            pass
    px = payload.get("price")
    try:
        return float(px) if px is not None else None
    except (TypeError, ValueError):
        return None


def _series(payload: dict | None) -> list[float]:
    if not payload:
        return []
    pts = payload.get("points") or []
    if pts:
        out = []
        for p in pts:
            try:
                out.append(float(p["value"]))
            except (TypeError, ValueError, KeyError):
                continue
        if out:
            return out
    return [float(x) for x in (payload.get("closes") or []) if x is not None]


def _ago(xs: list[float], n: int) -> float | None:
    if len(xs) <= n:
        return None
    return float(xs[-1 - n])


def _chg(xs: list[float], n: int, *, pct: bool = True) -> float | None:
    prev = _ago(xs, n)
    if prev is None or not xs:
        return None
    cur = float(xs[-1])
    if pct:
        if prev == 0:
            return None
        return (cur / prev - 1.0) * 100.0
    return cur - prev


def _pct_n(xs: list[float], n: int) -> float | None:
    return _chg(xs, n, pct=True)


def _map(x: float | None, knots: list[tuple[float, float]]) -> float | None:
    if x is None or not knots:
        return None
    v = float(x)
    if v <= knots[0][0]:
        return knots[0][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if v <= x1:
            if x1 == x0:
                return y1
            t = (v - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return knots[-1][1]


def _blend(parts: list[tuple[float, float | None]]) -> float | None:
    num = 0.0
    den = 0.0
    for w, r in parts:
        if r is None:
            continue
        num += w * float(r)
        den += w
    if den <= 0:
        return None
    return _clip(num / den, 0.0, 100.0)


def _bank_score(kre: list[float], spy: list[float] | None) -> tuple[float | None, dict]:
    if len(kre) < 22:
        return None, {}
    chg20 = _pct_n(kre, 20)
    chg60 = _pct_n(kre, 60) if len(kre) > 60 else None
    peak = max(kre[-60:]) if len(kre) >= 60 else max(kre)
    dd = (kre[-1] / peak - 1.0) * 100 if peak else None
    rel = None
    if spy and len(spy) > 20 and len(kre) > 20:
        s20 = _pct_n(spy, 20)
        if chg20 is not None and s20 is not None:
            rel = chg20 - s20
    s20 = _map(chg20, [(5, 12), (0, 38), (-8, 62), (-18, 85), (-30, 96)])
    s60 = _map(chg60, [(8, 15), (0, 40), (-12, 65), (-25, 88)])
    sdd = _map(dd, [(-2, 20), (-8, 45), (-18, 70), (-30, 92)])
    srel = _map(rel, [(4, 15), (0, 40), (-6, 65), (-15, 88)])
    score = _blend([(0.35, s20), (0.20, s60), (0.25, sdd), (0.20, srel)])
    return score, {
        "kre": _round(kre[-1], 2),
        "chg20": _round(chg20, 2),
        "chg60": _round(chg60, 2),
        "drawdown_60": _round(dd, 2),
        "vs_spy_20d": _round(rel, 2),
    }


def _rate_duration(daily10: list[float], threshold: float = 4.0) -> dict:
    if len(daily10) < 60:
        return {"score": 40.0, "weeks_above": None, "share_1y": None}
    window = daily10[-252:] if len(daily10) >= 252 else daily10
    share = sum(1 for x in window if x >= threshold) / len(window)
    streak = 0
    for x in reversed(daily10):
        if x >= threshold:
            streak += 1
        else:
            break
    weeks = streak / 5.0
    score = _map(weeks, [(0, 12), (8, 28), (26, 48), (52, 68), (100, 86), (150, 95)])
    share_score = _map(share * 100, [(10, 15), (40, 40), (70, 65), (90, 82)])
    blended = _blend([(0.55, score), (0.45, share_score)]) or 40.0
    return {
        "score": blended,
        "weeks_above": _round(weeks, 1),
        "share_1y": _round(share * 100, 1),
        "threshold": threshold,
        "last": _round(daily10[-1], 3),
    }


def build_financial_stress(
    *,
    tsy_meta: dict,
    fred: dict,
    mkt: dict,
    jpy_carry: float | None,
    hy_risk: float | None,
    nfci: float | None,
) -> dict[str, Any]:
    ust10 = tsy_meta.get("ust10")
    ust2 = tsy_meta.get("ust2")
    daily10 = tsy_meta.get("daily10") or []
    real10 = tsy_meta.get("real10")

    mort = _series(fred.get("mort30"))
    home = _series(fred.get("home"))
    delinq = _series(fred.get("delinq"))
    tdsp = _series(fred.get("tdsp"))
    cre = _series(fred.get("cre_delinq"))
    ig = _series(fred.get("ig"))
    hy = _series(fred.get("hy"))
    kre = _series(mkt.get("KRE"))
    spy = _series(mkt.get("SPY"))

    mort_lv = mort[-1] if mort else None
    spread = (mort_lv - float(ust10)) if mort_lv is not None and ust10 is not None else None
    home_12m = _chg(home, 12 if len(home) < 400 else 252, pct=True)
    delinq_lv = delinq[-1] if delinq else None
    delinq_1y = _chg(delinq, 4 if len(delinq) < 200 else 52, pct=False)
    tdsp_lv = tdsp[-1] if tdsp else None
    cre_lv = cre[-1] if cre else None
    cre_1y = _chg(cre, 4 if len(cre) < 200 else 52, pct=False)
    ig_lv = ig[-1] if ig else None
    hy_lv = hy[-1] if hy else None
    hy_n = 21 if len(hy) > 40 else max(1, len(hy) // 8) if hy else 1
    hy_widen = _chg(hy, hy_n, pct=False) if hy else None

    s_mtg = _map(mort_lv, [(3.0, 12), (5.0, 38), (6.5, 58), (7.5, 75), (9.0, 92)])
    s_spr = _map(spread, [(1.2, 18), (1.8, 40), (2.3, 62), (3.0, 85), (3.8, 96)])
    s_px = _map(home_12m, [(8, 12), (3, 28), (0, 48), (-5, 72), (-12, 92)])
    s_del = _map(delinq_lv, [(0.8, 15), (1.7, 38), (3.0, 62), (6.0, 85), (10, 97)])
    if s_del is not None and delinq_1y is not None and delinq_1y > 0.15:
        s_del = _clip(s_del + min(12, delinq_1y * 20), 0, 100)
    s_tdsp = _map(tdsp_lv, [(9.2, 14), (10.5, 36), (11.5, 55), (12.5, 75), (13.5, 92)])

    housing = _blend([
        (0.20, s_mtg),
        (0.15, s_spr),
        (0.25, s_px),
        (0.25, s_del),
        (0.15, s_tdsp),
    ])

    s_hy = hy_risk
    if s_hy is None:
        s_hy = _map(hy_lv, [(3.0, 16), (4.0, 40), (5.5, 68), (8.0, 90)])
    s_ig = _map(ig_lv, [(0.7, 16), (1.1, 40), (1.8, 68), (3.0, 90)])
    s_cre = _map(cre_lv, [(0.4, 16), (1.2, 42), (2.5, 68), (5.0, 88), (8.0, 97)])
    if s_cre is not None and cre_1y is not None and cre_1y > 0.2:
        s_cre = _clip(s_cre + min(14, cre_1y * 18), 0, 100)
    s_bank, bank_meta = _bank_score(list(kre), list(spy) if spy else None)

    s_2y = _map(ust2, [(2.5, 12), (3.5, 30), (4.5, 55), (5.5, 78), (6.5, 92)])
    s_hyw = _map(hy_widen, [(-0.4, 12), (0.0, 38), (0.4, 62), (1.0, 85)])
    refi = _blend([(0.55, s_2y), (0.25, s_ig), (0.20, s_hyw)])

    credit = _blend([
        (0.28, s_hy),
        (0.14, s_ig),
        (0.24, s_cre),
        (0.18, s_bank),
        (0.16, refi),
    ])

    s_10y = _map(ust10, [(2.5, 14), (3.5, 32), (4.5, 55), (5.5, 78), (6.5, 92)])
    s_real = _map(real10, [(0.5, 18), (1.5, 40), (2.2, 62), (3.0, 85)])
    rate_level = _blend([(0.55, s_10y), (0.45, s_real)])
    duration = _rate_duration(list(daily10))

    lev = _blend([(0.4, s_del), (0.25, s_tdsp), (0.35, s_cre)])
    rate_hot = (rate_level or 0) >= 50 or (mort_lv is not None and mort_lv >= 6.0)
    lev_hot = (lev or 0) >= 55 or (housing or 0) >= 58 or (credit or 0) >= 58
    credit_widening = (hy_widen is not None and hy_widen > 0.25) or (s_hy or 0) >= 60
    if rate_hot and lev_hot and credit_widening:
        interaction = _clip(55 + 0.35 * (lev or 50), 60, 96)
        interaction_note = "高利率、杠杆压力与信用利差走阔同时出现，连锁反应通道打开。"
    elif rate_hot and lev_hot:
        interaction = _clip(42 + 0.25 * (lev or 50), 40, 78)
        interaction_note = "利率高且偿债/地产杠杆升温，但信用利差尚未失控。"
    elif rate_hot and not lev_hot:
        interaction = 16.0
        interaction_note = "利率偏高，但家庭/银行杠杆与逾期仍可控——更像利率正常化，不是次贷重演。"
    else:
        interaction = 12.0
        interaction_note = "利率与杠杆都未同时过热。"

    jpy = float(jpy_carry) if jpy_carry is not None else None
    fsi_raw = _blend([
        (0.10, rate_level),
        (0.10, duration.get("score")),
        (0.20, housing),
        (0.22, credit),
        (0.12, s_bank),
        (0.12, jpy),
        (0.14, interaction),
    ])
    fsi = fsi_raw if fsi_raw is not None else 40.0

    dampen = False
    if rate_hot and (housing or 0) < 42 and (credit or 0) < 42 and not credit_widening:
        fsi = _clip(fsi * 0.78, 0, 100)
        dampen = True
    if (housing or 0) >= 65 and (credit or 0) >= 60:
        fsi = _clip(fsi + 12, 0, 100)

    if fsi >= 78 and (s_bank or 0) >= 68:
        regime = "financial_crisis"
        answer = "会：已接近金融危机式连锁（住房 + 信用 + 银行同时承压）。"
        call = "金融危机预警"
        emoji = "🔴"
    elif fsi >= 62 or ((housing or 0) >= 60 and (credit or 0) >= 55):
        regime = "credit_contraction"
        answer = "正在从高利率环境向信用收缩演变，还不是 2008 式次贷崩塌，但连锁已经启动。"
        call = "信用/住房压力"
        emoji = "🟠"
    elif fsi >= 45:
        regime = "watch"
        answer = "高利率在增加融资成本，但房地产杠杆与信用利差尚未共振。美债利率上升本身还不是次贷危机。"
        call = "需观察"
        emoji = "🟡"
    else:
        regime = "normal"
        answer = "不会。当前更像利率正常化：家庭逾期与企业信用仍稳，固定利率存量房贷切断了「利率↑→立刻大规模违约」的 2008 机械链。"
        call = "不像次贷危机"
        emoji = "🟢"

    return {
        "ok": True,
        "score": _round(fsi, 1),
        "call": call,
        "emoji": emoji,
        "regime": regime,
        "answer": answer,
        "headline": "美债利率上升本身不会自动造成次贷危机。危险的是高利率通过房地产、信用和银行资产负债表形成连锁。",
        "note": (
            "2008 的核心不是「10Y 太高」，而是泡沫 + 次级按揭 + 高杠杆证券化 + 脆弱的金融机构。"
            "今天美国大量存量房贷是长期固定利率，因此美债↑≠房贷立刻违约。"
            "更该跟踪：高利率持续多久、新增贷款质量、商业地产、企业再融资、银行。"
        ),
        "dampen_high_rate_only": dampen,
        "interaction_note": interaction_note,
        "pillars": [
            {"id": "rate_level", "label": "Rate Level", "zh": "利率水平", "score": _round(rate_level, 1)},
            {"id": "duration", "label": "Duration", "zh": "高利率持续", "score": _round(duration.get("score"), 1)},
            {"id": "housing", "label": "Housing", "zh": "房地产", "score": _round(housing, 1)},
            {"id": "credit", "label": "Credit", "zh": "信用/再融资", "score": _round(credit, 1)},
            {"id": "bank", "label": "Bank", "zh": "银行", "score": _round(s_bank, 1)},
            {"id": "jpy", "label": "JPY Carry", "zh": "日元套利", "score": _round(jpy, 1)},
            {"id": "interaction", "label": "Rates × Leverage", "zh": "利率×杠杆", "score": _round(interaction, 1)},
        ],
        "housing": {
            "score": _round(housing, 1),
            "mortgage_30y": _round(mort_lv, 3),
            "mortgage_spread": _round(spread, 3),
            "home_price_12m": _round(home_12m, 2),
            "delinquency": _round(delinq_lv, 3),
            "delinquency_1y": _round(delinq_1y, 3),
            "tdsp": _round(tdsp_lv, 3),
        },
        "credit": {
            "score": _round(credit, 1),
            "hy_oas": _round(hy_lv, 3),
            "ig_oas": _round(ig_lv, 3),
            "cre_delinquency": _round(cre_lv, 3),
            "cre_delinquency_1y": _round(cre_1y, 3),
            "refi": _round(refi, 1),
            "hy_widen": _round(hy_widen, 3),
        },
        "bank": {"score": _round(s_bank, 1), **bank_meta},
        "rates": {
            "ust10": _round(float(ust10), 3) if ust10 is not None else None,
            "ust2": _round(float(ust2), 3) if ust2 is not None else None,
            "real10": _round(float(real10), 3) if real10 is not None else None,
            "nfci": _round(nfci, 3) if nfci is not None else None,
            **duration,
        },
        "chain": [
            "美债收益率 ↑ → 融资成本 ↑ → 房贷/企业债/消费贷利率 ↑",
            "房地产需求 ↓ → 房价压力 ↑；若杠杆高 → 违约 ↑ → MBS/银行资产 ↓",
            "信用利差扩大 → 银行收紧贷款 → 企业再融资困难 + 消费下降",
            "只有「利率高 + 维持很久 + 地产杠杆高 + 偿债能力下降 + 利差走阔」同时出现，才接近系统性风险。",
        ],
    }
