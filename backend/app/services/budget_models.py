"""预算模型：持仓 N 天赚/亏展望。

可插拔注册表。当前默认用组合概率展望里的 Block Bootstrap；
后续可继续往 MODELS 里加新模型，前端「预算模型」页与组合测算下拉共用。
"""

from __future__ import annotations

from typing import Any

from app.services.portfolio import PortfolioError, analyze_portfolio


class BudgetModelError(Exception):
    pass


def _pick_block(forecast: dict, model_id: str) -> dict:
    key_map = {
        "block_bootstrap": "block_bootstrap",
        "bootstrap": "bootstrap",
        "gbm": "gbm",
        "garch_t": "garch_t",
    }
    key = key_map.get(model_id, "block_bootstrap")
    block = (forecast or {}).get(key) or {}
    if block.get("median_value") is not None:
        return block
    # fallback chain
    for k in ("block_bootstrap", "bootstrap", "gbm", "garch_t"):
        b = (forecast or {}).get(k) or {}
        if b.get("median_value") is not None:
            return b
    return block


def _run_via_portfolio(
    legs: list[dict],
    *,
    model_id: str,
    capital: float,
    horizon_days: int,
    window: int,
    n_sims: int,
    drift_mode: str,
    loss_limit: float,
) -> dict:
    analysis = analyze_portfolio(
        legs,
        window=window,
        confidence=0.95,
        capital=capital,
        horizon_days=horizon_days,
        n_sims=n_sims,
        drift_mode=drift_mode,
        loss_limit=loss_limit,
    )
    fc = analysis.get("forecast") or {}
    block = _pick_block(fc, model_id)
    return {"analysis": analysis, "forecast": fc, "block": block}


def _meta_block_bootstrap() -> dict:
    return {
        "id": "block_bootstrap",
        "name": "Block Bootstrap",
        "label": "Block 概率展望（默认）",
        "description": "按历史收益块重抽样，估算持仓 N 个交易日后的中位盈亏与盈利概率。",
        "supports_multi": True,
        "default": True,
    }


def _meta_bootstrap() -> dict:
    return {
        "id": "bootstrap",
        "name": "Bootstrap",
        "label": "日收益 Bootstrap",
        "description": "有放回抽取历史日收益叠乘 N 天。",
        "supports_multi": True,
        "default": False,
    }


def _meta_gbm() -> dict:
    return {
        "id": "gbm",
        "name": "GBM",
        "label": "GBM 参数法",
        "description": "对数收益近似正态，用 μ/σ 解析展望终值分布。",
        "supports_multi": True,
        "default": False,
    }


def _meta_garch_t() -> dict:
    return {
        "id": "garch_t",
        "name": "GARCH-t",
        "label": "GARCH-t 厚尾",
        "description": "GARCH(1,1)+Student-t，强调波动聚集与厚尾。",
        "supports_multi": True,
        "default": False,
    }


# 后续加模型：往这里登记 id → (meta_fn, runner)
MODELS: dict[str, dict[str, Any]] = {
    "block_bootstrap": {"meta": _meta_block_bootstrap, "runner": "portfolio"},
    "bootstrap": {"meta": _meta_bootstrap, "runner": "portfolio"},
    "gbm": {"meta": _meta_gbm, "runner": "portfolio"},
    "garch_t": {"meta": _meta_garch_t, "runner": "portfolio"},
}


def list_budget_models() -> list[dict]:
    rows = []
    for mid, spec in MODELS.items():
        meta = spec["meta"]()
        meta["id"] = mid
        rows.append(meta)
    rows.sort(key=lambda x: (not x.get("default"), x.get("label") or x["id"]))
    return rows


def get_budget_model(model_id: str | None) -> dict:
    mid = (model_id or "block_bootstrap").strip() or "block_bootstrap"
    if mid not in MODELS:
        raise BudgetModelError(f"未知预算模型：{mid}。可选：{', '.join(MODELS)}")
    meta = MODELS[mid]["meta"]()
    meta["id"] = mid
    return meta


def _normalize_legs(
    symbols: list[str] | None,
    legs: list[dict] | None,
) -> list[dict]:
    if legs:
        out = []
        for raw in legs:
            sym = str((raw or {}).get("symbol") or "").strip().upper()
            if not sym:
                continue
            side = str((raw or {}).get("side") or "long").strip().lower()
            if side in ("short", "做空"):
                side = "short"
            else:
                side = "long"
            item: dict[str, Any] = {"symbol": sym, "side": side}
            w = (raw or {}).get("weight")
            a = (raw or {}).get("amount")
            if w is not None and w != "":
                try:
                    item["weight"] = abs(float(w))
                except (TypeError, ValueError):
                    pass
            if a is not None and a != "":
                try:
                    item["amount"] = abs(float(a))
                except (TypeError, ValueError):
                    pass
            ep = (raw or {}).get("entry_price")
            if ep is not None and ep != "":
                try:
                    item["entry_price"] = float(ep)
                except (TypeError, ValueError):
                    pass
            out.append(item)
        if out:
            # 若都没权重/金额，均权
            if all(x.get("weight") is None and x.get("amount") is None for x in out):
                for x in out:
                    x["weight"] = 1.0
            return out

    syms: list[str] = []
    for s in symbols or []:
        t = str(s or "").strip().upper()
        if t and t not in syms:
            syms.append(t)
    if not syms:
        raise BudgetModelError("请至少填写一个股票代码")
    return [{"symbol": s, "weight": 1.0, "side": "long"} for s in syms]


def _verdict(median_pnl: float | None) -> str:
    if median_pnl is None:
        return "未知"
    if median_pnl > 1e-6:
        return "赚"
    if median_pnl < -1e-6:
        return "亏"
    return "平"


def run_budget_model(
    *,
    model_id: str | None = "block_bootstrap",
    symbols: list[str] | None = None,
    legs: list[dict] | None = None,
    horizon_days: int = 21,
    capital: float = 10000.0,
    window: int = 252,
    n_sims: int = 2000,
    drift_mode: str = "historical",
    loss_limit: float = 0.05,
) -> dict:
    """
    统一入口：多股票 + 持仓 N 天 → 中位赚/亏。
    """
    meta = get_budget_model(model_id)
    mid = meta["id"]
    parsed = _normalize_legs(symbols, legs)
    cap = float(capital or 0)
    if cap <= 0:
        raise BudgetModelError("本金须大于 0")
    hz = max(5, min(int(horizon_days or 21), 126))
    win = max(60, min(int(window or 252), 1000))
    sims = max(800, min(int(n_sims or 2000), 8000))

    try:
        packed = _run_via_portfolio(
            parsed,
            model_id=mid,
            capital=cap,
            horizon_days=hz,
            window=win,
            n_sims=sims,
            drift_mode=drift_mode,
            loss_limit=loss_limit,
        )
    except PortfolioError as exc:
        raise BudgetModelError(str(exc)) from exc

    block = packed["block"]
    median_v = block.get("median_value")
    if median_v is None:
        raise BudgetModelError("模型未返回中位终值，无法判断赚亏")

    median_f = float(median_v)
    median_pnl = round(median_f - cap, 2)
    # project_future 用 p05_value / p95_value
    p5 = block.get("p05_value", block.get("p5"))
    p95 = block.get("p95_value", block.get("p95"))
    p5_pnl = None if p5 is None else round(float(p5) - cap, 2)
    p95_pnl = None if p95 is None else round(float(p95) - cap, 2)
    prob_profit = block.get("prob_profit")
    verdict = _verdict(median_pnl)
    avoid = verdict == "亏"
    fc = packed["forecast"] or {}
    mu_ann = fc.get("mu_annual_approx")
    drift_bias = fc.get("drift_bias")

    if avoid:
        hint = (
            f"【{meta['label']}】持仓 {hz} 个交易日中位展望为亏"
            f"（约 ${median_pnl:,.2f}）。样本外推偏空，不宜建仓。"
        )
    elif verdict == "赚":
        hint = (
            f"【{meta['label']}】持仓 {hz} 天中位展望为赚"
            f"（约 ${median_pnl:,.2f}，盈利概率 {float(prob_profit or 0)*100:.0f}%）。"
            "这是把近一年涨跌外推到未来，不是因为现价便宜。"
        )
    else:
        hint = f"【{meta['label']}】持仓 {hz} 天中位接近持平。请结合路径回撤与危机系数。"

    analysis = packed["analysis"]
    assets = analysis.get("assets") or []
    per_symbol = []
    for a in assets:
        per_symbol.append({
            "symbol": a.get("symbol"),
            "side": a.get("side"),
            "weight": a.get("weight"),
            "price": a.get("price"),
        })

    return {
        "ok": True,
        "model_id": mid,
        "model_name": meta.get("name"),
        "model_label": meta.get("label"),
        "symbols": [x["symbol"] for x in parsed],
        "legs": parsed,
        "horizon_days": hz,
        "capital": round(cap, 2),
        "window": win,
        "drift_mode": drift_mode,
        "verdict": verdict,
        "avoid_entry": avoid,
        "median_terminal": round(median_f, 2),
        "median_pnl": median_pnl,
        "p5_pnl": p5_pnl,
        "p95_pnl": p95_pnl,
        "prob_profit": prob_profit,
        "prob_mdd_gt_10pct": block.get("prob_mdd_gt_10pct"),
        "mu_annual_approx": mu_ann,
        "drift_bias": drift_bias,
        "price_note": (
            "表里「现价」只作参考；中位盈亏 = 本金 × 历史日收益蒙特卡洛，"
            "不判断现价是否高位，也不做目标价估值。"
        ),
        "per_symbol": per_symbol,
        "hint": hint,
        "note": (
            f"{', '.join(x['symbol'] for x in parsed)} · 持仓 {hz} 个交易日 · "
            f"{meta.get('label')} · 中位{'盈利' if median_pnl >= 0 else '亏损'} "
            f"${abs(median_pnl):,.2f}"
        ),
        "forecast": fc,
        "portfolio_summary": {
            "vol_annual": (analysis.get("portfolio") or {}).get("vol_annual"),
            "ann_return_compound": (analysis.get("portfolio") or {}).get("ann_return_compound"),
        },
    }


def _dated_returns_list(symbol: str, window: int) -> tuple[list[float], float, str]:
    from app.services.ohlc import OhlcError, fetch_closes
    from app.services.portfolio import _bar_dated_returns

    try:
        ohlc = fetch_closes(symbol, "1d", apply_live=True)
    except OhlcError as exc:
        raise BudgetModelError(str(exc)) from exc
    rets_map = _bar_dated_returns(ohlc.get("ohlc_bars") or [], window)
    if len(rets_map) < 40:
        raise BudgetModelError(f"{symbol} 有效收益不足（{len(rets_map)}）")
    dates = sorted(rets_map.keys())
    rets = [float(rets_map[d]) for d in dates]
    px = ohlc.get("price")
    closes = ohlc.get("closes") or []
    price = float(px) if px is not None else (float(closes[-1]) if closes else 0.0)
    if price <= 0:
        raise BudgetModelError(f"{symbol} 无有效现价")
    return rets, price, ohlc.get("source") or ""


def _block_path_series(
    returns: list[float],
    *,
    horizon_days: int,
    n_sims: int,
    block_size: int = 10,
    demean: bool = False,
    seed: int = 42,
) -> tuple[list[float], list[list[float]]]:
    """返回 (终值倍数列表, 每条路径逐日净值倍数列表)。"""
    import math
    import random
    from statistics import mean

    sim = list(returns)
    if demean and sim:
        mu = mean(sim)
        sim = [r - mu for r in sim]
    n = len(sim)
    hz = max(1, int(horizon_days))
    bs = max(2, min(int(block_size), max(2, n // 5)))
    max_start = max(0, n - bs)
    rng = random.Random(seed)
    terminals: list[float] = []
    series: list[list[float]] = []
    for _ in range(max(100, int(n_sims))):
        path: list[float] = []
        while len(path) < hz:
            st = rng.randint(0, max_start) if max_start > 0 else 0
            path.extend(sim[st : st + bs])
        path = path[:hz]
        v = 1.0
        vals: list[float] = []
        for r in path:
            v *= math.exp(r)
            vals.append(v)
        terminals.append(v)
        series.append(vals)
    return terminals, series


def _touch_stats(
    series: list[list[float]],
    barrier: float,
    horizon_days: int,
) -> dict:
    from app.services.risk import _percentile, _round

    n = len(series)
    if n == 0:
        return {}
    first_days: list[int | None] = []
    for vals in series:
        hit: int | None = None
        for t, v in enumerate(vals, start=1):
            if v <= barrier + 1e-12:
                hit = t
                break
        first_days.append(hit)

    touched = [d for d in first_days if d is not None]
    touch_prob = len(touched) / n
    never_prob = 1.0 - touch_prob

    cum = []
    for t in range(1, horizon_days + 1):
        c = sum(1 for d in first_days if d is not None and d <= t) / n
        cum.append({"day": t, "cum_prob": _round(c, 4)})

    if touched:
        ts = sorted(float(x) for x in touched)
        t_med = _percentile(ts, 0.50)
        t_p25 = _percentile(ts, 0.25)
        t_p75 = _percentile(ts, 0.75)
        t_p90 = _percentile(ts, 0.90)
    else:
        t_med = t_p25 = t_p75 = t_p90 = None

    # 分桶：前 1/4、半程、全程
    q = max(1, horizon_days // 4)
    buckets = [
        {
            "label": f"≤{q} 天",
            "prob": _round(sum(1 for d in first_days if d is not None and d <= q) / n, 4),
        },
        {
            "label": f"≤{max(q, horizon_days // 2)} 天",
            "prob": _round(
                sum(1 for d in first_days if d is not None and d <= max(q, horizon_days // 2)) / n,
                4,
            ),
        },
        {
            "label": f"≤{horizon_days} 天（期内）",
            "prob": _round(touch_prob, 4),
        },
    ]

    return {
        "prob_touch": _round(touch_prob, 4),
        "prob_never_touch": _round(never_prob, 4),
        "touch_days_median": None if t_med is None else _round(t_med, 1),
        "touch_days_p25": None if t_p25 is None else _round(t_p25, 1),
        "touch_days_p75": None if t_p75 is None else _round(t_p75, 1),
        "touch_days_p90": None if t_p90 is None else _round(t_p90, 1),
        "cum_touch_by_day": cum,
        "touch_buckets": buckets,
    }


def solve_target_entry(
    *,
    symbols: list[str] | None = None,
    symbol: str | None = None,
    target_return: float = 0.05,
    horizon_days: int = 21,
    window: int = 252,
    n_sims: int = 3000,
    drift_mode: str = "historical",
    block_size: int = 10,
    target_buy_price: float | None = None,
) -> dict:
    """
    目标：持仓 N 天后赚 target_return（如 5%）。

    最优买入价定义（Block 路径中位）：
      P* = P0 × M / (1+target)
    也可直接传入 target_buy_price 作为你的目标买点。

    回撤时间：首次触及买点的天数分布 + 逐日累计触及概率。
    """
    from app.services.risk import _percentile, _round

    syms: list[str] = []
    if symbol:
        syms.append(str(symbol).strip().upper())
    for s in symbols or []:
        t = str(s or "").strip().upper()
        if t and t not in syms:
            syms.append(t)
    if not syms:
        raise BudgetModelError("请填写股票代码")

    tgt = float(target_return)
    if tgt <= -0.9 or tgt > 2.0:
        raise BudgetModelError("目标收益率不合理")
    hz = max(5, min(int(horizon_days or 21), 126))
    win = max(60, min(int(window or 252), 1000))
    sims = max(1000, min(int(n_sims or 3000), 8000))
    demean = str(drift_mode or "").lower() in ("zero", "μ=0", "mu0", "demean")
    mult_target = 1.0 + tgt
    custom_px = None
    if target_buy_price is not None and str(target_buy_price).strip() != "":
        try:
            custom_px = float(target_buy_price)
        except (TypeError, ValueError) as exc:
            raise BudgetModelError("目标买入价无效") from exc
        if custom_px <= 0:
            raise BudgetModelError("目标买入价须大于 0")

    items = []
    for sym in syms:
        rets, p0, src = _dated_returns_list(sym, win)
        terminals, series = _block_path_series(
            rets,
            horizon_days=hz,
            n_sims=sims,
            block_size=block_size,
            demean=demean,
        )
        med_m = float(_percentile(sorted(terminals), 0.50))
        p05_m = float(_percentile(sorted(terminals), 0.05))
        p95_m = float(_percentile(sorted(terminals), 0.95))
        model_optimal = p0 * med_m / mult_target
        if custom_px is not None:
            buy_px = custom_px
            price_source = "user"
        else:
            buy_px = model_optimal
            price_source = "model_optimal"

        barrier = buy_px / p0
        already = p0 <= buy_px + 1e-9
        touch = _touch_stats(series, 1.0 if already else barrier, hz)
        if already:
            # 现价已低于目标：视为第 0 天已触及
            touch = {
                **touch,
                "prob_touch": 1.0,
                "prob_never_touch": 0.0,
                "touch_days_median": 0,
                "touch_days_p25": 0,
                "touch_days_p75": 0,
                "touch_days_p90": 0,
                "cum_touch_by_day": [
                    {"day": t, "cum_prob": 1.0} for t in range(1, hz + 1)
                ],
                "touch_buckets": [
                    {"label": "已在买点以下", "prob": 1.0},
                ],
            }

        hit_target_now = sum(1 for t in terminals if t >= mult_target - 1e-12) / len(terminals)
        pullback_pct = max(0.0, 1.0 - barrier)
        t_med = touch.get("touch_days_median")
        time_note = (
            "现价已≤目标买点。"
            if already
            else (
                f"在触及的路径里，首次回撤到买点的中位约 {t_med} 个交易日"
                f"（P90≈{touch.get('touch_days_p90')} 天）；"
                f"期内永不触及约 {float(touch.get('prob_never_touch') or 0)*100:.1f}%。"
            )
        )

        items.append({
            "symbol": sym,
            "source": src,
            "spot_price": _round(p0, 4),
            "horizon_days": hz,
            "target_return": tgt,
            "median_mult": _round(med_m, 6),
            "median_terminal_price": _round(p0 * med_m, 4),
            "model_optimal_buy_price": _round(model_optimal, 4),
            "optimal_buy_price": _round(buy_px, 4),
            "buy_price_source": price_source,
            "pullback_from_spot_pct": _round(pullback_pct, 6),
            "already_at_or_below": already,
            "prob_touch_optimal": touch.get("prob_touch"),
            "prob_never_touch": touch.get("prob_never_touch"),
            "touch_days_median": touch.get("touch_days_median"),
            "touch_days_p25": touch.get("touch_days_p25"),
            "touch_days_p75": touch.get("touch_days_p75"),
            "touch_days_p90": touch.get("touch_days_p90"),
            "cum_touch_by_day": touch.get("cum_touch_by_day") or [],
            "touch_buckets": touch.get("touch_buckets") or [],
            "prob_earn_target_if_buy_now": _round(hit_target_now, 4),
            "prob_earn_target_if_buy_optimal": _round(
                sum(1 for t in terminals if t * mult_target / med_m >= mult_target - 1e-12) / len(terminals),
                4,
            ),
            "p05_terminal_price": _round(p0 * p05_m, 4),
            "p95_terminal_price": _round(p0 * p95_m, 4),
            "drift_mode": "zero" if demean else "historical",
            "time_note": time_note,
            "note": (
                f"{sym} 现价 ${p0:,.2f}。目标买点 ${buy_px:,.2f}"
                f"（{'你指定' if price_source == 'user' else f'模型最优≈${model_optimal:,.2f}'}）。"
                f"需回撤约 {pullback_pct*100:.1f}%。期内触及概率 "
                f"{float(touch.get('prob_touch') or 0)*100:.1f}%。{time_note}"
            ),
        })

    return {
        "ok": True,
        "method": "block_bootstrap_first_touch",
        "target_return": tgt,
        "horizon_days": hz,
        "window": win,
        "n_sims": sims,
        "drift_mode": "zero" if demean else "historical",
        "target_buy_price": custom_px,
        "definition": (
            "最优买入价 P* = 现价 × 中位终值倍数 / (1+目标收益率)；也可手填目标买点。"
            "回撤时间 = 路径首次跌到目标价的交易日；cum_touch_by_day 为逐日累计触及概率。"
        ),
        "items": items,
    }
