"""因子暴露 / 多元 OLS：解释收益来源，不做点预测。

独立于 Bootstrap/GBM 概率展望：回答「对哪些因素敏感」，而非「未来涨跌概率」。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from app.services.ohlc import OhlcError, fetch_closes
from app.services.risk import (
    TRADING_DAYS,
    _ols,
    _round,
    _sig_stars,
    compound_annualize,
)


class FactorError(Exception):
    pass


# 第一版宏观/市场因子（优先用 ETF/指数代号，兼容现有 OHLC 拉数）
FACTOR_SPECS = [
    {"key": "SPY", "symbol": "SPY", "kind": "log_return", "label": "市场 SPY", "unit": "日对数收益"},
    {"key": "QQQ", "symbol": "QQQ", "kind": "log_return", "label": "成长/纳指 QQQ", "unit": "日对数收益"},
    {"key": "SOXX", "symbol": "SMH", "kind": "log_return", "label": "半导体 SMH", "unit": "日对数收益"},
    {"key": "TNX", "symbol": "TNX", "kind": "diff", "label": "美债10Y Δ", "unit": "收益率点数变化"},
    {"key": "DXY", "symbol": "DXY", "kind": "log_return", "label": "美元 DXY", "unit": "日对数收益"},
    {"key": "VIX", "symbol": "VIX", "kind": "log_return", "label": "波动 VIX", "unit": "日对数收益"},
    {"key": "USO", "symbol": "USO", "kind": "log_return", "label": "原油 USO", "unit": "日对数收益"},
    {"key": "GLD", "symbol": "GLD", "kind": "log_return", "label": "黄金 GLD", "unit": "日对数收益"},
]


def _bar_series(bars: list[dict], kind: str, window: int) -> dict[str, float]:
    cleaned = []
    for bar in bars or []:
        ts, c = bar.get("ts"), bar.get("close")
        if ts is None or c is None:
            continue
        try:
            c = float(c)
        except (TypeError, ValueError):
            continue
        cleaned.append((int(ts), c))
    if len(cleaned) < 40:
        return {}
    cleaned = cleaned[-(window + 1) :] if len(cleaned) > window + 1 else cleaned
    out: dict[str, float] = {}
    for i in range(1, len(cleaned)):
        ts, c = cleaned[i]
        prev = cleaned[i - 1][1]
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        if kind == "diff":
            out[day] = c - prev
        else:
            if prev <= 0 or c <= 0:
                continue
            out[day] = math.log(c / prev)
    return out


def _fetch_factor_panel(window: int) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    panel: dict[str, dict[str, float]] = {}
    errors: dict[str, str] = {}
    for spec in FACTOR_SPECS:
        try:
            ohlc = fetch_closes(spec["symbol"], "1d", apply_live=False)
            series = _bar_series(ohlc.get("ohlc_bars") or [], spec["kind"], window)
            if len(series) < 30:
                raise FactorError(f"{spec['symbol']} 样本不足")
            panel[spec["key"]] = series
        except Exception as exc:  # noqa: BLE001
            errors[spec["key"]] = str(exc)
    return panel, errors


def _align(
    y_map: dict[str, float],
    panel: dict[str, dict[str, float]],
    keys: list[str],
) -> tuple[list[str], list[float], list[list[float]]]:
    common = set(y_map.keys())
    for k in keys:
        common &= set(panel[k].keys())
    dates = sorted(common)
    y = [y_map[d] for d in dates]
    X = [[panel[k][d] for k in keys] for d in dates]
    return dates, y, X


def _sig_level(p: float | None) -> str:
    if p is None:
        return "—"
    if p < 0.01:
        return "高"
    if p < 0.05:
        return "中"
    if p < 0.10:
        return "中低"
    return "低"


def _pack_fit(fit: dict, keys: list[str]) -> dict:
    coef = fit.get("coef") or []
    if not coef:
        return {"ok": False, "note": "回归失败"}
    alpha = coef[0]
    alpha_p = fit["p"][0] if fit.get("p") else None
    loadings = []
    for i, key in enumerate(keys):
        spec = next((s for s in FACTOR_SPECS if s["key"] == key), {"label": key})
        b = coef[i + 1]
        t = fit["t"][i + 1] if fit.get("t") and len(fit["t"]) > i + 1 else None
        p = fit["p"][i + 1] if fit.get("p") and len(fit["p"]) > i + 1 else None
        loadings.append({
            "key": key,
            "label": spec.get("label", key),
            "beta": _round(b, 4),
            "t_stat": _round(t, 3) if t is not None else None,
            "p_value": _round(p, 4) if p is not None else None,
            "sig": _sig_stars(p),
            "sig_level": _sig_level(p),
        })
    # 按 |beta| 排序便于阅读
    ranked = sorted(loadings, key=lambda r: abs(r["beta"] or 0), reverse=True)
    return {
        "ok": True,
        "alpha_daily": _round(alpha, 6),
        "alpha_annual": _round(compound_annualize(alpha), 6),
        "alpha_pvalue": _round(alpha_p, 4) if alpha_p is not None else None,
        "alpha_sig": _sig_stars(alpha_p),
        "r2": _round(fit.get("r2"), 4),
        "n": fit.get("n"),
        "loadings": loadings,
        "ranked": ranked,
    }


def scenario_expected(
    alpha_daily: float,
    loadings: list[dict],
    shocks: dict[str, float],
) -> dict:
    """
    情景条件期望（日度近似）：
      E[R] ≈ α + Σ β_k * shock_k
    shocks 与因子同量纲：权益/商品为对数收益（如 -0.05）；TNX 为点数变化（如 +0.30）。
    """
    expl = []
    total = float(alpha_daily or 0.0)
    for row in loadings:
        key = row["key"]
        if key not in shocks or shocks[key] is None:
            continue
        shock = float(shocks[key])
        contrib = float(row["beta"] or 0.0) * shock
        total += contrib
        expl.append({
            "key": key,
            "label": row.get("label", key),
            "beta": row.get("beta"),
            "shock": _round(shock, 6),
            "contribution": _round(contrib, 6),
        })
    return {
        "expected_daily": _round(total, 6),
        "expected_pct": _round(total * 100, 4),
        "contributions": expl,
        "note": "线性情景近似，因子关系可能失效；仅作暴露解读，不是预测。",
    }


def rolling_betas(
    dates: list[str],
    y: list[float],
    X: list[list[float]],
    keys: list[str],
    roll_window: int = 252,
    step: int = 5,
) -> dict:
    """滚动窗口 OLS，返回稀疏时间序列（每 step 日一点）。"""
    n = len(y)
    roll_window = max(40, min(int(roll_window or 126), n))
    step = max(1, int(step or 5))
    series: list[dict] = []
    for end in range(roll_window, n + 1, step):
        start = end - roll_window
        fit = _ols(y[start:end], X[start:end])
        if not fit.get("coef"):
            continue
        point = {"date": dates[end - 1], "r2": _round(fit["r2"], 4)}
        for i, key in enumerate(keys):
            point[key] = _round(fit["coef"][i + 1], 4)
        series.append(point)
    # 始终包含最后一点
    if n >= roll_window:
        fit = _ols(y[n - roll_window : n], X[n - roll_window : n])
        if fit.get("coef"):
            point = {"date": dates[-1], "r2": _round(fit["r2"], 4)}
            for i, key in enumerate(keys):
                point[key] = _round(fit["coef"][i + 1], 4)
            if not series or series[-1]["date"] != point["date"]:
                series.append(point)
    return {
        "roll_window": roll_window,
        "step": step,
        "points": series[-60:],  # 控制载荷
        "note": f"{roll_window} 日滚动回归，步长 {step}；用于观察暴露是否在上升/下降",
    }


def analyze_factor_exposure(
    symbol: str,
    window: int = 252,
    roll_window: int = 252,
    shocks: dict[str, float] | None = None,
) -> dict:
    code = (symbol or "").strip().upper()
    if not code:
        raise FactorError("请输入股票代码")
    if code.endswith(("USDT", "USDC")):
        raise FactorError("因子暴露模型目前面向美股/ETF，加密货币请另建因子集")
    window = max(60, min(int(window or 252), 1000))
    roll_window = max(40, min(int(roll_window or 252), window))

    try:
        ohlc = fetch_closes(code, "1d", apply_live=True)
    except OhlcError as exc:
        raise FactorError(str(exc)) from exc

    y_map = _bar_series(ohlc.get("ohlc_bars") or [], "log_return", window)
    if len(y_map) < 40:
        raise FactorError(f"{code} 收益样本不足")

    panel, errors = _fetch_factor_panel(window)
    keys = [s["key"] for s in FACTOR_SPECS if s["key"] in panel]
    if len(keys) < 3:
        raise FactorError("可用因子过少：" + "; ".join(f"{k}:{v}" for k, v in errors.items()))

    dates, y, X = _align(y_map, panel, keys)
    if len(dates) < 40:
        raise FactorError(f"与因子重叠交易日不足（{len(dates)}）")

    fit = _ols(y, X)
    packed = _pack_fit(fit, keys)
    if not packed.get("ok"):
        raise FactorError(packed.get("note") or "回归失败")

    # 默认情景：温和风险-off
    if not shocks:
        shocks = {
            "SPY": -0.05,
            "QQQ": -0.06,
            "SOXX": -0.10,
            "TNX": 0.20,
            "DXY": 0.01,
            "VIX": 0.15,
            "USO": -0.03,
            "GLD": 0.01,
        }
    # 只保留模型里有的因子
    shocks_use = {k: float(v) for k, v in shocks.items() if k in keys and v is not None}
    scen = scenario_expected(packed["alpha_daily"], packed["loadings"], shocks_use)
    roll = rolling_betas(dates, y, X, keys, roll_window=roll_window, step=5)

    # 叙事摘要 + 滚动 Beta 漂移
    top = packed["ranked"][:3]
    bits = []
    for row in top:
        bits.append(
            f"{row['label']} β={row['beta']}（显著性{row.get('sig_level') or '—'}）"
        )
    narrative = (
        f"{code} 在估计期内，对因子敏感度最高的是：" + "；".join(bits) + f"。R²={packed['r2']}。"
        "以上是风险暴露解释，不是未来收益预测。"
    )
    drift_notes = []
    pts = roll.get("points") or []
    if len(pts) >= 4:
        for key in keys[:4]:
            vals = [p.get(key) for p in pts if p.get(key) is not None]
            if len(vals) < 4:
                continue
            early = sum(vals[: max(2, len(vals)//3)]) / max(2, len(vals)//3)
            late = sum(vals[-max(2, len(vals)//3):]) / max(2, len(vals)//3)
            delta = late - early
            if abs(delta) >= 0.15:
                spec = next((s for s in FACTOR_SPECS if s["key"] == key), {"label": key})
                direction = "提高" if delta > 0 else "下降"
                drift_notes.append(
                    f"{spec.get('label', key)} 滚动β由约 {early:.2f} → {late:.2f}，近期敏感度正在{direction}"
                )
    rolling_insight = "；".join(drift_notes) if drift_notes else "滚动窗口内主要因子暴露未见剧烈漂移。"
    narrative = narrative + " " + rolling_insight + "。"

    return {
        "symbol": code,
        "window": window,
        "n": len(dates),
        "date_start": dates[0],
        "date_end": dates[-1],
        "price": _round(float(ohlc.get("price") or 0), 4) or None,
        "source": ohlc.get("source"),
        "factors_used": keys,
        "factors_dropped": errors,
        "factor_meta": [
            {**{k: s[k] for k in ("key", "symbol", "label", "kind", "unit")}, "ok": s["key"] in keys}
            for s in FACTOR_SPECS
        ],
        "ols": packed,
        "scenario": scen,
        "scenario_shocks": shocks_use,
        "rolling": roll,
        "rolling_insight": rolling_insight,
        "narrative": narrative,
        "role": "factor_exposure",
        "note": (
            "多元 OLS 因子暴露模型：解释收益对市场/行业/宏观的敏感度。"
            "与 Bootstrap/GBM 概率展望互补，但不替代情景分布。"
            "线性关系可能失效；滚动 Beta 用于观察暴露漂移。"
        ),
        "disclaimer": "因子暴露与情景期望依赖历史估计与线性假设，不代表未来实际收益。",
    }
