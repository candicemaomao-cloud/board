"""学习写套利：先用表里的前三块，其余先不用。

  微积分     对数价格 ln P；OLS 估 β（最小二乘 = 优化）
  概率论     z = (价差 − 均值) / 标准差；把偏离当「有多极端」
  时间序列   价差均值回归、ADF 协整、半衰期
  随机过程   离散 OU：spread_t = φ·spread_{t-1} + ε，先能算半衰期就够
  随机微积分 Black-Scholes / 期权，配对交易暂时用不到
  信息论/ML  选因子、降维，一对标的定了再考虑

对照：backend/app/user_arbs/qqq_tlt.py 只有参数；本文件把公式写出来。
刷新套利策略列表，点「计算」。
"""

from __future__ import annotations

import math
from statistics import mean, pstdev

from app.services.arb_strategy import ArbError, _adf, _align, _logs

STRATEGY = {
    "name": "学习：均值回归",
    "notes": "QQQ vs TLT。公式在本文件 run() 里，对照微积分 / 概率 / 时间序列。",
    "kind": "ols",
    "timeframe": "1d",
    "leg_a": "QQQ",
    "leg_b": "TLT",
    "factors": ["TLT"],
    "lookback": 60,
    "entry_z": 2.0,
    "exit_z": 0.5,
    "stop_z": 3.5,
    "notional": 10000,
    "bt_days": 365,
    "macro_filter": False,
    "beta": None,
}


def _ols_beta(y: list[float], x: list[float]) -> float:
    """微积分 / 优化：min Σ (y − α − βx)²，对 β 求导得 cov(x,y)/var(x)。"""
    n = len(x)
    if n < 8:
        return 1.0
    mx, my = mean(x), mean(y)
    varx = sum((xi - mx) ** 2 for xi in x)
    if varx < 1e-18:
        return 1.0
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    return cov / varx


def _half_life(spread: list[float]) -> float | None:
    """随机过程（离散 OU）：s_t = φ s_{t-1} + ε。
    半衰期 = ln(0.5) / ln(φ)，单位是 K 线根数。φ 不在 (0,1) 就不是回归。"""
    if len(spread) < 20:
        return None
    x = spread[:-1]
    y = spread[1:]
    mx, my = mean(x), mean(y)
    varx = sum((xi - mx) ** 2 for xi in x)
    if varx < 1e-18:
        return None
    phi = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / varx
    if not (0 < phi < 1):
        return None
    return math.log(0.5) / math.log(phi)


def _z(values: list[float]) -> float | None:
    """概率：标准化。|z|>2 大约是「比均值远两个标准差」。
    价差往往肥尾，不要把 2σ 当成真有 5% 概率。"""
    if len(values) < 8:
        return None
    mu = mean(values)
    sd = pstdev(values)
    if sd < 1e-12:
        return 0.0
    return (values[-1] - mu) / sd


def run(ctx):
    a = STRATEGY["leg_a"]
    b = STRATEGY["leg_b"]
    oa = ctx.closes(a)
    ob = ctx.closes(b)
    px_a, px_b, _dates = _align(oa.get("ohlc_bars") or [], ob.get("ohlc_bars") or [], STRATEGY["timeframe"])
    if len(px_a) < 80:
        raise ArbError("K 线不够，换一对或改成日线再试")

    # 微积分：连续复利 / 对数收益。价差用 ln P，加性、对称。
    log_a = _logs(px_a)
    log_b = _logs(px_b)
    beta = _ols_beta(log_a, log_b)
    # 时间序列：价差 = ln A − β ln B。回归的是这条残差，不是单边涨跌。
    spread = [ya - beta * yb for ya, yb in zip(log_a, log_b)]
    lookback = int(STRATEGY["lookback"])
    window = spread[-lookback:]
    z_now = _z(window)
    half = _half_life(spread)
    adf = _adf(spread)
    adf_txt = "通过" if adf and adf.get("pass") else "未通过"
    z_txt = f"{z_now:.2f}" if z_now is not None else "—"
    if half:
        lesson = (
            f"学习笔记 · {a} vs {b}。"
            f"ln 价 OLS β={beta:.3f}；近{lookback}根 z={z_txt}；"
            f"半衰期 {half:.0f} 根；ADF {adf_txt}。"
            "规则：|z|≥2 开、回到 0.5 平。相关 ≠ 协整 ≠ 可套利。"
        )
    else:
        lesson = (
            f"学习笔记 · {a} vs {b}。"
            f"ln 价 OLS β={beta:.3f}；近{lookback}根 z={z_txt}；ADF {adf_txt}。"
            "半衰期算不出（φ 不在 0–1），价差不太像均值回归。"
        )

    out = ctx.engine()
    out["hypothesis"] = lesson
    return out
