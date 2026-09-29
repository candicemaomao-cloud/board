"""股票价格风险测算。

输入代码 → 拉日线收盘价 → 计算波动率 / VaR / CVaR / Beta / 回撤等。
含 GARCH 族（含 EGARCH/GJR）、VaR/CVaR、Fama-French、EVT-GPD、Amihud / Corwin-Schultz 等。
"""

from __future__ import annotations

import math
import random
from statistics import mean, pstdev

from datetime import datetime, timezone

from app.services.fama_french import load_ff_factors, sector_etf
from app.services.ohlc import OhlcError, fetch_closes

TRADING_DAYS = 252
DEFAULT_BENCH = "SPY"
CRYPTO_BENCH = "BTCUSDT"


class RiskError(Exception):
    pass


def _log_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a and a > 0 and b and b > 0:
            out.append(math.log(b / a))
    return out


def _percentile(sorted_vals: list[float], q: float) -> float:
    """q in [0, 1], linear interpolation on sorted ascending list."""
    if not sorted_vals:
        return 0.0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    pos = (len(sorted_vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    w = pos - lo
    return sorted_vals[lo] * (1 - w) + sorted_vals[hi] * w


def _round(x: float | None, n: int = 6) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), n)


def _annualize_vol(daily_std: float) -> float:
    return daily_std * math.sqrt(TRADING_DAYS)


def compound_annualize(daily_value: float, periods: int = TRADING_DAYS) -> float:
    """复利年化：(1+日收益)^252 - 1，避免线性 *252 在高 α 股票上失真。"""
    try:
        return (1.0 + float(daily_value)) ** periods - 1.0
    except (OverflowError, ValueError):
        return float("nan")


def _sig_stars(p: float | None) -> str:
    if p is None or (isinstance(p, float) and (math.isnan(p) or math.isinf(p))):
        return ""
    if p < 0.01:
        return "***"
    if p < 0.05:
        return "**"
    if p < 0.10:
        return "*"
    return ""


def historical_vol(returns: list[float], window: int | None = None) -> dict:
    series = returns[-window:] if window and window > 1 else returns
    if len(series) < 2:
        return {"daily": None, "annual": None, "window": len(series)}
    daily = pstdev(series)
    return {
        "daily": _round(daily),
        "annual": _round(_annualize_vol(daily)),
        "window": len(series),
    }


def ewma_vol(returns: list[float], lam: float = 0.94) -> dict:
    """RiskMetrics EWMA：σ²_t = λ σ²_{t-1} + (1-λ) r²_{t-1}."""
    if len(returns) < 2:
        return {"lambda": lam, "daily": None, "annual": None}
    var = returns[0] ** 2
    for r in returns[1:]:
        var = lam * var + (1 - lam) * (r ** 2)
    daily = math.sqrt(max(var, 0.0))
    return {
        "lambda": lam,
        "daily": _round(daily),
        "annual": _round(_annualize_vol(daily)),
    }


def garch11_vol(returns: list[float], max_iter: int = 80) -> dict:
    """
    简易 GARCH(1,1)：σ²_t = ω + α r²_{t-1} + β σ²_{t-1}
    用网格搜索拟合 α,β（ω 由无条件方差约束），捕捉波动聚集。
    """
    if len(returns) < 30:
        return {
            "model": "GARCH(1,1)",
            "daily": None,
            "annual": None,
            "params": None,
            "note": "样本不足，至少需要约 30 个收益点",
        }

    n = len(returns)
    unc = sum(r * r for r in returns) / n
    best = None
    best_ll = -1e18

    for ai in range(1, 16):
        alpha = ai / 100.0  # 0.01 .. 0.15
        for bi in range(70, 96):
            beta = bi / 100.0  # 0.70 .. 0.95
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
                ll += -0.5 * (math.log(2 * math.pi) + math.log(var) + (r * r) / var)
            if not ok:
                continue
            if ll > best_ll:
                best_ll = ll
                # one-step ahead variance
                last_r = returns[-1]
                next_var = omega + alpha * (last_r * last_r) + beta * var
                best = {
                    "omega": omega,
                    "alpha": alpha,
                    "beta": beta,
                    "var": next_var,
                    "ll": ll,
                }

    if not best:
        return {
            "model": "GARCH(1,1)",
            "daily": None,
            "annual": None,
            "params": None,
            "note": "拟合失败",
        }

    daily = math.sqrt(max(best["var"], 0.0))
    return {
        "model": "GARCH(1,1)",
        "daily": _round(daily),
        "annual": _round(_annualize_vol(daily)),
        "params": {
            "omega": _round(best["omega"], 8),
            "alpha": _round(best["alpha"], 4),
            "beta": _round(best["beta"], 4),
            "persistence": _round(best["alpha"] + best["beta"], 4),
        },
        "note": "网格极大似然",
    }


def gjr_garch_vol(returns: list[float]) -> dict:
    """
    GJR-GARCH(1,1)：σ²_t = ω + (α + γ I_{t-1}) r²_{t-1} + β σ²_{t-1}
    I=1 当 r<0，捕捉杠杆效应（利空推高波动）。
    """
    if len(returns) < 40:
        return {
            "model": "GJR-GARCH(1,1)",
            "daily": None,
            "annual": None,
            "params": None,
            "note": "样本不足",
        }
    n = len(returns)
    unc = sum(r * r for r in returns) / n
    best = None
    best_ll = -1e18
    for ai in range(1, 12):
        alpha = ai / 100.0
        for gi in range(0, 16):
            gamma = gi / 100.0
            for bi in range(70, 96):
                beta = bi / 100.0
                if alpha + 0.5 * gamma + beta >= 0.999:
                    continue
                omega = unc * max(1e-8, 1 - alpha - 0.5 * gamma - beta)
                var = unc
                ll = 0.0
                ok = True
                for r in returns:
                    ind = 1.0 if r < 0 else 0.0
                    var = omega + (alpha + gamma * ind) * (r * r) + beta * var
                    if var <= 1e-16:
                        ok = False
                        break
                    ll += -0.5 * (math.log(2 * math.pi) + math.log(var) + (r * r) / var)
                if not ok:
                    continue
                if ll > best_ll:
                    best_ll = ll
                    last_r = returns[-1]
                    ind = 1.0 if last_r < 0 else 0.0
                    next_var = omega + (alpha + gamma * ind) * (last_r * last_r) + beta * var
                    best = {
                        "omega": omega,
                        "alpha": alpha,
                        "gamma": gamma,
                        "beta": beta,
                        "var": next_var,
                    }
    if not best:
        return {
            "model": "GJR-GARCH(1,1)",
            "daily": None,
            "annual": None,
            "params": None,
            "note": "拟合失败",
        }
    daily = math.sqrt(max(best["var"], 0.0))
    return {
        "model": "GJR-GARCH(1,1)",
        "daily": _round(daily),
        "annual": _round(_annualize_vol(daily)),
        "params": {
            "omega": _round(best["omega"], 8),
            "alpha": _round(best["alpha"], 4),
            "gamma": _round(best["gamma"], 4),
            "beta": _round(best["beta"], 4),
        },
        "leverage": "gamma>0 表示利空推高波动" if (best["gamma"] or 0) > 0 else "杠杆效应不明显",
        "note": "非对称 GARCH；γ 为杠杆项",
    }


def egarch_vol(returns: list[float]) -> dict:
    """
    EGARCH(1,1)：log(σ²_t) = ω + β log(σ²_{t-1}) + α(|z|-E|z|) + γ z
    z=r/σ，E|z|=√(2/π)。保证方差为正，并可捕捉杠杆效应（γ<0 常见）。
    """
    if len(returns) < 40:
        return {
            "model": "EGARCH(1,1)",
            "daily": None,
            "annual": None,
            "params": None,
            "note": "样本不足",
        }
    n = len(returns)
    unc = sum(r * r for r in returns) / n
    log_unc = math.log(max(unc, 1e-12))
    eabs = math.sqrt(2 / math.pi)
    best = None
    best_ll = -1e18
    for ai in range(-5, 16):
        alpha = ai / 50.0  # -0.10 .. 0.30
        for gi in range(-20, 6):
            gamma = gi / 50.0  # -0.40 .. 0.10
            for bi in range(70, 98):
                beta = bi / 100.0
                # ω ≈ (1-β) log(unc) under stationarity approx
                omega = (1 - beta) * log_unc
                log_var = log_unc
                ll = 0.0
                ok = True
                for r in returns:
                    if log_var > 5 or log_var < -40:
                        ok = False
                        break
                    var = math.exp(log_var)
                    if var <= 1e-16:
                        ok = False
                        break
                    z = r / math.sqrt(var)
                    log_var = (
                        omega
                        + beta * log_var
                        + alpha * (abs(z) - eabs)
                        + gamma * z
                    )
                    ll += -0.5 * (math.log(2 * math.pi) + math.log(var) + (r * r) / var)
                if not ok or log_var > 5 or log_var < -40:
                    continue
                if ll > best_ll:
                    best_ll = ll
                    next_var = math.exp(min(max(log_var, -40), 5))
                    best = {
                        "omega": omega,
                        "alpha": alpha,
                        "gamma": gamma,
                        "beta": beta,
                        "var": next_var,
                    }
    if not best:
        return {
            "model": "EGARCH(1,1)",
            "daily": None,
            "annual": None,
            "params": None,
            "note": "拟合失败",
        }
    daily = math.sqrt(max(best["var"], 0.0))
    return {
        "model": "EGARCH(1,1)",
        "daily": _round(daily),
        "annual": _round(_annualize_vol(daily)),
        "params": {
            "omega": _round(best["omega"], 6),
            "alpha": _round(best["alpha"], 4),
            "gamma": _round(best["gamma"], 4),
            "beta": _round(best["beta"], 4),
        },
        "leverage": "gamma<0 表示利空抬升波动" if (best["gamma"] or 0) < 0 else "杠杆方向不明显",
        "note": "对数方差形式，天然保证 σ²>0",
    }


def var_cvar_historical(returns: list[float], confidence: float = 0.95) -> dict:
    """历史模拟法：损失 = -r，取分位数。"""
    if not returns:
        return {"method": "historical", "confidence": confidence, "var": None, "cvar": None}
    losses = sorted(-r for r in returns)
    q = confidence
    var = _percentile(losses, q)
    tail = [x for x in losses if x >= var]
    cvar = mean(tail) if tail else var
    return {
        "method": "historical",
        "confidence": confidence,
        "var_daily": _round(var),
        "cvar_daily": _round(cvar),
        "var_pct": _round(var * 100, 4),
        "cvar_pct": _round(cvar * 100, 4),
    }


def var_cvar_parametric(returns: list[float], confidence: float = 0.95) -> dict:
    """参数法：假设正态，VaR = μ + σ * z（损失侧取负收益）。"""
    if len(returns) < 2:
        return {"method": "parametric", "confidence": confidence, "var": None, "cvar": None}
    mu = mean(returns)
    sig = pstdev(returns)
    # approx normal z for common levels
    z_map = {0.90: 1.28155, 0.95: 1.64485, 0.99: 2.32635}
    z = z_map.get(round(confidence, 2), 1.64485)
    # loss VaR ≈ -(μ - z σ) = -μ + z σ
    var = -mu + z * sig
    # ES for normal: μ_loss + σ * φ(z)/(1-α) where μ_loss=-μ
    phi = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    cvar = -mu + sig * phi / (1 - confidence)
    return {
        "method": "parametric",
        "confidence": confidence,
        "mean_daily": _round(mu),
        "std_daily": _round(sig),
        "z": _round(z, 5),
        "var_daily": _round(var),
        "cvar_daily": _round(cvar),
        "var_pct": _round(var * 100, 4),
        "cvar_pct": _round(cvar * 100, 4),
    }


def var_cvar_monte_carlo(
    returns: list[float],
    confidence: float = 0.95,
    paths: int = 5000,
    horizon: int = 1,
    seed: int = 42,
) -> dict:
    """蒙特卡洛：GBM 一步/多步，用历史 μ,σ 模拟未来收益分布。"""
    if len(returns) < 2:
        return {"method": "monte_carlo", "confidence": confidence, "var": None, "cvar": None}
    mu = mean(returns)
    sig = pstdev(returns)
    rng = random.Random(seed)
    losses: list[float] = []
    for _ in range(paths):
        # multi-step log return sum
        total = 0.0
        for _h in range(horizon):
            z = rng.gauss(0.0, 1.0)
            total += mu + sig * z
        losses.append(-total)
    losses.sort()
    var = _percentile(losses, confidence)
    tail = [x for x in losses if x >= var]
    cvar = mean(tail) if tail else var
    return {
        "method": "monte_carlo",
        "confidence": confidence,
        "paths": paths,
        "horizon_days": horizon,
        "var_daily": _round(var),
        "cvar_daily": _round(cvar),
        "var_pct": _round(var * 100, 4),
        "cvar_pct": _round(cvar * 100, 4),
    }


def max_drawdown(closes: list[float]) -> dict:
    if not closes:
        return {"max_drawdown": None, "peak_index": None, "trough_index": None}
    peak = closes[0]
    peak_i = 0
    max_dd = 0.0
    best = (0, 0)
    for i, px in enumerate(closes):
        if px > peak:
            peak = px
            peak_i = i
        dd = (peak - px) / peak if peak else 0.0
        if dd > max_dd:
            max_dd = dd
            best = (peak_i, i)
    return {
        "max_drawdown": _round(max_dd, 6),
        "max_drawdown_pct": _round(max_dd * 100, 4),
        "peak_index": best[0],
        "trough_index": best[1],
    }


def sharpe_sortino(returns: list[float], rf_daily: float = 0.0) -> dict:
    if len(returns) < 2:
        return {"sharpe": None, "sortino": None}
    excess = [r - rf_daily for r in returns]
    mu = mean(excess)
    sig = pstdev(excess)
    downside = [min(r, 0.0) for r in excess]
    down_var = mean([x * x for x in downside])
    down_std = math.sqrt(down_var) if down_var > 0 else 0.0
    sharpe = (mu / sig) * math.sqrt(TRADING_DAYS) if sig > 0 else None
    sortino = (mu / down_std) * math.sqrt(TRADING_DAYS) if down_std > 0 else None
    return {
        "sharpe": _round(sharpe, 4),
        "sortino": _round(sortino, 4),
        "ann_return": _round(mu * TRADING_DAYS, 6),
        "ann_vol": _round(_annualize_vol(sig), 6) if sig else None,
    }


def beta_capm(asset_returns: list[float], bench_returns: list[float]) -> dict:
    """CAPM OLS：alpha 用复利年化；附带 t/p/R²/相关。"""
    n = min(len(asset_returns), len(bench_returns))
    if n < 20:
        return {"beta": None, "alpha_daily": None, "corr": None, "note": "样本不足"}
    a = asset_returns[-n:]
    b = bench_returns[-n:]
    fit = _ols(a, [[x] for x in b])
    if not fit["coef"]:
        return {"beta": None, "alpha_daily": None, "corr": None, "note": "回归失败"}
    alpha = fit["coef"][0]
    beta = fit["coef"][1]
    ma, mb = mean(a), mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
    sig_a = math.sqrt(sum((x - ma) ** 2 for x in a) / n)
    sig_b = math.sqrt(sum((y - mb) ** 2 for y in b) / n)
    corr = cov / (sig_a * sig_b) if sig_a and sig_b else None
    alpha_p = fit["p"][0] if fit["p"] else None
    beta_p = fit["p"][1] if len(fit["p"]) > 1 else None
    return {
        "beta": _round(beta, 4),
        "beta_t": _round(fit["t"][1], 3) if len(fit["t"]) > 1 else None,
        "beta_pvalue": _round(beta_p, 4) if beta_p is not None else None,
        "beta_sig": _sig_stars(beta_p),
        "alpha_daily": _round(alpha, 6),
        "alpha_annual": _round(compound_annualize(alpha), 6),
        "alpha_annual_linear": _round(alpha * TRADING_DAYS, 6),
        "alpha_pvalue": _round(alpha_p, 4) if alpha_p is not None else None,
        "alpha_sig": _sig_stars(alpha_p),
        "corr": _round(corr, 4),
        "r_squared": _round(fit["r2"], 4),
        "benchmark_points": n,
        "note": "α 年化为复利 (1+日α)^252-1",
    }

def _ols(y: list[float], X: list[list[float]]) -> dict:
    """多元 OLS：返回 coef(含截距)、R²、t、p。优先 statsmodels，失败则手写。"""
    n = len(y)
    empty = {"coef": [], "r2": 0.0, "t": [], "p": [], "n": n}
    if n < 10 or not X or len(X) != n:
        return empty
    k = len(X[0])
    try:
        import numpy as np
        import statsmodels.api as sm

        x_arr = np.asarray(X, dtype=float)
        y_arr = np.asarray(y, dtype=float)
        model = sm.OLS(y_arr, sm.add_constant(x_arr, has_constant="add")).fit()
        return {
            "coef": [float(v) for v in model.params],
            "r2": float(model.rsquared),
            "t": [float(v) for v in model.tvalues],
            "p": [float(v) for v in model.pvalues],
            "n": int(model.nobs),
        }
    except Exception:
        pass

    cols = k + 1
    xtx = [[0.0] * cols for _ in range(cols)]
    xty = [0.0] * cols
    for i in range(n):
        row = [1.0] + list(X[i])
        for a in range(cols):
            xty[a] += row[a] * y[i]
            for b in range(cols):
                xtx[a][b] += row[a] * row[b]
    # invert xtx via Gauss-Jordan
    aug = [xtx[r][:] + [1.0 if r == c else 0.0 for c in range(cols)] for r in range(cols)]
    for col in range(cols):
        piv = max(range(col, cols), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-14:
            return empty
        aug[col], aug[piv] = aug[piv], aug[col]
        div = aug[col][col]
        for j in range(2 * cols):
            aug[col][j] /= div
        for r in range(cols):
            if r == col:
                continue
            fac = aug[r][col]
            for j in range(2 * cols):
                aug[r][j] -= fac * aug[col][j]
    xtx_inv = [[aug[r][cols + c] for c in range(cols)] for r in range(cols)]
    coef = [sum(xtx_inv[r][c] * xty[c] for c in range(cols)) for r in range(cols)]
    yhat = []
    for i in range(n):
        pred = coef[0]
        for j in range(k):
            pred += coef[j + 1] * X[i][j]
        yhat.append(pred)
    ym = sum(y) / n
    ss_tot = sum((yi - ym) ** 2 for yi in y)
    ss_res = sum((y[i] - yhat[i]) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-18 else 0.0
    df = max(n - cols, 1)
    sigma2 = ss_res / df
    tvals: list[float] = []
    pvals: list[float] = []
    for j in range(cols):
        se = math.sqrt(max(sigma2 * xtx_inv[j][j], 0.0))
        t = coef[j] / se if se > 1e-18 else 0.0
        # two-sided rough normal approx for p
        z = abs(t)
        # Abramowitz-ish erfc approx → Φ
        p = math.erfc(z / math.sqrt(2.0))
        tvals.append(t)
        pvals.append(p)
    return {"coef": coef, "r2": r2, "t": tvals, "p": pvals, "n": n}


def evt_gpd(returns: list[float], confidence: float = 0.99, threshold_q: float = 0.90) -> dict:
    """
    完整 EVT Peaks-Over-Threshold + GPD(ξ, β) 极大似然。
    损失侧：L = -r；阈值 u = 分位数；超额 x = L - u ~ GPD。
    """
    if len(returns) < 50:
        return {
            "method": "EVT-GPD",
            "confidence": confidence,
            "var": None,
            "cvar": None,
            "note": "样本不足",
        }
    losses = sorted(-r for r in returns)
    n = len(losses)
    # 自适应阈值：尽量保证超额样本 >= 20
    tq = threshold_q
    u = _percentile(losses, tq)
    excesses = [x - u for x in losses if x > u]
    for cand in (0.90, 0.85, 0.80, 0.75):
        if len(excesses) >= 20:
            break
        tq = cand
        u = _percentile(losses, tq)
        excesses = [x - u for x in losses if x > u]
    threshold_q = tq
    nu = len(excesses)
    if nu < 15:
        return {
            "method": "EVT-GPD",
            "confidence": confidence,
            "threshold": _round(u),
            "exceedances": nu,
            "var": None,
            "cvar": None,
            "note": "超阈值样本过少，无法稳健拟合 GPD",
        }

    xi = None
    beta = None
    try:
        from scipy.stats import genpareto

        xi_hat, _loc, beta_hat = genpareto.fit(excesses, floc=0)
        xi, beta = float(xi_hat), float(beta_hat)
    except Exception:
        xi, beta = None, None

    if xi is None or beta is None or beta <= 0 or math.isnan(xi) or math.isnan(beta):
        def nll(xi_v: float, beta_v: float) -> float:
            if beta_v <= 1e-16:
                return 1e18
            s = 0.0
            if abs(xi_v) < 1e-8:
                for x in excesses:
                    s += math.log(beta_v) + x / beta_v
                return s
            for x in excesses:
                z = 1.0 + xi_v * x / beta_v
                if z <= 1e-12:
                    return 1e18
                s += math.log(beta_v) + (1.0 + 1.0 / xi_v) * math.log(z)
            return s

        best = None
        best_nll = 1e18
        mean_ex = mean(excesses)
        for xi_i in range(-45, 46):
            xi_v = xi_i / 100.0
            for mul in (0.4, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0):
                if abs(xi_v) < 1e-8:
                    beta_v = mean_ex * mul
                else:
                    if xi_v >= 0.95:
                        continue
                    beta_v = max(1e-10, mean_ex * (1 - xi_v) * mul)
                score = nll(xi_v, beta_v)
                if score < best_nll:
                    best_nll = score
                    best = {"xi": xi_v, "beta": beta_v}
        if not best:
            return {
                "method": "EVT-GPD",
                "confidence": confidence,
                "threshold": _round(u),
                "exceedances": nu,
                "var": None,
                "cvar": None,
                "note": "GPD 拟合失败",
            }
        xi, beta = best["xi"], best["beta"]

    p = confidence
    # VaR / ES under GPD
    if abs(xi) < 1e-8:
        var = u + beta * math.log(max(1e-12, (nu / n) / max(1e-12, 1 - p)))
        cvar = var + beta
    else:
        # standard POT: Fbar(x) = (nu/n) * (1 + ξ(x-u)/β)^{-1/ξ}
        # VaR_p = u + (β/ξ) * [ ((n/nu)*(1-p))^{-ξ} - 1 ]
        ratio = (n / nu) * (1 - p)
        if ratio <= 0:
            return {
                "method": "EVT-GPD",
                "confidence": confidence,
                "xi": _round(xi, 4),
                "beta": _round(beta, 6),
                "var": None,
                "cvar": None,
                "note": "置信度过高相对样本",
            }
        var = u + (beta / xi) * (ratio ** (-xi) - 1.0)
        if xi >= 1.0:
            cvar = None
        else:
            # ES = (VaR + β - ξ u) / (1 - ξ)
            cvar = (var + beta - xi * u) / (1.0 - xi)

    return {
        "method": "EVT-GPD",
        "confidence": confidence,
        "threshold_q": threshold_q,
        "threshold": _round(u),
        "exceedances": nu,
        "n": n,
        "xi": _round(xi, 4),
        "beta": _round(beta, 6),
        "var_daily": _round(var),
        "cvar_daily": _round(cvar) if cvar is not None else None,
        "var_pct": _round(var * 100, 4),
        "cvar_pct": _round(cvar * 100, 4) if cvar is not None else None,
        "note": "GPD(POT)；ξ>0 厚尾，ξ≈0 近指数，ξ<0 有限上界",
    }


def fama_french_exposure(
    dates: list[str],
    returns: list[float],
    kind: str = "5",
) -> dict:
    """对齐 Ken French 日度因子做 OLS：R - RF ~ factors。"""
    label = "FF5" if kind == "5" else "FF3"
    names3 = ["Mkt-RF", "SMB", "HML"]
    names5 = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]
    names = names5 if kind == "5" else names3
    if len(dates) != len(returns) or len(returns) < 40:
        return {"model": label, "betas": None, "note": "样本不足或日期未对齐"}
    try:
        factors = load_ff_factors(kind)
    except Exception as exc:  # noqa: BLE001
        return {"model": label, "betas": None, "note": f"因子下载失败: {exc}"}

    y: list[float] = []
    X: list[list[float]] = []
    used = 0
    for d, r in zip(dates, returns):
        row = factors.get(d)
        if not row:
            continue
        rf = row.get("RF")
        if rf is None:
            continue
        xs = []
        ok = True
        for nm in names:
            # Ken French CSV headers may vary slightly
            val = row.get(nm)
            if val is None:
                # try alternate keys
                alt = {k: v for k, v in row.items() if k.replace(" ", "") == nm.replace(" ", "")}
                val = next(iter(alt.values()), None) if alt else None
            if val is None:
                ok = False
                break
            xs.append(val)
        if not ok:
            continue
        y.append(r - rf)
        X.append(xs)
        used += 1

    if used < 40:
        return {"model": label, "betas": None, "n": used, "note": "与因子日期重叠不足"}

    fit = _ols(y, X)
    coef = fit.get("coef") or []
    if not coef:
        return {"model": label, "betas": None, "n": used, "note": "回归失败"}

    alpha = coef[0]
    alpha_p = fit["p"][0] if fit.get("p") else None
    betas = {
        "alpha_daily": _round(alpha, 6),
        "alpha_annual": _round(compound_annualize(alpha), 6),
        "alpha_annual_linear": _round(alpha * TRADING_DAYS, 6),
        "alpha_pvalue": _round(alpha_p, 4) if alpha_p is not None else None,
        "alpha_sig": _sig_stars(alpha_p),
    }
    loadings = {}
    for i, nm in enumerate(names):
        key = nm.lower().replace("-", "_")
        b = coef[i + 1]
        t = fit["t"][i + 1] if fit.get("t") and len(fit["t"]) > i + 1 else None
        p = fit["p"][i + 1] if fit.get("p") and len(fit["p"]) > i + 1 else None
        betas[key] = _round(b, 4)
        loadings[nm] = {
            "beta": _round(b, 4),
            "t_stat": _round(t, 3) if t is not None else None,
            "p_value": _round(p, 4) if p is not None else None,
            "sig": _sig_stars(p),
        }

    return {
        "model": label,
        "n": used,
        "r2": _round(fit["r2"], 4),
        "betas": betas,
        "loadings": loadings,
        "note": "超额收益对 Ken French 日度因子回归；α 为复利年化",
    }


def industry_exposure(symbol: str, returns: list[float], window: int) -> dict:
    """用基本面行业 → 对应行业 ETF，再算相对行业 Beta。"""
    code = symbol.upper()
    if code.endswith("USDT") or code.endswith("USDC"):
        return {"sector": None, "etf": None, "beta": None, "note": "加密货币无股票行业分类"}

    sector = None
    try:
        from app.services.fundamentals import analyze as fund_analyze

        info = fund_analyze(code)
        company = (info or {}).get("company") or {}
        sector = company.get("sector") or (info or {}).get("sector") or None
    except Exception:  # noqa: BLE001
        sector = _yahoo_sector(code)

    etf = sector_etf(sector)
    if not etf:
        return {
            "sector": sector,
            "etf": None,
            "beta": None,
            "note": "未识别行业或无对应 ETF 映射",
        }
    try:
        ind = fetch_closes(etf, "1d", apply_live=False)
        i_closes = [float(x) for x in (ind.get("closes") or []) if x is not None]
        if len(i_closes) < 40:
            raise RiskError(f"{etf} 样本不足")
        i_use = i_closes[-(window + 1) :] if len(i_closes) > window + 1 else i_closes
        i_rets = _log_returns(i_use)
        capm = beta_capm(returns, i_rets)
        return {
            "sector": sector,
            "etf": etf,
            "beta": capm.get("beta"),
            "corr": capm.get("corr"),
            "alpha_annual": capm.get("alpha_annual"),
            "note": f"相对行业 ETF {etf} 的 Beta",
        }
    except Exception as exc:  # noqa: BLE001
        return {"sector": sector, "etf": etf, "beta": None, "note": f"行业 ETF 拉数失败: {exc}"}



def _yahoo_sector(symbol: str) -> str | None:
    try:
        import httpx
        from urllib.parse import quote

        url = (
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{quote(symbol, safe='')}"
            f"?modules=assetProfile"
        )
        headers = {"User-Agent": "Mozilla/5.0"}
        with httpx.Client(timeout=12.0, headers=headers, follow_redirects=True) as client:
            res = client.get(url)
            res.raise_for_status()
            data = res.json()
        result = ((data.get("quoteSummary") or {}).get("result") or [None])[0] or {}
        profile = result.get("assetProfile") or {}
        return profile.get("sector")
    except Exception:  # noqa: BLE001
        return None


def liquidity_metrics(bars: list[dict]) -> dict:
    """成交量 + Amihud 非流动性 + Corwin-Schultz 高低价差估计。"""
    vols: list[float] = []
    amihud_vals: list[float] = []
    cs_spreads: list[float] = []

    cleaned: list[dict] = []
    for bar in bars or []:
        try:
            o = float(bar.get("open") or 0)
            h = float(bar.get("high") or 0)
            l = float(bar.get("low") or 0)
            c = float(bar.get("close") or 0)
            v = float(bar.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        cleaned.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
        if v >= 0:
            vols.append(v)

    # Amihud: |r| / dollar volume
    for i in range(1, len(cleaned)):
        prev_c = cleaned[i - 1]["close"]
        c = cleaned[i]["close"]
        v = cleaned[i]["volume"]
        if prev_c <= 0 or c <= 0 or v <= 0:
            continue
        ret = abs(math.log(c / prev_c))
        dollar = c * v
        if dollar > 0:
            amihud_vals.append(ret / dollar)

    # Corwin-Schultz pairwise
    const = 3 - 2 * math.sqrt(2)
    for i in range(len(cleaned) - 1):
        h0, l0 = cleaned[i]["high"], cleaned[i]["low"]
        h1, l1 = cleaned[i + 1]["high"], cleaned[i + 1]["low"]
        if min(h0, l0, h1, l1) <= 0:
            continue
        beta = (math.log(h0 / l0) ** 2) + (math.log(h1 / l1) ** 2)
        h2 = max(h0, h1)
        l2 = min(l0, l1)
        if l2 <= 0:
            continue
        gamma = math.log(h2 / l2) ** 2
        if beta <= 0 or const <= 0:
            continue
        try:
            alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / const - math.sqrt(max(gamma, 0) / const)
        except ValueError:
            continue
        # spread can be noisy / negative → clip
        try:
            ea = math.exp(alpha)
            s = 2 * (ea - 1) / (1 + ea)
        except OverflowError:
            continue
        if 0 <= s <= 0.5:
            cs_spreads.append(s)

    out: dict = {
        "avg_volume": _round(mean(vols), 2) if len(vols) >= 5 else None,
        "recent_5d_avg_volume": _round(mean(vols[-5:]), 2) if len(vols) >= 5 else None,
        "volume_cv": _round(pstdev(vols) / mean(vols), 4) if len(vols) >= 5 and mean(vols) else None,
    }
    if amihud_vals:
        # ×1e9 便于大盘股阅读（仍是相对尺度）
        a = mean(amihud_vals) * 1e9
        out["amihud"] = _round(a, 6)
        out["amihud_note"] = "均值 |r|/(P·V)×1e9，越大越不流动"
    else:
        out["amihud"] = None
        out["amihud_note"] = "缺少成交量，Amihud 不可用"
    if cs_spreads:
        out["corwin_schultz"] = _round(mean(cs_spreads), 6)
        out["corwin_schultz_pct"] = _round(mean(cs_spreads) * 100, 4)
        out["corwin_schultz_note"] = "高低价隐含买卖价差（Corwin-Schultz）"
    else:
        out["corwin_schultz"] = None
        out["corwin_schultz_pct"] = None
        out["corwin_schultz_note"] = "价差估计样本不足"
    out["note"] = "Amihud + Corwin-Schultz；非真实 L1 报价价差"
    return out


def _bar_dates(bars: list[dict], n_returns: int) -> list[str]:
    """取与 returns 对齐的日期（每根 bar 对应收盘，收益用第 2 根起）。"""
    if not bars or n_returns <= 0:
        return []
    use = bars[-(n_returns + 1) :] if len(bars) > n_returns + 1 else bars
    dates: list[str] = []
    for bar in use[1:]:
        ts = bar.get("ts")
        if ts is None:
            dates.append("")
            continue
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        dates.append(dt.strftime("%Y-%m-%d"))
    if len(dates) > n_returns:
        dates = dates[-n_returns:]
    elif len(dates) < n_returns:
        dates = [""] * (n_returns - len(dates)) + dates
    return dates


def _pick_benchmark(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("USDT") or s.endswith("USDC"):
        return CRYPTO_BENCH if s not in {CRYPTO_BENCH, "BTCUSDT"} else "ETHUSDT"
    return DEFAULT_BENCH


def analyze_risk(
    symbol: str,
    window: int = 252,
    confidence: float = 0.95,
    benchmark: str | None = None,
) -> dict:
    code = (symbol or "").strip().upper()
    if not code:
        raise RiskError("请输入股票代码")
    if confidence not in (0.90, 0.95, 0.99):
        confidence = 0.95
    window = max(30, min(int(window or 252), 1000))

    try:
        ohlc = fetch_closes(code, "1d", apply_live=True)
    except OhlcError as exc:
        raise RiskError(str(exc)) from exc

    closes = [float(x) for x in (ohlc.get("closes") or []) if x is not None]
    if len(closes) < 40:
        raise RiskError(f"{code} 日线样本不足（{len(closes)}），无法测算风险")

    use_closes = closes[-(window + 1) :] if len(closes) > window + 1 else closes
    returns = _log_returns(use_closes)
    if len(returns) < 30:
        raise RiskError("有效收益率样本不足")

    bars = ohlc.get("ohlc_bars") or []
    use_bars = bars[-(window + 1) :] if len(bars) > window + 1 else bars
    dates = _bar_dates(use_bars, len(returns))

    bench_sym = (benchmark or _pick_benchmark(code)).upper()
    beta = {"beta": None, "benchmark": bench_sym, "note": "基准拉数失败"}
    try:
        if bench_sym != code:
            bench = fetch_closes(bench_sym, "1d", apply_live=False)
            b_closes = [float(x) for x in (bench.get("closes") or []) if x is not None]
            b_use = b_closes[-(window + 1) :] if len(b_closes) > window + 1 else b_closes
            b_rets = _log_returns(b_use)
            beta = beta_capm(returns, b_rets)
            beta["benchmark"] = bench_sym
    except Exception as exc:  # noqa: BLE001
        beta = {"beta": None, "benchmark": bench_sym, "note": f"基准失败: {exc}"}

    hist = historical_vol(returns)
    ewma = ewma_vol(returns)
    garch = garch11_vol(returns)
    gjr = gjr_garch_vol(returns)
    egarch = egarch_vol(returns)
    var_h = var_cvar_historical(returns, confidence)
    var_p = var_cvar_parametric(returns, confidence)
    var_m = var_cvar_monte_carlo(returns, confidence)
    evt = evt_gpd(returns, confidence=min(0.99, max(confidence, 0.95)))
    dd = max_drawdown(use_closes)
    ratios = sharpe_sortino(returns)
    liq = liquidity_metrics(use_bars)

    ff3 = fama_french_exposure(dates, returns, kind="3")
    ff5 = fama_french_exposure(dates, returns, kind="5")
    industry = industry_exposure(code, returns, window)

    price = ohlc.get("price") or use_closes[-1]

    def _dollar(pct_ret: float | None) -> float | None:
        if pct_ret is None or price is None:
            return None
        return _round(float(price) * float(pct_ret), 4)

    return {
        "symbol": code,
        "price": _round(float(price), 4) if price is not None else None,
        "source": ohlc.get("source"),
        "bars": len(use_closes),
        "returns": len(returns),
        "window": window,
        "confidence": confidence,
        "volatility": {
            "historical": hist,
            "ewma": ewma,
            "garch": garch,
            "gjr_garch": gjr,
            "egarch": egarch,
        },
        "var": {
            "historical": var_h,
            "parametric": var_p,
            "monte_carlo": var_m,
        },
        "cvar": {
            "historical": {
                "cvar_daily": var_h.get("cvar_daily"),
                "cvar_pct": var_h.get("cvar_pct"),
                "cvar_dollar": _dollar(var_h.get("cvar_daily")),
            },
            "parametric": {
                "cvar_daily": var_p.get("cvar_daily"),
                "cvar_pct": var_p.get("cvar_pct"),
                "cvar_dollar": _dollar(var_p.get("cvar_daily")),
            },
            "monte_carlo": {
                "cvar_daily": var_m.get("cvar_daily"),
                "cvar_pct": var_m.get("cvar_pct"),
                "cvar_dollar": _dollar(var_m.get("cvar_daily")),
            },
            "note": "CVaR=超过 VaR 后的平均损失，监管更推荐",
        },
        "var_dollar": {
            "historical": _dollar(var_h.get("var_daily")),
            "parametric": _dollar(var_p.get("var_daily")),
            "monte_carlo": _dollar(var_m.get("var_daily")),
        },
        "beta": beta,
        "factors": {
            "ff3": ff3,
            "ff5": ff5,
        },
        "industry": industry,
        "evt": evt,
        "drawdown": dd,
        "ratios": ratios,
        "liquidity": liq,
    }

