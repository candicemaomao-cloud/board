"""投资组合风险测算：多标的权重、相关、组合波动/VaR/Beta。"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from statistics import mean, pstdev

from app.services.ohlc import OhlcError, fetch_closes
from app.services.risk import (
    TRADING_DAYS,
    DEFAULT_BENCH,
    CRYPTO_BENCH,
    _log_returns,
    _percentile,
    _round,
    _annualize_vol,
    compound_annualize,
    beta_capm,
    var_cvar_historical,
    max_drawdown,
    sharpe_sortino,
)


class PortfolioError(Exception):
    pass


def _pick_benchmark(symbols: list[str]) -> str:
    if symbols and all(s.endswith(("USDT", "USDC")) for s in symbols):
        return CRYPTO_BENCH
    return DEFAULT_BENCH


def _bar_dated_returns(bars: list[dict], window: int) -> dict[str, float]:
    """返回 {YYYY-MM-DD: log return}，取最近 window 个收益。"""
    cleaned = []
    for bar in bars or []:
        ts = bar.get("ts")
        c = bar.get("close")
        if ts is None or c is None:
            continue
        try:
            c = float(c)
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        cleaned.append((int(ts), c))
    if len(cleaned) < 40:
        return {}
    cleaned = cleaned[-(window + 1) :] if len(cleaned) > window + 1 else cleaned
    out: dict[str, float] = {}
    for i in range(1, len(cleaned)):
        ts, c = cleaned[i]
        prev = cleaned[i - 1][1]
        if prev <= 0:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        out[day] = math.log(c / prev)
    return out


def _cov(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 2:
        return 0.0
    ma, mb = mean(a), mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n


def _port_stats(w: list[float], mu: list[float], cov: list[list[float]]) -> dict:
    m = len(w)
    mean_r = sum(w[i] * mu[i] for i in range(m))
    var = 0.0
    for i in range(m):
        for j in range(m):
            var += w[i] * w[j] * cov[i][j]
    vol = math.sqrt(max(var, 0.0))
    sharpe = (mean_r / vol) * math.sqrt(TRADING_DAYS) if vol > 1e-18 else None
    return {
        "mean_daily": _round(mean_r),
        "ann_return_compound": _round(compound_annualize(mean_r), 6),
        "vol_annual": _round(_annualize_vol(vol)),
        "sharpe": _round(sharpe, 4) if sharpe is not None else None,
    }


def _mat_eye(n: int) -> list[list[float]]:
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _mat_add_diag(a: list[list[float]], eps: float) -> list[list[float]]:
    n = len(a)
    out = [row[:] for row in a]
    for i in range(n):
        out[i][i] += eps
    return out


def _mat_vec(a: list[list[float]], v: list[float]) -> list[float]:
    return [sum(a[i][j] * v[j] for j in range(len(v))) for i in range(len(a))]


def _mat_inv(a: list[list[float]]) -> list[list[float]] | None:
    """Gauss-Jordan 求逆；失败返回 None。"""
    n = len(a)
    m = [a[i][:] + _mat_eye(n)[i] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-14:
            return None
        m[col], m[piv] = m[piv], m[col]
        div = m[col][col]
        for j in range(2 * n):
            m[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            fac = m[r][col]
            for j in range(2 * n):
                m[r][j] -= fac * m[col][j]
    return [[m[i][n + j] for j in range(n)] for i in range(n)]


def _mat_cond_est(a: list[list[float]]) -> float:
    """粗估条件数：max|diag| / min|diag|（仅作奇异提示，非精确 κ）。"""
    diags = [abs(a[i][i]) for i in range(len(a))]
    hi = max(diags) if diags else 0.0
    lo = min(d for d in diags if d > 0) if any(d > 0 for d in diags) else 0.0
    if lo <= 0:
        return float("inf")
    return hi / lo


def markowitz_weights(
    symbols: list[str],
    returns_mat: dict[str, list[float]],
    cov: list[list[float]],
    rf_daily: float = 0.0,
) -> dict:
    """
    马科维茨：对协方差 Σ 求逆得到
      全局最小方差 GMV:  w ∝ Σ^{-1} 1
      切线组合(最大夏普): w ∝ Σ^{-1}(μ-rf)
    纯 Python 实现，不依赖 numpy；Σ 奇异时加岭回归再求逆。
    """
    m = len(symbols)
    if m < 2:
        return {"ok": False, "note": "至少 2 只标的才能做马科维茨优化"}

    mu = [mean(returns_mat[s]) for s in symbols]
    Sigma = [row[:] for row in cov]
    singular = False
    method = "inv"
    cond = _mat_cond_est(Sigma)
    inv = _mat_inv(Sigma)
    if inv is None or (math.isfinite(cond) and cond > 1e12):
        singular = True
        method = "ridge"
        # 逐步加大岭参数直到可逆
        inv = None
        for eps in (1e-10, 1e-8, 1e-6, 1e-4, 1e-2):
            inv = _mat_inv(_mat_add_diag(Sigma, eps))
            if inv is not None:
                cond = _mat_cond_est(_mat_add_diag(Sigma, eps))
                break
    if inv is None:
        return {
            "ok": False,
            "singular": True,
            "note": "协方差矩阵奇异且岭回归仍无法求逆：资产高度共线，无法给出稳定权重",
        }

    ones = [1.0] * m
    raw_gmv = _mat_vec(inv, ones)
    s_gmv = sum(raw_gmv)
    if abs(s_gmv) < 1e-18:
        return {"ok": False, "note": "Σ^{-1}1 求和近 0，无法归一化", "singular": True}
    w_gmv = [x / s_gmv for x in raw_gmv]

    excess = [mu[i] - rf_daily for i in range(m)]
    raw_tan = _mat_vec(inv, excess)
    s_tan = sum(raw_tan)
    if abs(s_tan) < 1e-18:
        w_tan = w_gmv[:]
        tan_note = "超额收益过小，切线组合退化为 GMV"
    else:
        w_tan = [x / s_tan for x in raw_tan]
        tan_note = None

    def pack(w_list: list[float], label: str, extra_note: str | None = None) -> dict:
        w_long = [max(x, 0.0) for x in w_list]
        s = sum(w_long)
        if s > 1e-12:
            w_long = [x / s for x in w_long]
        else:
            w_long = [1.0 / m] * m
        rows = []
        for i, sym in enumerate(symbols):
            rows.append({
                "symbol": sym,
                "weight": _round(w_list[i], 6),
                "weight_pct": _round(w_list[i] * 100, 4),
                "weight_long_only": _round(w_long[i], 6),
                "weight_long_only_pct": _round(w_long[i] * 100, 4),
            })
        return {
            "label": label,
            "weights": rows,
            "stats": _port_stats(w_list, mu, cov),
            "stats_long_only": _port_stats(w_long, mu, cov),
            "note": extra_note,
        }

    return {
        "ok": True,
        "singular": singular,
        "condition_number": _round(cond, 2) if math.isfinite(cond) else None,
        "invert_method": method,
        "formula": {
            "gmv": "w ∝ Σ^{-1} 1",
            "tangency": "w ∝ Σ^{-1}(μ − rf)",
            "ols_hint": "多因子选股同理用 (X'X)^{-1}X'Y，见风险模型 Fama-French",
        },
        "gmv": pack(w_gmv, "全局最小方差 GMV"),
        "tangency": pack(w_tan, "切线组合 / 最大夏普", tan_note),
        "note": (
            "协方差近奇异：组合里存在高度共线资产，数学上难以求稳逆，"
            "金融含义是风险未真正分散；已用岭回归给出近似权重。"
            if singular
            else "对日度协方差矩阵求逆得到解析最优权重；负权重表示空头，可看 long-only 投影。"
        ),
    }



def _corr_from_cov(cov: list[list[float]]) -> list[list[float]]:
    m = len(cov)
    out = [[0.0] * m for _ in range(m)]
    for i in range(m):
        si = math.sqrt(max(cov[i][i], 0.0))
        for j in range(m):
            sj = math.sqrt(max(cov[j][j], 0.0))
            out[i][j] = cov[i][j] / (si * sj) if si > 1e-18 and sj > 1e-18 else 0.0
    return out


def _equity_mdd_from_rets(rets: list[float]) -> float:
    eq = [1.0]
    for r in rets:
        eq.append(eq[-1] * math.exp(r))
    dd = max_drawdown(eq)
    # max_drawdown returns dict with max_drawdown as fraction loss (positive)
    v = dd.get("max_drawdown")
    if v is None:
        v = dd.get("max_drawdown_pct")
        if v is not None:
            v = float(v) / 100.0
    return float(v or 0.0)


def _iter_long_simplex(m: int, step: float):
    """生成多头单纯形权重：w_i≥0, sum=1。"""
    if m <= 0:
        return
    if m == 1:
        yield [1.0]
        return
    n_steps = max(1, int(round(1.0 / step)))

    def rec(remain_slots: int, remain: int):
        if remain_slots == 1:
            yield [remain]
            return
        for k in range(remain + 1):
            for tail in rec(remain_slots - 1, remain - k):
                yield [k] + tail

    for parts in rec(m, n_steps):
        yield [p / n_steps for p in parts]


def safe_hedge_allocate(
    symbols: list[str],
    returns_mat: dict[str, list[float]],
    cov: list[list[float]],
    loss_limit: float = 0.05,
    grid_step: float = 0.05,
    allow_cash: bool = True,
) -> dict:
    """
    极度保守仓位：硬约束历史最大回撤 ≤ loss_limit（默认 5%），
    自动识别负相关对冲对，在可行域内选波动最低的多头(+现金)组合。

    若全仓投资均突破底线，则按「风险资产缩放 + 现金垫」卡死亏损上限。
    """
    m = len(symbols)
    if m < 1:
        return {"ok": False, "note": "至少需要 1 只资产"}
    loss_limit = float(loss_limit or 0.05)
    if not (0.005 <= loss_limit <= 0.5):
        loss_limit = 0.05
    grid_step = max(0.02, min(float(grid_step or 0.05), 0.2))

    n = len(returns_mat[symbols[0]])
    if n < 30:
        return {"ok": False, "note": "收益序列过短，无法做安全仓位优化"}

    corr = _corr_from_cov(cov)
    # 负相关对冲对：corr 越小越强
    pairs = []
    for i in range(m):
        for j in range(i + 1, m):
            pairs.append({
                "a": symbols[i],
                "b": symbols[j],
                "corr": _round(corr[i][j], 4),
                "hedge_score": _round(-corr[i][j], 4),  # 越大越适合对冲
            })
    pairs.sort(key=lambda x: x["corr"])
    top_hedge = pairs[0] if pairs else None

    mu = [mean(returns_mat[s]) for s in symbols]
    mats = [returns_mat[s] for s in symbols]

    def eval_weights(w_risky: list[float], cash: float) -> dict:
        # w_risky 已是风险仓内占比；总仓 = (1-cash)*w_risky_i
        scale = 1.0 - cash
        w = [scale * x for x in w_risky]
        rets = []
        for t in range(n):
            rets.append(sum(w[i] * mats[i][t] for i in range(m)))
        mdd = _equity_mdd_from_rets(rets)
        stats = _port_stats(w, mu, cov)
        # 对冲利用度：负相关对上的最小权重积（鼓励两边都有仓）
        hedge_util = 0.0
        if top_hedge:
            ia = symbols.index(top_hedge["a"])
            ib = symbols.index(top_hedge["b"])
            if top_hedge["corr"] < 0:
                hedge_util = min(w[ia], w[ib]) * (-top_hedge["corr"])
        return {
            "w": w,
            "cash": cash,
            "mdd": mdd,
            "vol": stats["vol_annual"] or 0.0,
            "ret": stats["ann_return_compound"] or 0.0,
            "stats": stats,
            "hedge_util": hedge_util,
            "feasible": mdd <= loss_limit + 1e-9,
        }

    # 1) 网格：纯风险仓多头单纯形 × 现金比例
    cash_grid = [0.0]
    if allow_cash:
        cash_grid = [i / 20.0 for i in range(0, 21)]  # 0%, 5%, ..., 100%

    candidates: list[dict] = []
    for w_r in _iter_long_simplex(m, grid_step):
        for cash in cash_grid:
            if cash >= 1.0 - 1e-12:
                # 全现金
                candidates.append({
                    "w": [0.0] * m,
                    "cash": 1.0,
                    "mdd": 0.0,
                    "vol": 0.0,
                    "ret": 0.0,
                    "stats": _port_stats([0.0] * m, mu, cov),
                    "hedge_util": 0.0,
                    "feasible": True,
                })
                continue
            candidates.append(eval_weights(w_r, cash))

    feasible = [c for c in candidates if c["feasible"]]

    # 2) 若网格无可行（极端波动），对每个风险结构做连续缩放找刚卡在 loss_limit 的现金
    if not feasible and allow_cash:
        for w_r in _iter_long_simplex(m, max(grid_step, 0.1)):
            # 全仓该结构的 MDD
            full = eval_weights(w_r, 0.0)
            if full["mdd"] <= loss_limit:
                feasible.append(full)
                continue
            if full["mdd"] <= 1e-12:
                continue
            # 近似：回撤随敞口近似线性 → scale = limit/mdd
            scale = min(1.0, loss_limit / full["mdd"])
            cash = 1.0 - scale
            scaled = eval_weights(w_r, cash)
            if scaled["feasible"]:
                feasible.append(scaled)

    if not feasible:
        # 最后兜底：全现金
        best = eval_weights([1.0 / m] * m, 1.0)
        best["w"] = [0.0] * m
        best["cash"] = 1.0
        best["mdd"] = 0.0
        best["feasible"] = True
        method = "cash_only_fallback"
        note = (
            f"在样本期无法找到历史 MDD≤{loss_limit*100:.1f}% 的风险仓位，"
            "已回退为全现金。请换更稳的资产或放宽底线。"
        )
    else:
        # 硬约束 MDD≤limit 下：尽量满仓投资，再选更低波动，并优先负相关对冲腿
        # （不再把「MDD 更小」当作第一目标，否则会塌缩成全现金）
        invested = [c for c in feasible if c["cash"] < 1.0 - 1e-9]
        pool = invested if invested else feasible
        pool.sort(
            key=lambda c: (
                c["cash"],
                c["vol"] if c["vol"] is not None else 0.0,
                -c["hedge_util"],
                c["mdd"],
                -(c["ret"] or 0.0),
            )
        )
        best = pool[0]
        near = [c for c in pool if c["cash"] <= best["cash"] + 0.05 + 1e-12]
        if top_hedge and top_hedge["corr"] < -0.05:
            near.sort(
                key=lambda c: (
                    -c["hedge_util"],
                    c["vol"] if c["vol"] is not None else 0.0,
                    c["mdd"],
                    -(c["ret"] or 0.0),
                )
            )
            hedged = [c for c in near if c["hedge_util"] > 0]
            best = hedged[0] if hedged else near[0]
        else:
            near.sort(
                key=lambda c: (
                    c["vol"] if c["vol"] is not None else 0.0,
                    c["mdd"],
                    -(c["ret"] or 0.0),
                )
            )
            best = near[0]
        method = "grid_mdd_hard_cap"
        note = (
            f"硬约束：样本期组合最大回撤 ≤ {loss_limit*100:.1f}%。"
            "在不破底线的前提下尽量满仓；同档仓位选更低波动，并自动加重负相关对冲腿。"
            "仅当满仓会破线时才加现金垫。"
        )

    w_best = best["w"]
    rows = []
    for i, sym in enumerate(symbols):
        rows.append({
            "symbol": sym,
            "side": "long" if w_best[i] >= 0 else "short",
            "weight": _round(w_best[i], 6),
            "weight_pct": _round(w_best[i] * 100, 4),
        })
    if best["cash"] > 1e-8:
        rows.append({
            "symbol": "CASH",
            "side": "long",
            "weight": _round(best["cash"], 6),
            "weight_pct": _round(best["cash"] * 100, 4),
        })

    # 压力：主对冲对在 A 最差分位日，B 与组合表现
    stress = None
    if top_hedge and m >= 2:
        ia = symbols.index(top_hedge["a"])
        ib = symbols.index(top_hedge["b"])
        a_rets = mats[ia]
        # A 最差 5% 交易日
        order = sorted(range(n), key=lambda t: a_rets[t])
        k = max(1, n // 20)
        worst_idx = order[:k]
        avg_a = mean(a_rets[t] for t in worst_idx)
        avg_b = mean(mats[ib][t] for t in worst_idx)
        avg_p = mean(sum(w_best[i] * mats[i][t] for i in range(m)) for t in worst_idx)
        stress = {
            "when": f"{top_hedge['a']} 最差 5% 交易日",
            "avg_return_a": _round(avg_a, 6),
            "avg_return_b": _round(avg_b, 6),
            "avg_return_portfolio": _round(avg_p, 6),
            "interpretation": (
                f"当 {top_hedge['a']} 暴跌时，{top_hedge['b']} 平均日收益 "
                f"{'同向' if avg_a * avg_b > 0 else '反向'}；"
                "组合日均跌幅被对冲缓冲。"
                if top_hedge["corr"] < 0
                else "样本中该对相关性非负，对冲效果有限，更多依靠降仓/现金。"
            ),
        }

    safety_margin = loss_limit - best["mdd"]
    return {
        "ok": True,
        "method": method,
        "loss_limit": loss_limit,
        "loss_limit_pct": _round(loss_limit * 100, 2),
        "hist_mdd": _round(best["mdd"], 6),
        "hist_mdd_pct": _round(best["mdd"] * 100, 4),
        "safety_margin_pct": _round(safety_margin * 100, 4),
        "cash_weight": _round(best["cash"], 6),
        "cash_weight_pct": _round(best["cash"] * 100, 4),
        "weights": rows,
        "stats": best["stats"],
        "feasible_count": len(feasible) if feasible else 0,
        "hedge_pairs": pairs[: min(5, len(pairs))],
        "primary_hedge": top_hedge,
        "stress_test": stress,
        "objective": "硬约束 MDD≤limit → 尽量满仓 → 最小波动 → 最大对冲利用 → 略高收益",
        "note": note,
        "disclaimer": (
            "回撤约束基于样本期历史路径，不保证未来 MDD 不超过该底线；"
            "极端新政/流动性危机下负相关也可能失效。"
        ),
    }


# ----------------------------------------------------------------------
# 信用评级迁移（示意用马尔可夫链，非官方最新迁移矩阵）
# ----------------------------------------------------------------------
CREDIT_RATINGS = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "D"]

# 示意年度转移概率（行=期初，列=期末），D 为吸收态
# 数值仅用于教学演示，不替代评级机构正式矩阵
_CREDIT_P = [
    # AAA   AA     A      BBB    BB     B      CCC    D
    [0.90, 0.06, 0.02, 0.01, 0.005, 0.003, 0.001, 0.001],  # AAA
    [0.02, 0.88, 0.06, 0.02, 0.01, 0.005, 0.003, 0.002],   # AA
    [0.01, 0.04, 0.85, 0.06, 0.02, 0.01, 0.005, 0.005],    # A
    [0.005, 0.01, 0.05, 0.82, 0.07, 0.025, 0.01, 0.01],    # BBB
    [0.002, 0.005, 0.02, 0.06, 0.78, 0.08, 0.03, 0.023],   # BB
    [0.001, 0.002, 0.01, 0.03, 0.07, 0.72, 0.10, 0.067],   # B
    [0.0, 0.001, 0.005, 0.01, 0.04, 0.10, 0.60, 0.244],    # CCC
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],              # D
]


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    n, m, p = len(a), len(b[0]), len(b)
    out = [[0.0] * m for _ in range(n)]
    for i in range(n):
        for k in range(p):
            aik = a[i][k]
            for j in range(m):
                out[i][j] += aik * b[k][j]
    return out




def _matrix_power(p: list[list[float]], n: int) -> list[list[float]]:
    # identity
    m = len(p)
    result = [[1.0 if i == j else 0.0 for j in range(m)] for i in range(m)]
    base = [row[:] for row in p]
    exp = max(0, int(n))
    while exp > 0:
        if exp & 1:
            result = _matmul(result, base)
        base = _matmul(base, base)
        exp >>= 1
    return result


def credit_migration(
    years: int = 10,
    start: dict[str, float] | None = None,
    notional: float = 10_000_000_000.0,
) -> dict:
    """
    用马尔可夫转移矩阵 P^n 预测 n 年后评级分布 / 违约敞口。
    start: 各评级初始权重（或敞口占比），默认全在 A。
    """
    years = max(1, min(int(years or 1), 50))
    ratings = CREDIT_RATINGS[:]
    if not start:
        start = {"A": 1.0}
    weights = []
    for r in ratings:
        weights.append(float(start.get(r, 0.0) or 0.0))
    s = sum(weights)
    if s <= 0:
        raise PortfolioError("初始评级权重之和须 > 0")
    weights = [w / s for w in weights]

    p_n = _matrix_power(_CREDIT_P, years)
    # 行向量：dist = w₀ · Pⁿ
    dist = [sum(weights[i] * p_n[i][j] for i in range(len(ratings))) for j in range(len(ratings))]

    # one-step from each rating to D for table
    trans_rows = []
    for i, r in enumerate(ratings):
        row = {"from": r}
        for j, c in enumerate(ratings):
            row[c] = _round(_CREDIT_P[i][j], 4)
        trans_rows.append(row)

    default_idx = ratings.index("D")
    default_share = dist[default_idx]
    return {
        "years": years,
        "ratings": ratings,
        "transition_1y": trans_rows,
        "start_weights": {r: _round(weights[i], 6) for i, r in enumerate(ratings)},
        "end_weights": {r: _round(dist[i], 6) for i, r in enumerate(ratings)},
        "default_probability": _round(default_share, 6),
        "default_notional": _round(float(notional) * default_share, 2),
        "notional": notional,
        "note": (
            f"演示矩阵：对初始组合做 P^{years}。"
            "D 为吸收态。矩阵来自教学示意，非正式标普/穆迪迁移表。"
        ),
    }


def _gauss(rng: random.Random) -> float:
    # Box-Muller
    u1 = max(rng.random(), 1e-12)
    u2 = rng.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _student_t(rng: random.Random, df: float = 5.0) -> float:
    """t = Z / sqrt(χ²_df / df)。"""
    z = _gauss(rng)
    # χ²(df) ≈ Gamma(df/2, 1/2); for integer df use sum of squares
    k = max(3, int(round(df)))
    chi = sum(_gauss(rng) ** 2 for _ in range(k))
    return z / math.sqrt(max(chi / k, 1e-18))


def _cholesky(cov: list[list[float]]) -> list[list[float]] | None:
    n = len(cov)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                v = cov[i][i] - s
                if v <= 1e-18:
                    # ridge
                    v = 1e-10
                L[i][j] = math.sqrt(v)
            else:
                if abs(L[j][j]) < 1e-18:
                    return None
                L[i][j] = (cov[i][j] - s) / L[j][j]
    return L


def _fit_garch11(returns: list[float]) -> tuple[float, float, float, float]:
    """粗网格拟合 GARCH(1,1)，返回 omega, alpha, beta, last_var。"""
    n = len(returns)
    unc = sum(r * r for r in returns) / n
    best = None
    best_ll = -1e18
    for ai in range(2, 16):
        alpha = ai / 100.0
        for bi in range(70, 96):
            beta = bi / 100.0
            if alpha + beta >= 0.999:
                continue
            omega = unc * max(1e-8, 1 - alpha - beta)
            var = unc
            ll = 0.0
            ok = True
            for r in returns:
                var = omega + alpha * (r * r) + beta * var
                if var <= 1e-16:
                    ok = False
                    break
                ll += -0.5 * (math.log(var) + (r * r) / var)
            if ok and ll > best_ll:
                best_ll = ll
                last_r = returns[-1]
                next_var = omega + alpha * (last_r * last_r) + beta * var
                best = (omega, alpha, beta, next_var)
    if not best:
        return unc * 0.05, 0.05, 0.90, unc
    return best


def _path_terminal_and_mdd(
    rets: list[float],
) -> tuple[float, float, list[tuple[int, float]], int | None, bool]:
    """
    返回：
      terminal, mdd, marks,
      recovery_days: 从 MDD 谷底回到前期峰值所需交易日（仅当 MDD>10%；未恢复则为 None）,
      unrecovered: MDD>10% 且模拟期内未回到峰值
    """
    v = 1.0
    peak = 1.0
    mdd = 0.0
    trough_t = 0
    peak_at_mdd = 1.0
    marks: list[tuple[int, float]] = []
    for t, r in enumerate(rets, start=1):
        v *= math.exp(r)
        if v > peak:
            peak = v
        dd = 1.0 - v / peak if peak > 0 else 0.0
        if dd > mdd:
            mdd = dd
            trough_t = t
            peak_at_mdd = peak
        marks.append((t, v))

    recovery_days: int | None = None
    unrecovered = False
    if mdd > 0.10:
        recovered = False
        for t, vv in marks:
            if t <= trough_t:
                continue
            if vv >= peak_at_mdd - 1e-12:
                recovery_days = t - trough_t
                recovered = True
                break
        unrecovered = not recovered
    return v, mdd, marks, recovery_days, unrecovered


def _summarize_paths(
    terminals: list[float],
    mdds: list[float],
    cap: float,
    fan_by_day: dict[int, list[float]],
    recovery_days: list[int | None] | None = None,
    unrecovered_flags: list[bool] | None = None,
) -> dict:
    # 保持 (终值, MDD) 配对，再分别排序做分位数
    n = min(len(terminals), len(mdds))
    if n == 0:
        return {}
    pairs = list(zip(terminals[:n], mdds[:n]))
    terms_sorted = sorted(t for t, _ in pairs)
    mdds_sorted = sorted(d for _, d in pairs)

    def prob_term(cond) -> float:
        return sum(1 for t, _ in pairs if cond(t)) / n

    def prob_mdd(cond) -> float:
        return sum(1 for _, d in pairs if cond(d)) / n

    def prob_joint(cond) -> float:
        return sum(1 for t, d in pairs if cond(t, d)) / n

    p_mdd10 = prob_mdd(lambda d: d > 0.10)
    p_mdd20 = prob_mdd(lambda d: d > 0.20)
    # 回撤后最终盈利（联合概率）与条件恢复率
    p_rec10 = prob_joint(lambda t, d: d > 0.10 and t > 1.0)
    p_rec20 = prob_joint(lambda t, d: d > 0.20 and t > 1.0)
    cond_rec10 = (p_rec10 / p_mdd10) if p_mdd10 > 1e-12 else None
    cond_rec20 = (p_rec20 / p_mdd20) if p_mdd20 > 1e-12 else None

    # CVaR 5%：最差 5% 终值的平均（报告为相对起点的平均亏损比例）
    var5 = _percentile(terms_sorted, 0.05)
    tail = [t for t in terms_sorted if t <= var5 + 1e-15]
    if not tail:
        tail = terms_sorted[: max(1, n // 20)]
    cvar_mult = mean(tail)
    cvar_loss = 1.0 - cvar_mult  # 正数表示平均亏损幅度

    # Time to Recovery：MDD>10% 路径中，从谷底回到前期峰值的天数
    rec_times: list[int] = []
    n_mdd10 = 0
    n_unrec = 0
    if recovery_days is not None and unrecovered_flags is not None:
        for i in range(n):
            if mdds[i] <= 0.10:
                continue
            n_mdd10 += 1
            if unrecovered_flags[i] or recovery_days[i] is None:
                n_unrec += 1
            else:
                rec_times.append(int(recovery_days[i]))
    unrecovered_rate = (n_unrec / n_mdd10) if n_mdd10 else None
    if rec_times:
        rec_sorted = sorted(rec_times)
        ttr_median = _percentile([float(x) for x in rec_sorted], 0.50)
        ttr_p90 = _percentile([float(x) for x in rec_sorted], 0.90)
    else:
        ttr_median = None
        ttr_p90 = None

    fan = []
    for day in sorted(fan_by_day.keys()):
        arr = sorted(fan_by_day[day])
        fan.append({
            "day": day,
            "p05": _round(_percentile(arr, 0.05) * cap, 4),
            "p25": _round(_percentile(arr, 0.25) * cap, 4),
            "p50": _round(_percentile(arr, 0.50) * cap, 4),
            "p75": _round(_percentile(arr, 0.75) * cap, 4),
            "p95": _round(_percentile(arr, 0.95) * cap, 4),
        })

    return {
        "median_mult": _round(_percentile(terms_sorted, 0.50), 6),
        "p05_mult": _round(_percentile(terms_sorted, 0.05), 6),
        "p95_mult": _round(_percentile(terms_sorted, 0.95), 6),
        "mean_mult": _round(mean(terms_sorted), 6),
        "median_value": _round(_percentile(terms_sorted, 0.50) * cap, 4),
        "p05_value": _round(_percentile(terms_sorted, 0.05) * cap, 4),
        "p95_value": _round(_percentile(terms_sorted, 0.95) * cap, 4),
        # 终值口径：P(期末亏损 > x) —— 不是“永久性损失”
        "prob_profit": _round(prob_term(lambda x: x > 1.0), 4),
        "prob_terminal_loss_5pct": _round(prob_term(lambda x: x < 0.95), 4),
        "prob_terminal_loss_10pct": _round(prob_term(lambda x: x < 0.90), 4),
        "prob_terminal_gain_10pct": _round(prob_term(lambda x: x > 1.10), 4),
        "prob_terminal_gain_20pct": _round(prob_term(lambda x: x > 1.20), 4),
        "prob_loss_10pct": _round(prob_term(lambda x: x < 0.90), 4),
        "prob_gain_10pct": _round(prob_term(lambda x: x > 1.10), 4),
        "prob_loss_5pct": _round(prob_term(lambda x: x < 0.95), 4),
        "prob_gain_20pct": _round(prob_term(lambda x: x > 1.20), 4),
        # 路径最大回撤
        "prob_mdd_gt_10pct": _round(p_mdd10, 4),
        "prob_mdd_gt_20pct": _round(p_mdd20, 4),
        "expected_mdd": _round(mean(mdds_sorted), 4),
        "mdd_p50": _round(_percentile(mdds_sorted, 0.50), 4),
        "mdd_p95": _round(_percentile(mdds_sorted, 0.95), 4),
        # Recovery
        "prob_mdd10_and_profit": _round(p_rec10, 4),
        "prob_mdd20_and_profit": _round(p_rec20, 4),
        "recovery_rate_mdd10": _round(cond_rec10, 4) if cond_rec10 is not None else None,
        "recovery_rate_mdd20": _round(cond_rec20, 4) if cond_rec20 is not None else None,
        # Time to Recovery（回到回撤前峰值）
        "ttr_median_days": _round(ttr_median, 1) if ttr_median is not None else None,
        "ttr_p90_days": _round(ttr_p90, 1) if ttr_p90 is not None else None,
        "unrecovered_rate_mdd10": _round(unrecovered_rate, 4) if unrecovered_rate is not None else None,
        # 尾部 CVaR（最差 5% 终值的平均亏损幅度）
        "var_5pct_mult": _round(var5, 6),
        "cvar_5pct_mult": _round(cvar_mult, 6),
        "cvar_5pct_loss": _round(cvar_loss, 4),
        "cvar_5pct_value": _round(cvar_mult * cap, 4),
        "fan": fan,
    }


def _model_consistency(models: dict[str, dict]) -> dict:
    """比较四模型关键概率是否同向、分歧多大。"""
    keys = ["prob_profit", "prob_terminal_loss_10pct", "prob_mdd_gt_10pct", "prob_mdd10_and_profit"]
    rows = {}
    for k in keys:
        vals = []
        for name, m in models.items():
            if m and m.get(k) is not None:
                vals.append((name, float(m[k])))
        if len(vals) < 2:
            continue
        nums = [v for _, v in vals]
        spread = max(nums) - min(nums)
        bullish = sum(1 for v in nums if v >= 0.5)
        bearish = sum(1 for v in nums if v < 0.5)
        if k == "prob_profit":
            agree = bullish == len(nums) or bearish == len(nums)
        else:
            # 风险概率：都偏高(>0.25)或都偏低(<0.25)算大致一致方向
            high = sum(1 for v in nums if v >= 0.25)
            low = sum(1 for v in nums if v < 0.25)
            agree = high == len(nums) or low == len(nums)
        rows[k] = {
            "values": {n: _round(v, 4) for n, v in vals},
            "spread": _round(spread, 4),
            "agree_direction": agree,
        }

    spreads = [rows[k]["spread"] for k in rows]
    agrees = [rows[k]["agree_direction"] for k in rows]
    max_spread = max(spreads) if spreads else 0.0
    if max_spread <= 0.08 and all(agrees):
        level, label = "green", "模型一致"
        advice = "四模型方向大体一致，分歧较小；仍是情景分布，不是交易信号。"
    elif max_spread <= 0.15 or sum(agrees) >= len(agrees) - 1:
        level, label = "yellow", "模型分歧"
        advice = "模型间存在可见分歧，不宜给出强方向性判断；更应关注回撤与尾部区间。"
    else:
        level, label = "red", "模型严重分歧"
        advice = "不同假设下结论差较大；当前不适合用单一概率做仓位决策。"

    # 自动摘要
    profits = []
    mdds = []
    for name, m in models.items():
        if m.get("prob_profit") is not None:
            profits.append(float(m["prob_profit"]))
        if m.get("prob_mdd_gt_10pct") is not None:
            mdds.append(float(m["prob_mdd_gt_10pct"]))
    direction = "中性"
    if profits:
        mid = sum(profits) / len(profits)
        if mid >= 0.55:
            direction = "偏多"
        elif mid <= 0.45:
            direction = "中性偏弱"
    path_risk = "高" if mdds and sum(mdds) / len(mdds) >= 0.6 else ("中" if mdds and sum(mdds)/len(mdds) >= 0.35 else "低")
    summary = f"模型共识：{label}；方向：{direction}；路径回撤风险：{path_risk}。"
    return {
        "level": level,
        "label": label,
        "advice": advice,
        "summary": summary,
        "direction": direction,
        "path_risk": path_risk,
        "max_spread": _round(max_spread, 4),
        "metrics": rows,
    }



def _collect_model_paths(path_iter, n_sims: int, steps: list[int]):
    """path_iter() -> list[float] 日收益路径。"""
    term, mdd, rec, unrec = [], [], [], []
    fan = {s: [] for s in steps}
    want = set(steps)
    for _ in range(n_sims):
        path = path_iter()
        t, d, marks, rd, ur = _path_terminal_and_mdd(path)
        term.append(t)
        mdd.append(d)
        rec.append(rd)
        unrec.append(ur)
        for day, v in marks:
            if day in want:
                fan[day].append(v)
    return term, mdd, fan, rec, unrec

def project_future(
    returns: list[float],
    horizon_days: int = 63,
    n_sims: int = 4000,
    capital: float = 1.0,
    seed: int = 42,
    asset_mats: list[list[float]] | None = None,
    weights: list[float] | None = None,
    cov: list[list[float]] | None = None,
    block_size: int = 10,
    drift_mode: str = "historical",
) -> dict:
    """
    四模型概率展望（情景分布，非点预测）：
      1) iid Bootstrap
      2) GBM（正态对数收益）
      3) Block Bootstrap（保留波动聚集/短期结构）
      4) GARCH(1,1) + Student-t（波动聚集 + 厚尾）
    若提供多资产收益与协方差，GBM/GARCH 路径用 Cholesky 相关冲击，组合收益 = w·r。
    同时报告：终值跌幅概率 vs 路径最大回撤概率（二者不同）。
    """
    if len(returns) < 30:
        return {"ok": False, "note": "收益样本不足，无法做概率展望"}
    horizon_days = max(1, min(int(horizon_days or 63), 252))
    n_sims = max(500, min(int(n_sims or 4000), 12000))
    block_size = max(3, min(int(block_size or 10), 21))
    cap = float(capital) if capital and capital > 0 else 1.0
    mu = mean(returns)
    sig = pstdev(returns) if len(returns) > 1 else 0.0
    drift_mode = (drift_mode or "historical").strip().lower()
    if drift_mode not in ("historical", "zero"):
        drift_mode = "historical"
    # 主模拟用的收益：zero 模式去掉样本均值，专看波动/路径风险，避免“牛市样本→万物看涨”
    sim_returns = returns
    sim_asset_mats = asset_mats
    sim_mu = mu
    if drift_mode == "zero":
        sim_returns = [r - mu for r in returns]
        sim_mu = 0.0
        if asset_mats is not None:
            sim_asset_mats = []
            for col in asset_mats:
                mcol = mean(col) if col else 0.0
                sim_asset_mats.append([x - mcol for x in col])
    rng = random.Random(seed)
    n = len(sim_returns)
    steps = sorted({1, max(1, horizon_days // 4), max(1, horizon_days // 2), horizon_days})

    use_multi = (
        asset_mats is not None
        and weights is not None
        and cov is not None
        and len(asset_mats) >= 2
        and len(asset_mats) == len(weights)
        and len(cov) == len(weights)
    )
    L = _cholesky(cov) if use_multi else None
    if use_multi and L is None:
        use_multi = False
    # 多资产矩阵与权重长度以 sim 为准
    if drift_mode == "zero" and sim_asset_mats is not None:
        asset_mats = sim_asset_mats
    asset_mus = [mean(col) for col in asset_mats] if use_multi else []

    def record_fan(fan_map: dict[int, list[float]], marks: list[tuple[int, float]]) -> None:
        want = set(steps)
        for t, v in marks:
            if t in want:
                fan_map[t].append(v)

    # ---- 1) iid Bootstrap ----
    term_b, mdd_b, fan_b, rec_b, unrec_b = _collect_model_paths(
        lambda: [sim_returns[rng.randrange(n)] for _ in range(horizon_days)],
        n_sims,
        steps,
    )
    boot = _summarize_paths(term_b, mdd_b, cap, fan_b, rec_b, unrec_b)
    boot["name"] = "Bootstrap"
    boot["assumption"] = "未来日收益独立同分布于历史（打乱时间结构）"

    # ---- 3) Block Bootstrap ----
    term_blk, mdd_blk, rec_blk, unrec_blk = [], [], [], []
    fan_blk: dict[int, list[float]] = {s: [] for s in steps}
    max_start = max(0, n - block_size)
    for _ in range(n_sims):
        path: list[float] = []
        if use_multi:
            # 同步块：同一起点切多资产，再加权 → 保留相关 + 块内时序
            while len(path) < horizon_days:
                st = rng.randint(0, max_start) if max_start > 0 else 0
                for k in range(block_size):
                    if len(path) >= horizon_days:
                        break
                    idx = min(st + k, n - 1)
                    r = sum(weights[i] * asset_mats[i][idx] for i in range(len(weights)))
                    path.append(r)
        else:
            while len(path) < horizon_days:
                st = rng.randint(0, max_start) if max_start > 0 else 0
                path.extend(sim_returns[st : st + block_size])
            path = path[:horizon_days]
        term, mdd, marks, rd, ur = _path_terminal_and_mdd(path)
        term_blk.append(term)
        mdd_blk.append(mdd)
        rec_blk.append(rd)
        unrec_blk.append(ur)
        for day, v in marks:
            if day in set(steps):
                fan_blk[day].append(v)
    block = _summarize_paths(term_blk, mdd_blk, cap, fan_blk, rec_blk, unrec_blk)
    block["name"] = "Block Bootstrap"
    block["assumption"] = f"连续抽取 {block_size} 日收益块，部分保留波动聚集与短期结构"
    block["block_size"] = block_size

    # ---- 2) GBM：解析终值 + 模拟路径拿 MDD / 相关冲击 ----
    drift = (sim_mu - 0.5 * sig * sig) * horizon_days
    vol_h = sig * math.sqrt(horizon_days)

    def norm_cdf(z: float) -> float:
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    def gbm_prob_above(threshold_mult: float) -> float:
        if vol_h <= 1e-18:
            return 1.0 if drift >= math.log(max(threshold_mult, 1e-12)) else 0.0
        z = (math.log(max(threshold_mult, 1e-12)) - drift) / vol_h
        return 1.0 - norm_cdf(z)

    term_g, mdd_g, rec_g, unrec_g = [], [], [], []
    fan_g: dict[int, list[float]] = {s: [] for s in steps}
    for _ in range(n_sims):
        path = []
        for _t in range(horizon_days):
            if use_multi and L is not None:
                z = [_gauss(rng) for _ in range(len(weights))]
                shocks = _mat_vec(L, z)
                r = sum(
                    weights[i] * (asset_mus[i] - 0.5 * cov[i][i] + shocks[i])
                    for i in range(len(weights))
                )
            else:
                r = sim_mu - 0.5 * sig * sig + sig * _gauss(rng)
            path.append(r)
        term, mdd, marks, rd, ur = _path_terminal_and_mdd(path)
        term_g.append(term)
        mdd_g.append(mdd)
        rec_g.append(rd)
        unrec_g.append(ur)
        for day, v in marks:
            if day in set(steps):
                fan_g[day].append(v)
    gbm = _summarize_paths(term_g, mdd_g, cap, fan_g, rec_g, unrec_g)
    # 终值概率用解析式覆盖（更稳），MDD 仍用模拟
    gbm["prob_profit"] = _round(gbm_prob_above(1.0), 4)
    gbm["prob_terminal_loss_5pct"] = _round(1.0 - gbm_prob_above(0.95), 4)
    gbm["prob_terminal_loss_10pct"] = _round(1.0 - gbm_prob_above(0.90), 4)
    gbm["prob_terminal_gain_10pct"] = _round(gbm_prob_above(1.10), 4)
    gbm["prob_terminal_gain_20pct"] = _round(gbm_prob_above(1.20), 4)
    gbm["prob_loss_10pct"] = gbm["prob_terminal_loss_10pct"]
    gbm["prob_gain_10pct"] = gbm["prob_terminal_gain_10pct"]
    gbm["prob_loss_5pct"] = gbm["prob_terminal_loss_5pct"]
    gbm["prob_gain_20pct"] = gbm["prob_terminal_gain_20pct"]
    gbm["median_mult"] = _round(math.exp(drift), 6)
    gbm["p05_mult"] = _round(math.exp(drift - 1.64485 * vol_h), 6)
    gbm["p95_mult"] = _round(math.exp(drift + 1.64485 * vol_h), 6)
    gbm["median_value"] = _round(gbm["median_mult"] * cap, 4)
    gbm["p05_value"] = _round(gbm["p05_mult"] * cap, 4)
    gbm["p95_value"] = _round(gbm["p95_mult"] * cap, 4)
    gbm["name"] = "GBM"
    gbm["assumption"] = "对数收益正态" + ("；多资产相关冲击 Σ=LL′" if use_multi else "")

    # ---- 4) GARCH + Student-t ----
    omega, alpha, beta, var0 = _fit_garch11(sim_returns)
    df_t = 5.0
    term_gt, mdd_gt, rec_gt, unrec_gt = [], [], [], []
    fan_gt: dict[int, list[float]] = {s: [] for s in steps}
    for _ in range(n_sims):
        path = []
        var = var0
        for _t in range(horizon_days):
            if use_multi and L is not None:
                z = [_student_t(rng, df_t) for _ in range(len(weights))]
                scale = math.sqrt(max(df_t / (df_t - 2.0), 1e-8))
                z = [zi / scale for zi in z]
                shocks = _mat_vec(L, z)
                innov = sum(weights[i] * shocks[i] for i in range(len(weights)))
                unit = innov / sig if sig > 1e-12 else innov
                r = sim_mu + math.sqrt(max(var, 1e-16)) * unit
            else:
                eps = _student_t(rng, df_t)
                eps /= math.sqrt(max(df_t / (df_t - 2.0), 1e-8))
                r = sim_mu + math.sqrt(max(var, 1e-16)) * eps
            path.append(r)
            var = omega + alpha * (r - sim_mu) ** 2 + beta * var
        term, mdd, marks, rd, ur = _path_terminal_and_mdd(path)
        term_gt.append(term)
        mdd_gt.append(mdd)
        rec_gt.append(rd)
        unrec_gt.append(ur)
        for day, v in marks:
            if day in set(steps):
                fan_gt[day].append(v)
    garch_t = _summarize_paths(term_gt, mdd_gt, cap, fan_gt, rec_gt, unrec_gt)
    garch_t["name"] = "GARCH-t"
    garch_t["assumption"] = "GARCH(1,1) 波动聚集 + Student-t 厚尾"
    garch_t["garch_params"] = {
        "omega": _round(omega, 8),
        "alpha": _round(alpha, 4),
        "beta": _round(beta, 4),
        "df": df_t,
    }

    models = {
        "bootstrap": boot,
        "gbm": gbm,
        "block_bootstrap": block,
        "garch_t": garch_t,
    }
    consistency = _model_consistency({
        "Bootstrap": boot,
        "GBM": gbm,
        "Block": block,
        "GARCH-t": garch_t,
    })

    # 主扇形用 Block Bootstrap（更贴近真实路径结构）
    primary_fan = block.get("fan") or boot.get("fan") or []

    # 若当前是 historical，额外跑一版去漂移 Block（较少模拟）作对照，解释“为什么都偏多”
    zero_drift = None
    if drift_mode == "historical" and abs(mu) > 1e-8:
        z_rets = [r - mu for r in returns]
        z_n = len(z_rets)
        z_max = max(0, z_n - block_size)
        z_sims = min(n_sims, 2000)

        def _z_path():
            path: list[float] = []
            while len(path) < horizon_days:
                st = rng.randint(0, z_max) if z_max > 0 else 0
                path.extend(z_rets[st : st + block_size])
            return path[:horizon_days]

        zt, zm, zf, zr, zu = _collect_model_paths(_z_path, z_sims, steps)
        zero_drift = _summarize_paths(zt, zm, cap, zf, zr, zu)
        zero_drift["name"] = "Block（μ=0 去漂移）"
        zero_drift["assumption"] = "去掉样本均值后只保留波动结构，用于风险视角对照"

    mu_ann = mu * TRADING_DAYS
    if abs(mu_ann) >= 0.08:
        drift_bias = (
            f"样本期日均收益约 {mu_ann*100:.1f}%/年（折算），正漂移会把四模型都推向「偏多」。"
            "换票若同处牛市样本，结论会看起来很像——这是历史外推，不是选股 alpha。"
        )
    elif abs(mu_ann) <= 0.02:
        drift_bias = "样本期漂移接近 0，展望更接近纯波动/路径风险视角。"
    else:
        drift_bias = "样本期存在一定漂移，盈利概率会受均值外推影响。"

    if drift_mode == "zero":
        drift_bias = "当前为去漂移模式（μ=0）：盈利概率接近 50% 附近属正常；请重点看 MDD / CVaR / 恢复时间。"

    return {
        "ok": True,
        "method": "bootstrap + block + GBM + GARCH-t",
        "horizon_days": horizon_days,
        "n_sims": n_sims,
        "capital": cap,
        "drift_mode": drift_mode,
        "mu_daily": _round(mu),
        "mu_annual_approx": _round(mu_ann, 6),
        "sigma_daily": _round(sig),
        "sigma_annual": _round(_annualize_vol(sig)),
        "drift_bias": drift_bias,
        "zero_drift": zero_drift,
        "multivariate": bool(use_multi),
        "block_size": block_size,
        "bootstrap": boot,
        "gbm": gbm,
        "block_bootstrap": block,
        "garch_t": garch_t,
        "models": [
            {"key": "bootstrap", **{k: v for k, v in boot.items() if k != "fan"}},
            {"key": "gbm", **{k: v for k, v in gbm.items() if k != "fan"}},
            {"key": "block_bootstrap", **{k: v for k, v in block.items() if k != "fan"}},
            {"key": "garch_t", **{k: v for k, v in garch_t.items() if k != "fan"}},
        ],
        "consistency": consistency,
        "fan": primary_fan,
        "definitions": {
            "prob_terminal_loss_10pct": "P(Terminal Loss > 10%)：展望期末相对起点亏损超过 10%（不是永久性损失）",
            "prob_mdd_gt_10pct": "P(MDD > 10%)：路径最大回撤超过 10%",
            "prob_mdd10_and_profit": "回撤后最终盈利：P(MDD>10% 且期末盈利)，联合概率",
            "recovery_rate_mdd10": "条件恢复率：在 MDD>10% 路径中最终仍盈利的比例",
            "ttr_median_days": "中位恢复时间：从 MDD 谷底回到前期峰值的交易日数（仅计已恢复路径）",
            "unrecovered_rate_mdd10": "未恢复路径比例：MDD>10% 且展望期内未回到峰值",
            "cvar_5pct_loss": "CVaR 5%：最差 5% 终值情景的平均亏损幅度",
            "prob_profit": "P(S_T > S_0)：期末净值高于起点",
        },
        "note": (
            f"展望 {horizon_days} 个交易日 · 四模型情景分布。"
            "模型结果依赖历史与假设，不代表未来实际收益；"
            "请使用概率区间 / 尾部风险 / 模型一致性，而非目标价或点预测。"
        ),
        "disclaimer": "模型结果依赖历史数据和模型假设，不代表未来实际收益。",
        "tagline": "不预测一个价格，而是评估一组可能的结果。",
    }



def analyze_portfolio(
    legs: list[dict],
    window: int = 252,
    confidence: float = 0.95,
    benchmark: str | None = None,
    capital: float | None = None,
    horizon_days: int = 63,
    n_sims: int = 4000,
    drift_mode: str = "historical",
    loss_limit: float = 0.05,
) -> dict:
    if not legs:
        raise PortfolioError("请至少添加一只标的")
    if confidence not in (0.90, 0.95, 0.99):
        confidence = 0.95
    window = max(30, min(int(window or 252), 1000))

    # normalize legs（支持做多/做空：side=long|short，或负权重/负金额）
    parsed: list[dict] = []
    for raw in legs:
        sym = str((raw or {}).get("symbol") or "").strip().upper()
        if not sym:
            continue
        w = (raw or {}).get("weight")
        amt = (raw or {}).get("amount")
        side_raw = str((raw or {}).get("side") or "").strip().lower()
        try:
            w = float(w) if w is not None and w != "" else None
        except (TypeError, ValueError):
            w = None
        try:
            amt = float(amt) if amt is not None and amt != "" else None
        except (TypeError, ValueError):
            amt = None

        # 推断方向：显式 side > 负号 > 默认 long
        if side_raw in ("long", "short", "做多", "做空"):
            side = "short" if side_raw in ("short", "做空") else "long"
        elif (w is not None and w < 0) or (amt is not None and amt < 0):
            side = "short"
        else:
            side = "long"

        if w is not None:
            w = abs(w)
        if amt is not None:
            amt = abs(amt)
        if w is not None and w == 0 and amt is None:
            raise PortfolioError(f"{sym} 权重不能为 0")
        if amt is not None and amt == 0 and w is None:
            raise PortfolioError(f"{sym} 金额不能为 0")

        parsed.append({"symbol": sym, "weight": w, "amount": amt, "side": side})

    if not parsed:
        raise PortfolioError("请至少添加一只有效标的")
    if len(parsed) > 20:
        raise PortfolioError("组合最多 20 只标的")

    def _sign(p: dict) -> float:
        return -1.0 if p["side"] == "short" else 1.0

    # 按毛敞口 |w| 归一化；空头为负权重。组合日收益 r_p = Σ w_i r_i
    if all(p["amount"] is not None for p in parsed):
        signed = [_sign(p) * float(p["amount"]) for p in parsed]
        gross = sum(abs(x) for x in signed)
        if gross <= 0:
            raise PortfolioError("金额毛敞口须大于 0")
        for p, s in zip(parsed, signed):
            p["weight"] = s / gross
    elif all(p["weight"] is not None for p in parsed):
        signed = [_sign(p) * float(p["weight"]) for p in parsed]
        gross = sum(abs(x) for x in signed)
        if gross <= 0:
            raise PortfolioError("权重毛敞口须大于 0")
        for p, s in zip(parsed, signed):
            p["weight"] = s / gross
    else:
        for p in parsed:
            if p["weight"] is None and p["amount"] is not None:
                p["weight"] = abs(float(p["amount"]))
            elif p["weight"] is None:
                p["weight"] = 1.0
            else:
                p["weight"] = abs(float(p["weight"]))
        signed = [_sign(p) * float(p["weight"]) for p in parsed]
        gross = sum(abs(x) for x in signed)
        if gross <= 0:
            raise PortfolioError("权重毛敞口须大于 0")
        for p, s in zip(parsed, signed):
            p["weight"] = s / gross

    # fetch series
    series: dict[str, dict[str, float]] = {}
    prices: dict[str, float] = {}
    sources: dict[str, str] = {}
    errors: dict[str, str] = {}
    for p in parsed:
        sym = p["symbol"]
        try:
            ohlc = fetch_closes(sym, "1d", apply_live=True)
            rets = _bar_dated_returns(ohlc.get("ohlc_bars") or [], window)
            if len(rets) < 30:
                raise PortfolioError(f"{sym} 有效收益不足")
            series[sym] = rets
            px = ohlc.get("price")
            closes = ohlc.get("closes") or []
            prices[sym] = float(px) if px is not None else (float(closes[-1]) if closes else None)
            sources[sym] = ohlc.get("source") or ""
        except (OhlcError, PortfolioError) as exc:
            errors[sym] = str(exc)
        except Exception as exc:  # noqa: BLE001
            errors[sym] = str(exc)

    ok_syms = [p["symbol"] for p in parsed if p["symbol"] in series]
    if len(ok_syms) < 1:
        raise PortfolioError("所有标的拉数失败：" + "; ".join(f"{k}:{v}" for k, v in errors.items()))

    # re-normalize by gross |w| among successful legs
    weight_map = {p["symbol"]: p["weight"] for p in parsed if p["symbol"] in series}
    side_map = {p["symbol"]: p["side"] for p in parsed if p["symbol"] in series}
    gross = sum(abs(v) for v in weight_map.values())
    if gross <= 0:
        raise PortfolioError("有效标的毛敞口为 0")
    weight_map = {k: v / gross for k, v in weight_map.items()}

    # common dates
    date_sets = [set(series[s].keys()) for s in ok_syms]
    common = set.intersection(*date_sets) if date_sets else set()
    dates = sorted(common)
    if len(dates) < 30:
        raise PortfolioError(f"标的重叠交易日不足（{len(dates)}），请减少标的或缩短窗口")

    # aligned matrix
    mat = {s: [series[s][d] for d in dates] for s in ok_syms}
    n = len(dates)
    m = len(ok_syms)

    # cov matrix
    cov = [[0.0] * m for _ in range(m)]
    for i, si in enumerate(ok_syms):
        for j, sj in enumerate(ok_syms):
            cov[i][j] = _cov(mat[si], mat[sj])

    w_vec = [weight_map[s] for s in ok_syms]

    # portfolio variance = w' Σ w
    port_var = 0.0
    for i in range(m):
        for j in range(m):
            port_var += w_vec[i] * w_vec[j] * cov[i][j]
    port_var = max(port_var, 0.0)
    port_vol_daily = math.sqrt(port_var)

    # portfolio return series
    port_rets = []
    for t in range(n):
        r = sum(w_vec[i] * mat[ok_syms[i]][t] for i in range(m))
        port_rets.append(r)

    # equity curve from portfolio returns (start 1.0)
    equity = [1.0]
    for r in port_rets:
        equity.append(equity[-1] * math.exp(r))

    # marginal / component risk contribution: w_i * (Σw)_i / σ
    sigma_w = [sum(cov[i][j] * w_vec[j] for j in range(m)) for i in range(m)]
    assets = []
    weighted_vol_sum = 0.0
    for i, s in enumerate(ok_syms):
        asset_vol = math.sqrt(max(cov[i][i], 0.0))
        weighted_vol_sum += abs(w_vec[i]) * asset_vol
        mctr = sigma_w[i] / port_vol_daily if port_vol_daily > 1e-18 else 0.0
        ctr = w_vec[i] * mctr
        pct_ctr = ctr / port_vol_daily if port_vol_daily > 1e-18 else 0.0
        side = side_map.get(s) or ("short" if w_vec[i] < 0 else "long")
        assets.append({
            "symbol": s,
            "side": side,
            "side_label": "做空" if side == "short" else "做多",
            "weight": _round(w_vec[i], 6),
            "weight_pct": _round(w_vec[i] * 100, 4),
            "weight_abs_pct": _round(abs(w_vec[i]) * 100, 4),
            "price": _round(prices.get(s), 4),
            "source": sources.get(s),
            "vol_daily": _round(asset_vol),
            "vol_annual": _round(_annualize_vol(asset_vol)),
            "risk_contribution": _round(pct_ctr, 4),
            "risk_contribution_pct": _round(pct_ctr * 100, 4),
            "amount": _round((capital or 0) * w_vec[i], 2) if capital else None,
        })

    long_exp = sum(w for w in w_vec if w > 0)
    short_exp = sum(-w for w in w_vec if w < 0)
    net_exp = sum(w_vec)
    gross_exp = sum(abs(w) for w in w_vec)

    # correlation matrix
    corr = []
    for i, si in enumerate(ok_syms):
        row = {"symbol": si}
        for j, sj in enumerate(ok_syms):
            si_std = math.sqrt(max(cov[i][i], 0.0))
            sj_std = math.sqrt(max(cov[j][j], 0.0))
            c = cov[i][j] / (si_std * sj_std) if si_std and sj_std else None
            row[sj] = _round(c, 4)
        corr.append(row)

    div_ratio = (weighted_vol_sum / port_vol_daily) if port_vol_daily > 1e-18 else None

    var_h = var_cvar_historical(port_rets, confidence)
    dd = max_drawdown(equity)
    ratios = sharpe_sortino(port_rets)
    mu = mean(port_rets) if port_rets else 0.0

    bench_sym = (benchmark or _pick_benchmark(ok_syms)).upper()
    beta = {"beta": None, "benchmark": bench_sym}
    try:
        if bench_sym not in series:
            bohlc = fetch_closes(bench_sym, "1d", apply_live=False)
            b_rets_map = _bar_dated_returns(bohlc.get("ohlc_bars") or [], window)
        else:
            b_rets_map = series[bench_sym]
        b_aligned = [b_rets_map[d] for d in dates if d in b_rets_map]
        # only use overlapping — if missing dates drop from port too? use intersection
        common_b = [d for d in dates if d in b_rets_map]
        if len(common_b) >= 30:
            p_aligned = []
            b_list = []
            idx = {d: i for i, d in enumerate(dates)}
            for d in common_b:
                p_aligned.append(port_rets[idx[d]])
                b_list.append(b_rets_map[d])
            beta = beta_capm(p_aligned, b_list)
            beta["benchmark"] = bench_sym
    except Exception as exc:  # noqa: BLE001
        beta = {"beta": None, "benchmark": bench_sym, "note": f"基准失败: {exc}"}

    def _dollar(pct_ret: float | None) -> float | None:
        if pct_ret is None or capital is None:
            return None
        return _round(float(capital) * float(pct_ret), 2)

    # 年化协方差展示（日协方差 × 252）
    cov_annual = []
    for i, si in enumerate(ok_syms):
        row = {"symbol": si}
        for j, sj in enumerate(ok_syms):
            row[sj] = _round(cov[i][j] * TRADING_DAYS, 8)
        cov_annual.append(row)

    mk = markowitz_weights(ok_syms, mat, cov)
    safe = safe_hedge_allocate(
        ok_syms,
        mat,
        cov,
        loss_limit=loss_limit,
        grid_step=0.05 if len(ok_syms) <= 3 else 0.1,
        allow_cash=True,
    )

    return {
        "window": window,
        "confidence": confidence,
        "n_dates": n,
        "date_start": dates[0],
        "date_end": dates[-1],
        "capital": capital,
        "assets": assets,
        "dropped": errors,
        "correlation": corr,
        "covariance_annual": cov_annual,
        "markowitz": mk,
        "safe_allocate": safe,
        "forecast": project_future(
            port_rets,
            horizon_days=horizon_days,
            n_sims=n_sims,
            capital=float(capital) if capital and capital > 0 else 1.0,
            asset_mats=[mat[s] for s in ok_syms],
            weights=w_vec,
            cov=cov,
            block_size=10,
            drift_mode=drift_mode,
        ),
        "symbols": ok_syms,
        "portfolio": {
            "vol_daily": _round(port_vol_daily),
            "vol_annual": _round(_annualize_vol(port_vol_daily)),
            "mean_daily": _round(mu),
            "ann_return_linear": _round(mu * TRADING_DAYS, 6),
            "ann_return_compound": _round(compound_annualize(mu), 6),
            "diversification_ratio": _round(div_ratio, 4),
            "long_exposure": _round(long_exp, 6),
            "short_exposure": _round(short_exp, 6),
            "net_exposure": _round(net_exp, 6),
            "gross_exposure": _round(gross_exp, 6),
            "has_short": short_exp > 1e-12,
            "var": var_h,
            "var_dollar": _dollar(var_h.get("var_daily")),
            "cvar_dollar": _dollar(var_h.get("cvar_daily")),
            "drawdown": dd,
            "ratios": ratios,
            "beta": beta,
        },
        "note": (
            "权重按毛敞口 |w| 归一化；负权重=做空，组合收益 r=Σ w_i r_i。"
            "协方差 Σ 用于风险贡献；马科维茨对 Σ 求逆得到 GMV / 切线权重（可含空头）。"
        ),
    }
