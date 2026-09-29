"""三维挂谷预算模型（工程近似）。

思想映射（非定理搬运）：
- 方向 = 必须覆盖的风险情景（涨/跌/波动/跳空/去漂移…）
- 细管 = 该情景下的收益路径族
- 维数满 = 启用方向上损失可控的比例要高；不能只看一个中位「赚」

用户可配置各方向冲击大小；输出方向覆盖表 + 完备性评分。
"""

from __future__ import annotations

import math
import random
from statistics import mean
from typing import Any

from app.services.budget_models import BudgetModelError, _dated_returns_list
from app.services.risk import _percentile, _round


# 默认可配置方向（冲击单位见 kind）
# family: base | market | crisis —— 前端分组；crisis =「向所有方向旋转」的极端管
DIRECTION_SPECS: list[dict[str, Any]] = [
    {
        "id": "base",
        "label": "基准（无冲击）",
        "kind": "none",
        "family": "base",
        "unit": "—",
        "default": 0.0,
        "enabled_default": True,
        "description": "历史块重抽样，不加方向冲击。",
    },
    {
        "id": "spot_up",
        "label": "现货上行",
        "kind": "drift",
        "family": "market",
        "unit": "持仓期累计收益",
        "default": 0.05,
        "enabled_default": True,
        "description": "把样本漂移抬到约目标累计收益（方向管：上涨）。",
    },
    {
        "id": "spot_down",
        "label": "现货下行",
        "kind": "drift",
        "family": "market",
        "unit": "持仓期累计收益",
        "default": -0.05,
        "enabled_default": True,
        "description": "把样本漂移压到约目标累计收益（方向管：下跌）。",
    },
    {
        "id": "vol_spike",
        "label": "波动跳升",
        "kind": "vol_mult",
        "family": "market",
        "unit": "σ 倍数",
        "default": 1.8,
        "enabled_default": True,
        "description": "去均值后放大波动（方向管：波动维度）。",
    },
    {
        "id": "gap_down",
        "label": "跳空低开",
        "kind": "gap",
        "family": "market",
        "unit": "首日缺口",
        "default": -0.04,
        "enabled_default": True,
        "description": "路径第 1 日叠加跳空（方向管：缺口）。",
    },
    {
        "id": "gap_up",
        "label": "跳空高开",
        "kind": "gap",
        "family": "market",
        "unit": "首日缺口",
        "default": 0.03,
        "enabled_default": False,
        "description": "路径第 1 日向上跳空。",
    },
    {
        "id": "zero_drift",
        "label": "去漂移 μ=0",
        "kind": "demean",
        "family": "market",
        "unit": "开关(1=开)",
        "default": 1.0,
        "enabled_default": True,
        "description": "去掉样本均值，只保留波动结构（风险视角管）。",
    },
    {
        "id": "bear_vol",
        "label": "下跌+高波动",
        "kind": "combo_bear_vol",
        "family": "market",
        "unit": "跌幅；可调 σ",
        "default": -0.08,
        "default_vol": 2.0,
        "enabled_default": True,
        "description": "同时施加下行漂移与波动放大。",
    },
    # —— 极端危机：全天候「随机方向」——
    {
        "id": "liquidity_crunch",
        "label": "流动性枯竭",
        "kind": "crisis_bundle",
        "family": "crisis",
        "unit": "冲击强度(缺口近似)",
        "default": -0.10,
        "default_vol": 2.8,
        "default_gap": -0.08,
        "default_left_tail": 1.6,
        "enabled_default": True,
        "description": "大缺口 + 高波动 + 左尾加厚：买卖价差扩大、砸盘难出的近似。",
    },
    {
        "id": "hyperinflation",
        "label": "恶性通胀",
        "kind": "crisis_bundle",
        "family": "crisis",
        "unit": "实际收益冲击",
        "default": -0.12,
        "default_vol": 2.2,
        "default_gap": -0.03,
        "default_left_tail": 1.3,
        "enabled_default": True,
        "description": "实际回报被通胀侵蚀：偏负漂移 + 波动抬升（成长/久期资产压力）。",
    },
    {
        "id": "geopolitical",
        "label": "地缘政治爆雷",
        "kind": "crisis_bundle",
        "family": "crisis",
        "unit": "首日冲击强度",
        "default": -0.09,
        "default_vol": 2.5,
        "default_gap": -0.07,
        "default_left_tail": 1.4,
        "enabled_default": True,
        "description": "突发风险溢价：跳空下跌 + 持续高波动（避险抛售管）。",
    },
    {
        "id": "rate_spike",
        "label": "利率急升",
        "kind": "crisis_bundle",
        "family": "crisis",
        "unit": "估值压缩跌幅",
        "default": -0.10,
        "default_vol": 2.0,
        "default_gap": -0.04,
        "default_left_tail": 1.2,
        "enabled_default": True,
        "description": "折现率上冲：久期资产杀估值，偏负漂移 + 中高波动。",
    },
    {
        "id": "credit_stress",
        "label": "信用危机",
        "kind": "crisis_bundle",
        "family": "crisis",
        "unit": "利差冲击代理",
        "default": -0.11,
        "default_vol": 2.6,
        "default_gap": -0.06,
        "default_left_tail": 1.5,
        "enabled_default": True,
        "description": "信用利差走阔 / 融资断裂：左尾加厚 + 缺口（去杠杆管）。",
    },
    {
        "id": "stagflation",
        "label": "滞胀",
        "kind": "crisis_bundle",
        "family": "crisis",
        "unit": "增长+物价双杀",
        "default": -0.10,
        "default_vol": 2.3,
        "default_gap": -0.03,
        "default_left_tail": 1.35,
        "enabled_default": True,
        "description": "增长下行叠加通胀黏性：持续负漂移与抬升波动。",
    },
    {
        "id": "policy_shock",
        "label": "政策/监管骤变",
        "kind": "crisis_bundle",
        "family": "crisis",
        "unit": "事件冲击",
        "default": -0.08,
        "default_vol": 2.4,
        "default_gap": -0.06,
        "default_left_tail": 1.4,
        "enabled_default": False,
        "description": "关税/制裁/行业监管等离散事件：缺口主导 + 波动余波。",
    },
    {
        "id": "bank_run",
        "label": "金融系统挤兑",
        "kind": "crisis_bundle",
        "family": "crisis",
        "unit": "系统风险强度",
        "default": -0.15,
        "default_vol": 3.2,
        "default_gap": -0.10,
        "default_left_tail": 1.8,
        "enabled_default": False,
        "description": "系统流动性与置信崩溃：极端缺口 + 极厚左尾（2020/SVB 类代理）。",
    },
]


def list_kakeya_directions() -> list[dict]:
    return [dict(x) for x in DIRECTION_SPECS]


def _f(shock: dict, key: str, fallback: float) -> float:
    raw = shock.get(key)
    if raw is None or raw == "":
        return float(fallback)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(fallback)


def _block_paths_with_shock(
    returns: list[float],
    *,
    horizon_days: int,
    n_sims: int,
    shock: dict,
    block_size: int = 10,
    seed: int = 42,
) -> tuple[list[float], list[float]]:
    """返回终值倍数、路径 MDD。"""
    sim = list(returns)
    mu = mean(sim) if sim else 0.0
    kind = shock.get("kind") or "none"
    value = _f(shock, "value", shock.get("default", 0.0) or 0.0)
    vol_extra = _f(shock, "vol_mult", shock.get("default_vol", 1.8) or 1.8)
    gap_cfg = _f(shock, "gap", shock.get("default_gap", 0.0) or 0.0)
    left_tail = _f(shock, "left_tail", shock.get("default_left_tail", 1.0) or 1.0)

    use = list(sim)
    daily_shift = 0.0
    gap = 0.0

    if kind == "demean":
        use = [r - mu for r in sim]
    elif kind == "vol_mult":
        scale = max(0.2, value if value > 0 else 1.0)
        use = [(r - mu) * scale for r in sim]
    elif kind == "drift":
        target = math.log(max(1.0 + value, 1e-6)) if value > -0.95 else value
        base = mu * horizon_days
        daily_shift = (target - base) / max(horizon_days, 1)
        use = list(sim)
    elif kind == "combo_bear_vol":
        target = math.log(max(1.0 + value, 1e-6)) if value > -0.95 else value
        daily_shift = target / max(horizon_days, 1)
        scale = max(0.2, vol_extra)
        use = [(r - mu) * scale for r in sim]
    elif kind == "gap":
        use = list(sim)
        gap = value
    elif kind == "crisis_bundle":
        # 危机管：负漂移(value) + 波动放大 + 首日缺口 + 左尾加厚
        target = math.log(max(1.0 + value, 1e-6)) if value > -0.95 else value
        daily_shift = target / max(horizon_days, 1)
        scale = max(0.2, vol_extra)
        lt = max(1.0, left_tail)
        centered = [r - mu for r in sim]
        use = []
        for r in centered:
            x = r * scale
            if x < 0:
                x *= lt
            use.append(x)
        gap = gap_cfg if gap_cfg != 0 else min(value * 0.6, -0.02)
    else:
        use = list(sim)

    n = len(use)
    hz = max(1, int(horizon_days))
    bs = max(2, min(int(block_size), max(2, n // 5)))
    max_start = max(0, n - bs)
    rng = random.Random(seed + hash(str(shock.get("id"))) % 10007)
    terminals: list[float] = []
    mdds: list[float] = []

    for _ in range(max(200, int(n_sims))):
        path: list[float] = []
        while len(path) < hz:
            st = rng.randint(0, max_start) if max_start > 0 else 0
            path.extend(use[st : st + bs])
        path = path[:hz]
        if daily_shift:
            path = [r + daily_shift for r in path]
        if gap and path:
            path = list(path)
            path[0] = path[0] + (math.log(max(1.0 + gap, 1e-6)) if gap > -0.95 else gap)

        v = 1.0
        peak = 1.0
        mdd = 0.0
        for r in path:
            v *= math.exp(r)
            if v > peak:
                peak = v
            dd = 1.0 - v / peak if peak > 0 else 0.0
            if dd > mdd:
                mdd = dd
        terminals.append(v)
        mdds.append(mdd)
    return terminals, mdds


def _summarize_direction(
    terminals: list[float],
    mdds: list[float],
    *,
    capital: float,
    loss_limit: float,
) -> dict:
    n = len(terminals)
    ts = sorted(terminals)
    ms = sorted(mdds)
    med = _percentile(ts, 0.50)
    p05 = _percentile(ts, 0.05)
    p95 = _percentile(ts, 0.95)
    med_pnl = (med - 1.0) * capital
    p05_pnl = (p05 - 1.0) * capital
    p95_pnl = (p95 - 1.0) * capital
    mdd_p50 = _percentile(ms, 0.50)
    mdd_p90 = _percentile(ms, 0.90)
    lim = abs(float(loss_limit)) * capital
    # 管「可控」：5%分位亏损不超过亏损底线，且路径 MDD P90 不超过 1.5×底线
    covered = (p05_pnl >= -lim - 1e-9) and (mdd_p90 <= abs(loss_limit) * 1.5 + 1e-9)
    fragile = (not covered) and (p05_pnl >= -lim * 2)
    return {
        "median_mult": _round(med, 6),
        "median_pnl": _round(med_pnl, 2),
        "p05_pnl": _round(p05_pnl, 2),
        "p95_pnl": _round(p95_pnl, 2),
        "prob_profit": _round(sum(1 for t in terminals if t > 1.0) / n, 4),
        "mdd_p50": _round(mdd_p50, 4),
        "mdd_p90": _round(mdd_p90, 4),
        "covered": covered,
        "fragile": fragile,
        "status": "覆盖" if covered else ("脆弱" if fragile else "缺口"),
    }


def _merge_enabled_shocks(shocks: list[dict] | None) -> list[dict]:
    by_id = {d["id"]: dict(d) for d in DIRECTION_SPECS}
    for raw in shocks or []:
        sid = str((raw or {}).get("id") or "").strip()
        if sid not in by_id:
            continue
        item = by_id[sid]
        if "enabled" in (raw or {}):
            item["enabled"] = bool(raw["enabled"])
        else:
            item["enabled"] = bool(item.get("enabled_default", True))
        if raw.get("value") is not None and raw.get("value") != "":
            try:
                item["value"] = float(raw["value"])
            except (TypeError, ValueError):
                item["value"] = item.get("default", 0.0)
        else:
            item["value"] = item.get("default", 0.0)
        if raw.get("vol_mult") is not None and raw.get("vol_mult") != "":
            try:
                item["vol_mult"] = float(raw["vol_mult"])
            except (TypeError, ValueError):
                item["vol_mult"] = item.get("default_vol", 1.8)
        else:
            item["vol_mult"] = item.get("default_vol", 1.8)
        for opt_key, def_key in (("gap", "default_gap"), ("left_tail", "default_left_tail")):
            if raw.get(opt_key) is not None and raw.get(opt_key) != "":
                try:
                    item[opt_key] = float(raw[opt_key])
                except (TypeError, ValueError):
                    item[opt_key] = item.get(def_key, 0.0 if opt_key == "gap" else 1.0)
            elif def_key in item:
                item[opt_key] = item.get(def_key)

    for item in by_id.values():
        if "enabled" not in item:
            item["enabled"] = bool(item.get("enabled_default", True))
        if "value" not in item:
            item["value"] = item.get("default", 0.0)
        if "vol_mult" not in item:
            item["vol_mult"] = item.get("default_vol", 1.8)
        if "gap" not in item and "default_gap" in item:
            item["gap"] = item.get("default_gap")
        if "left_tail" not in item and "default_left_tail" in item:
            item["left_tail"] = item.get("default_left_tail")

    enabled = [by_id[k] for k in by_id if by_id[k].get("enabled")]
    if not enabled:
        raise BudgetModelError("请至少启用一个方向冲击")
    return enabled


def _capital_adequacy(directions: list[dict], *, capital: float, loss_limit: float) -> dict:
    """危机资本充实度：可投本金 ≤ 亏损底线 / 最狠危机管 |P05亏损率|。"""
    crisis = [d for d in directions if d.get("family") == "crisis"]
    pool = crisis if crisis else directions
    worst_frac = 0.0
    worst_id = None
    worst_label = None
    for d in pool:
        p05 = float(d.get("p05_pnl") or 0.0)
        frac = abs(min(0.0, p05)) / capital if capital > 0 else 0.0
        if frac > worst_frac:
            worst_frac = frac
            worst_id = d.get("id")
            worst_label = d.get("label")
    if worst_frac < 1e-9:
        investable = 1.0
    else:
        # f * worst_frac * equity <= loss_limit * equity
        investable = min(1.0, loss_limit / worst_frac)
    gate_ok = investable >= 0.55 and (not crisis or sum(1 for d in crisis if d.get("covered")) / max(1, len(crisis)) >= 0.35)
    return {
        "worst_direction": worst_id,
        "worst_label": worst_label,
        "worst_p05_loss_frac": _round(worst_frac, 6),
        "loss_limit": loss_limit,
        "investable_ratio": _round(investable, 4),
        "cash_buffer_ratio": _round(max(0.0, 1.0 - investable), 4),
        "investable_capital": _round(capital * investable, 2),
        "formula": "可投本金 ≤ 总资本 × min(1, 亏损底线 / max_危机|P05亏损率|)",
        "gate_ok": gate_ok,
        "gate_hint": (
            None
            if gate_ok
            else "危机资本充实度不足：不宜把展望中位数当可投利润预算；请减仓或加对冲后再存入风险组合。"
        ),
        "note": (
            f"最狠危机管「{worst_label or worst_id}」P05亏损率约 {worst_frac * 100:.1f}%；"
            f"在亏损底线 {loss_limit * 100:.1f}% 下，建议可投不超过总资本的 {investable * 100:.0f}% "
            f"（约 {capital * investable:,.0f}），其余作现金缓冲。"
            if worst_frac >= 1e-9
            else "危机管下未见显著左尾，可投比例接近满仓（仍须人工复核）。"
        ),
    }


def _score_return_bundle(
    rets: list[float],
    *,
    enabled: list[dict],
    horizon_days: int,
    n_sims: int,
    capital: float,
    loss_limit: float,
    label: str,
) -> dict:
    directions = []
    for shock in enabled:
        terms, mdds = _block_paths_with_shock(
            rets,
            horizon_days=horizon_days,
            n_sims=n_sims,
            shock=shock,
        )
        summary = _summarize_direction(terms, mdds, capital=capital, loss_limit=loss_limit)
        directions.append({
            "id": shock["id"],
            "label": shock["label"],
            "kind": shock["kind"],
            "family": shock.get("family") or "market",
            "value": shock.get("value"),
            "vol_mult": shock.get("vol_mult"),
            "gap": shock.get("gap"),
            "left_tail": shock.get("left_tail"),
            "unit": shock.get("unit"),
            "description": shock.get("description"),
            **summary,
        })

    n_dir = len(directions)
    n_cov = sum(1 for d in directions if d["covered"])
    n_gap = sum(1 for d in directions if d["status"] == "缺口")
    n_crisis = sum(1 for d in directions if d.get("family") == "crisis")
    n_crisis_cov = sum(1 for d in directions if d.get("family") == "crisis" and d["covered"])
    dim_score = n_cov / n_dir if n_dir else 0.0
    crisis_score = n_crisis_cov / n_crisis if n_crisis else None

    hz2 = max(5, horizon_days // 2)
    short_cov = 0
    for shock in enabled:
        t2, m2 = _block_paths_with_shock(
            rets, horizon_days=hz2, n_sims=max(600, n_sims // 2), shock=shock
        )
        s2 = _summarize_direction(t2, m2, capital=capital, loss_limit=loss_limit)
        if s2["covered"]:
            short_cov += 1
    short_score = short_cov / n_dir if n_dir else 0.0
    scale_consistency = 1.0 - abs(dim_score - short_score)

    if dim_score >= 0.8:
        verdict = "维数较满"
        hint = "启用方向上多数可控：情景覆盖相对完整，仍须看缺口方向。"
    elif dim_score >= 0.5:
        verdict = "维数不足"
        hint = "半数方向脆弱/缺口：中位赚钱不能代表风险管已盖住。"
    else:
        verdict = "方向缺口大"
        hint = "多数压力方向不可控：不宜按单一「赚」结论建仓。"
    if n_crisis and (crisis_score or 0) < 0.35:
        hint += " 极端危机管覆盖偏低——全天候维度仍缺。"

    capital_need = _capital_adequacy(directions, capital=capital, loss_limit=loss_limit)

    return {
        "label": label,
        "directions": directions,
        "dim_score": _round(dim_score, 4),
        "crisis_score": None if crisis_score is None else _round(crisis_score, 4),
        "crisis_count": n_crisis,
        "crisis_covered": n_crisis_cov,
        "covered_count": n_cov,
        "gap_count": n_gap,
        "direction_count": n_dir,
        "short_horizon_days": hz2,
        "short_dim_score": _round(short_score, 4),
        "scale_consistency": _round(scale_consistency, 4),
        "verdict": verdict,
        "hint": hint,
        "capital_need": capital_need,
    }


def _dated_returns_map(symbol: str, window: int) -> tuple[dict[str, float], float, str]:
    """date -> log return, plus last price and source."""
    from app.services.ohlc import OhlcError, fetch_closes
    from app.services.portfolio import _bar_dated_returns

    try:
        ohlc = fetch_closes(symbol, "1d", apply_live=True)
    except OhlcError as exc:
        raise BudgetModelError(str(exc)) from exc
    dated = _bar_dated_returns(ohlc.get("ohlc_bars") or [], window)
    if len(dated) < 30:
        raise BudgetModelError(f"{symbol}: 有效收益样本不足")
    px = ohlc.get("price")
    closes = ohlc.get("closes") or []
    price = float(px) if px is not None else (float(closes[-1]) if closes else 0.0)
    if price <= 0:
        raise BudgetModelError(f"{symbol}: 无有效现价")
    return dated, price, str(ohlc.get("source") or "")


def _parse_kakeya_legs(legs: list[dict] | None) -> list[dict]:
    if not legs:
        return []
    parsed: list[dict] = []
    for raw in legs:
        sym = str((raw or {}).get("symbol") or "").strip().upper()
        if not sym:
            continue
        side = str((raw or {}).get("side") or "long").strip().lower()
        if side not in ("long", "short"):
            side = "long"
        sign = 1.0 if side == "long" else -1.0
        w = (raw or {}).get("weight")
        amt = (raw or {}).get("amount")
        weight = None
        if w is not None and w != "":
            try:
                weight = abs(float(w))
            except (TypeError, ValueError):
                weight = None
        if weight is None and amt is not None and amt != "":
            try:
                weight = abs(float(amt))
            except (TypeError, ValueError):
                weight = None
        if weight is None or weight <= 0:
            weight = 1.0
        parsed.append({"symbol": sym, "side": side, "sign": sign, "raw_weight": weight})
    if len(parsed) > 12:
        raise BudgetModelError("组合腿一次最多 12 条")
    if not parsed:
        raise BudgetModelError("请填写有效组合腿")
    gross = sum(p["raw_weight"] for p in parsed) or 1.0
    for p in parsed:
        p["weight"] = p["sign"] * (p["raw_weight"] / gross)
    return parsed


def _portfolio_returns_from_legs(legs: list[dict], window: int) -> tuple[list[float], list[str], list[dict], dict]:
    maps: dict[str, dict[str, float]] = {}
    meta: list[dict] = []
    for leg in legs:
        dated, px, src = _dated_returns_map(leg["symbol"], window)
        maps[leg["symbol"]] = dated
        meta.append({
            "symbol": leg["symbol"],
            "side": leg["side"],
            "weight": _round(leg["weight"], 6),
            "weight_abs": _round(abs(leg["weight"]), 6),
            "spot_price": _round(px, 4),
            "source": src,
        })
    common = set.intersection(*(set(m.keys()) for m in maps.values()))
    if len(common) < 40:
        raise BudgetModelError("组合腿历史日期对齐后样本不足，请缩短窗口或检查代码")
    dates = sorted(common)
    rets = [
        sum(maps[leg["symbol"]][d] * leg["weight"] for leg in legs)
        for d in dates
    ]
    return rets, dates, meta, {"n_days": len(dates), "start": dates[0], "end": dates[-1]}


def _hedge_vs_benchmark(
    port_rets: list[float],
    dates: list[str],
    *,
    window: int,
    benchmark: str = "SPY",
) -> tuple[list[float], dict]:
    """相对基准去 beta：残差 = r_p - β r_b（针：市场方向体积压缩）。"""
    bench_map, _, _ = _dated_returns_map(benchmark, window)
    pairs = [(port_rets[i], bench_map[d]) for i, d in enumerate(dates) if d in bench_map]
    if len(pairs) < 40:
        raise BudgetModelError(f"对冲基准 {benchmark} 与组合对齐后样本不足")
    p = [x[0] for x in pairs]
    bb = [x[1] for x in pairs]
    n = len(p)
    mean_p = sum(p) / n
    mean_b = sum(bb) / n
    var_b = sum((x - mean_b) ** 2 for x in bb) / n
    if var_b < 1e-16:
        beta = 0.0
    else:
        cov = sum((p[i] - mean_p) * (bb[i] - mean_b) for i in range(n)) / n
        beta = cov / var_b
    residual = [p[i] - beta * bb[i] for i in range(n)]
    return residual, {
        "benchmark": benchmark,
        "beta": _round(beta, 4),
        "n_days": n,
        "note": "对冲后序列 = 组合收益 − β×基准收益；衡量去掉系统性市场暴露后的针状残差。",
    }


def run_kakeya_model(
    *,
    symbols: list[str] | None = None,
    symbol: str | None = None,
    legs: list[dict] | None = None,
    hedge: bool = True,
    hedge_benchmark: str = "SPY",
    horizon_days: int = 21,
    capital: float = 10000.0,
    window: int = 252,
    n_sims: int = 2000,
    loss_limit: float = 0.05,
    shocks: list[dict] | None = None,
) -> dict:
    hz = max(5, min(int(horizon_days or 21), 126))
    win = max(60, min(int(window or 252), 1000))
    sims = max(800, min(int(n_sims or 2000), 6000))
    cap = float(capital)
    if cap <= 0:
        raise BudgetModelError("本金须大于 0")
    lim = max(0.005, min(float(loss_limit or 0.05), 0.5))
    enabled = _merge_enabled_shocks(shocks)

    parsed_legs = _parse_kakeya_legs(legs) if legs else []

    base_meta = {
        "ok": True,
        "model": "kakeya_3d_budget",
        "model_label": "三维挂谷模型（全方向压力）",
        "horizon_days": hz,
        "window": win,
        "n_sims": sims,
        "capital": round(cap, 2),
        "loss_limit": lim,
        "definition": (
            "「向所有方向旋转」≈ 市场/危机方向管要尽量盖住。"
            "含流动性枯竭、恶性通胀、地缘爆雷、利率急升、信用危机、滞胀、政策骤变、系统挤兑等。"
            "覆盖=该管下 5%分位亏损≤亏损底线，且 MDD P90≤1.5×底线。"
            "维数评分=全部启用方向覆盖占比；危机评分=极端危机族覆盖占比。"
            "危机资本充实度：可投 ≤ 总资本 × min(1, 亏损底线 / max_危机|P05亏损率|)。"
            "组合模式可对冲前后对比（相对 SPY 去 beta）。"
        ),
        "available_directions": list_kakeya_directions(),
    }

    # —— 组合腿模式：对冲前后对比 ——
    if parsed_legs:
        port_rets, dates, leg_meta, align = _portfolio_returns_from_legs(parsed_legs, win)
        before = _score_return_bundle(
            port_rets,
            enabled=enabled,
            horizon_days=hz,
            n_sims=sims,
            capital=cap,
            loss_limit=lim,
            label="对冲前（组合净值路径）",
        )
        after = None
        hedge_meta = None
        if hedge:
            try:
                hedged_rets, hedge_meta = _hedge_vs_benchmark(
                    port_rets,
                    dates,
                    window=win,
                    benchmark=str(hedge_benchmark or "SPY").upper(),
                )
                after = _score_return_bundle(
                    hedged_rets,
                    enabled=enabled,
                    horizon_days=hz,
                    n_sims=sims,
                    capital=cap,
                    loss_limit=lim,
                    label="对冲后（去 beta 残差）",
                )
            except BudgetModelError:
                after = None
                hedge_meta = {"error": "对冲基准不可用，仅返回对冲前结果"}

        compare = None
        if after:
            compare = {
                "dim_delta": _round(after["dim_score"] - before["dim_score"], 4),
                "crisis_delta": (
                    None
                    if before["crisis_score"] is None or after["crisis_score"] is None
                    else _round(after["crisis_score"] - before["crisis_score"], 4)
                ),
                "investable_delta": _round(
                    after["capital_need"]["investable_ratio"] - before["capital_need"]["investable_ratio"],
                    4,
                ),
                "improved": (
                    after["dim_score"] >= before["dim_score"]
                    and (after["capital_need"]["investable_ratio"] >= before["capital_need"]["investable_ratio"])
                ),
                "note": (
                    "对冲后维数/可投比例上升，说明市场系统性暴露被压薄（针更细）。"
                    if (
                        after["dim_score"] >= before["dim_score"]
                        and after["capital_need"]["investable_ratio"] >= before["capital_need"]["investable_ratio"]
                    )
                    else "对冲后未必全面变好：危机管可能仍打残差；需看具体缺口方向。"
                ),
            }

        return {
            **base_meta,
            "mode": "portfolio",
            "legs": leg_meta,
            "align": align,
            "hedge": hedge_meta,
            "before": before,
            "after": after,
            "compare": compare,
            "items": [],  # 兼容旧前端
            "gate": before["capital_need"],
        }

    # —— 单票 / 多票逐个 ——
    syms: list[str] = []
    if symbol:
        syms.append(str(symbol).strip().upper())
    for s in symbols or []:
        t = str(s or "").strip().upper()
        if t and t not in syms:
            syms.append(t)
    if not syms:
        raise BudgetModelError("请填写股票代码或组合腿")
    if len(syms) > 8:
        raise BudgetModelError("一次最多 8 个代码")

    per_symbol = []
    for sym in syms:
        rets, px, src = _dated_returns_list(sym, win)
        bundle = _score_return_bundle(
            rets,
            enabled=enabled,
            horizon_days=hz,
            n_sims=sims,
            capital=cap,
            loss_limit=lim,
            label=sym,
        )
        per_symbol.append({
            "symbol": sym,
            "spot_price": _round(px, 4),
            "source": src,
            **bundle,
        })

    return {
        **base_meta,
        "mode": "symbols",
        "items": per_symbol,
        "gate": per_symbol[0]["capital_need"] if per_symbol else None,
    }
