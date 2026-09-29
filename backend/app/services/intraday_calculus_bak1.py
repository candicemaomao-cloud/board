"""单票日内回测：导数拐点、积分谷底、偏导对冲、梯度调仓。"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from datetime import date, timedelta

from app.services.ohlc import OhlcError, fetch_closes, fetch_closes_covering
from app.services.param_matrix import (
    BUDGET as DEFAULT_BUDGET,
    precompute_session_highs,
    precompute_sigma,
    rth_days,
    slip,
)
from app.services.user_research import wash_bars
from app.user_stocks.tsla_golden import (
    MIN_HOLD_BARS,
    K_STOP,
    K_TP,
    K_FADE,
    MIN_TP_ABS,
    LOOKBACK_5,
    LOOKBACK_30,
    TREND_THRESHOLD,
    atr_5,
    atr_wilder_series,
    confirm_break,
    confirm_break_down,
    et_day,
    et_time,
    gradient,
    is_rth,
    log_returns,
    mu_index,
    opening_range_series,
    remaining_5m,
    signal_at,
    stdev,
)

INT_BARS = 6  # 半小时 = 6 根 5min
INT_MIN = 0.004  # 历史样本不够时的兜底
BETA_WIN = 78
TARGET_DAY_VOL = 0.015
HEDGE_DEFAULT = "SPY"
GARCH_MIN = 80
MIN_REV_BARS = 6  # 反向加速至少拿 30 分钟，避免刚进就因下一根 f″<0 被砍
K_REV = 0.35  # 浮亏至少 0.35×ATR 才认反向加速
VOL_Z_BARS = 6  # 成交量 Z 相对过去 30 分钟（6 根 5min）
VOL_Z_DEFAULT = 1.0  # 默认 1σ，不是 3 倍天量
KELLY_MIN_TRADES = 8
KELLY_LOOKBACK = 30
KELLY_HALF = 0.5
KELLY_LO, KELLY_HI = 0.35, 1.4


def _fit_garch11(returns: list[float]) -> tuple[float, float, float, float] | None:
    """σ²_t = ω + α r²_{t-1} + β σ²_{t-1}。网格拟合，返回 omega, alpha, beta, last_var。"""
    n = len(returns)
    if n < GARCH_MIN:
        return None
    unc = sum(r * r for r in returns) / n
    best = None
    best_ll = -1e18
    for ai in range(2, 15, 2):
        alpha = ai / 100.0
        for bi in range(75, 96, 3):
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
            if not ok or ll <= best_ll:
                continue
            best_ll = ll
            next_var = omega + alpha * (returns[-1] ** 2) + beta * var
            best = (omega, alpha, beta, next_var)
    return best


def garch_scale_series(bars: list[dict]) -> list[float]:
    """每个 5min 的 GARCH σ / 无条件 σ。>1 说明波动在聚集，止盈该放宽。不含隔夜跳空。"""
    scales = [1.0] * len(bars)
    pairs: list[tuple[int, float, object]] = []
    prev_c = prev_d = None
    for i, bar in enumerate(bars):
        if not is_rth(bar["ts"]):
            prev_c = prev_d = None
            continue
        day = et_day(bar["ts"])
        if prev_c is not None and prev_d == day:
            pairs.append((i, math.log(max(bar["close"], 1e-12) / max(prev_c, 1e-12)), day))
        prev_c = bar["close"]
        prev_d = day
    hist: list[float] = []
    fit_day = None
    omega = alpha = beta = var = unc = None
    for i, r, day in pairs:
        if fit_day != day:
            fitted = _fit_garch11(hist)
            if fitted:
                omega, alpha, beta, var = fitted
                unc = sum(x * x for x in hist) / len(hist)
            fit_day = day
        if omega is not None and unc:
            ratio = math.sqrt(max(var, 1e-16)) / math.sqrt(max(unc, 1e-16))
            scales[i] = min(2.0, max(0.75, ratio))
            var = omega + alpha * (r * r) + beta * var
        hist.append(r)
    return scales


def trap_area(ys: list[float]) -> float:
    if len(ys) < 2:
        return 0.0
    return 0.5 * sum(ys[i] + ys[i + 1] for i in range(len(ys) - 1))


def percentile(xs: list[float], q: float) -> float | None:
    ys = sorted(xs)
    if not ys:
        return None
    if len(ys) == 1:
        return ys[0]
    pos = (len(ys) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ys) - 1)
    w = pos - lo
    return ys[lo] * (1.0 - w) + ys[hi] * w


def session_vwaps(bars: list[dict]) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    num = den = 0.0
    prev = None
    for i, bar in enumerate(bars):
        if not is_rth(bar["ts"]):
            out[i] = (num / den) if den else None
            continue
        day = et_day(bar["ts"])
        if day != prev:
            num = den = 0.0
            prev = day
        typical = (bar["high"] + bar["low"] + bar["close"]) / 3.0
        vol = max(float(bar.get("volume") or 0.0), 1.0)
        num += typical * vol
        den += vol
        out[i] = num / den if den else None
    return out


def volume_z_series(bars: list[dict], lookback: int = VOL_Z_BARS) -> list[float | None]:
    """当前 5min 成交量相对过去 lookback 根 RTH 的 Z-Score。只用已经走完的柱，不含本根。"""
    out: list[float | None] = [None] * len(bars)
    prev_day = None
    day_vols: list[float] = []
    carry: list[float] = []
    for i, bar in enumerate(bars):
        if not is_rth(bar["ts"]):
            continue
        day = et_day(bar["ts"])
        if day != prev_day:
            if day_vols:
                carry = day_vols[-lookback:]
            day_vols = []
            prev_day = day
        cur = max(float(bar.get("volume") or 0.0), 0.0)
        hist = day_vols if len(day_vols) >= 3 else (carry + day_vols)
        hist = hist[-lookback:]
        if len(hist) >= 3:
            mean = sum(hist) / len(hist)
            var = sum((x - mean) ** 2 for x in hist) / (len(hist) - 1)
            sd = math.sqrt(var) if var > 0 else 0.0
            out[i] = (cur - mean) / sd if sd > 1e-9 else 0.0
        day_vols.append(cur)
    return out


def vol_ok_at(vol_zs: list[float | None], i: int, enabled: bool, threshold: float, width: int = 3) -> bool:
    """拐点当根经常缩量。看近 width 根里最高的量 Z，捕捉之前的砸盘/拉升，而不是卡死在当根。"""
    if not enabled:
        return True
    peak = peak_vol_z(vol_zs, i, width)
    if peak is None:
        return True
    return peak >= threshold


def peak_vol_z(vol_zs: list[float | None], i: int, width: int = 3) -> float | None:
    xs = []
    for j in range(max(0, i - width + 1), i + 1):
        z = vol_zs[j] if j < len(vol_zs) else None
        if z is not None:
            xs.append(z)
    return max(xs) if xs else None


def kelly_from_trades(trades: list[dict]) -> tuple[float, float | None, float | None, int, float | None]:
    """用已经平掉的单 walk-forward 半凯利。本金栏已是日额度，再夹到梯度同一区间。样本不够时保持 1。"""
    xs = [t for t in trades if t.get("pnl") is not None][-KELLY_LOOKBACK:]
    n = len(xs)
    if n < KELLY_MIN_TRADES:
        return 1.0, None, None, n, None
    wins = [t["pnl"] for t in xs if t["pnl"] > 0]
    losses = [abs(t["pnl"]) for t in xs if t["pnl"] < 0]
    p = len(wins) / n
    avg_w = (sum(wins) / len(wins)) if wins else 0.0
    avg_l = (sum(losses) / len(losses)) if losses else 0.0
    if avg_l < 1e-9:
        b = 3.0 if avg_w > 0 else 0.0
    else:
        b = avg_w / avg_l
    if b < 1e-9:
        f_star = 0.0
    else:
        f_star = (p * b - (1.0 - p)) / b
    half = f_star * KELLY_HALF
    if half <= 0:
        scale = KELLY_LO
    else:
        scale = min(KELLY_HI, max(KELLY_LO, half))
    return scale, p, b, n, f_star


def sized_budget(budget: float, weight: float, trades: list[dict], use_kelly: bool) -> tuple[float, float, float | None, float | None, int, float | None]:
    if not use_kelly:
        return budget * weight, 1.0, None, None, 0, None
    scale, p, b, n, f_star = kelly_from_trades(trades)
    return budget * weight * scale, scale, p, b, n, f_star


def panic_series(bars: list[dict], vwaps: list[float | None]) -> list[float]:
    """相对当日 VWAP 的下陷面积（梯形）。冲高后仍在均线上方时面积≈0，就是半山腰。"""
    out = [0.0] * len(bars)
    for i in range(len(bars)):
        if i < INT_BARS - 1 or not vwaps[i]:
            continue
        dips = []
        for j in range(i - INT_BARS + 1, i + 1):
            center = vwaps[j] or vwaps[i]
            dips.append(max(center - bars[j]["close"], 0.0) / max(center, 1e-12))
        out[i] = trap_area(dips)
    return out


def euphoria_series(bars: list[dict], vwaps: list[float | None]) -> list[float]:
    """相对当日 VWAP 的上伸面积。还在均线下方的反弹面积≈0，空头就是半山腰。"""
    out = [0.0] * len(bars)
    for i in range(len(bars)):
        if i < INT_BARS - 1 or not vwaps[i]:
            continue
        lifts = []
        for j in range(i - INT_BARS + 1, i + 1):
            center = vwaps[j] or vwaps[i]
            lifts.append(max(bars[j]["close"] - center, 0.0) / max(center, 1e-12))
        out[i] = trap_area(lifts)
    return out


def is_local_min(bars: list[dict], i: int) -> bool:
    if i < LOOKBACK_5 - 1:
        return False
    px = [bars[j]["close"] for j in range(i - LOOKBACK_5 + 1, i + 1)]
    speed = gradient(px)
    accel = gradient(speed)
    if len(speed) < 2:
        return False
    return speed[-2] < 0 and speed[-1] >= 0 and accel[-1] > 0


def is_local_max(bars: list[dict], i: int) -> bool:
    if i < LOOKBACK_5 - 1:
        return False
    px = [bars[j]["close"] for j in range(i - LOOKBACK_5 + 1, i + 1)]
    speed = gradient(px)
    accel = gradient(speed)
    if len(speed) < 2:
        return False
    return speed[-2] > 0 and speed[-1] <= 0 and accel[-1] < 0


def size_frac(panic: float, p70: float | None, p90: float | None, z: float) -> float:
    if p70 is None or p90 is None:
        return 1.0 if panic >= INT_MIN else 0.0
    if panic < p70:
        return 0.0
    if panic < p90:
        return 0.2
    return 1.0 if z >= 2.5 else 0.5


def oversold_shadow(closes: list[float]) -> float:
    """相对窗口起点，价格跌下去的阴影面积（梯形法则）。单位：根数 × 百分比。"""
    if len(closes) < 2:
        return 0.0
    s0 = max(closes[0], 1e-12)
    dips = [max(s0 - px, 0.0) / s0 for px in closes]
    return trap_area(dips)


def last_n_days(days: list, n: int) -> list:
    if n <= 0:
        return days
    return days[-n:] if len(days) > n else days


def spy_map(bars: list[dict]) -> dict[int, dict]:
    return {int(bar["ts"]): bar for bar in bars}


def spy_at(index: dict[int, dict], ts: int, stamps: list[int]) -> dict | None:
    j = bisect.bisect_right(stamps, int(ts)) - 1
    if j < 0:
        return None
    return index.get(stamps[j])


def beta_series(stock: list[dict], spy_index: dict[int, dict], stamps: list[int]) -> list[float | None]:
    out: list[float | None] = [None] * len(stock)
    rs: list[float] = []
    rb: list[float] = []
    prev_s = prev_b = None
    for i, bar in enumerate(stock):
        spy = spy_at(spy_index, bar["ts"], stamps)
        if prev_s and prev_b and spy:
            rs.append(math.log(max(bar["close"], 1e-12) / prev_s))
            rb.append(math.log(max(spy["close"], 1e-12) / prev_b))
            if len(rs) > BETA_WIN:
                rs.pop(0)
                rb.pop(0)
            if len(rs) >= 20:
                mb = sum(rb) / len(rb)
                varb = sum((x - mb) ** 2 for x in rb)
                if varb > 1e-18:
                    ms = sum(rs) / len(rs)
                    cov = sum((a - ms) * (b - mb) for a, b in zip(rs, rb))
                    out[i] = cov / varb
        prev_s = bar["close"]
        if spy:
            prev_b = spy["close"]
    return out


def shadow_series(bars: list[dict]) -> list[float]:
    out = [0.0] * len(bars)
    for i in range(len(bars)):
        if i < INT_BARS - 1:
            continue
        px = [bars[j]["close"] for j in range(i - INT_BARS + 1, i + 1)]
        out[i] = oversold_shadow(px)
    return out


def day_vol(bars: list[dict], idxs: list[int]) -> float:
    px = [bars[i]["close"] for i in idxs]
    rets = log_returns(px)
    return stdev(rets) * math.sqrt(max(len(rets), 1)) if rets else 0.0


def load_bars(symbol: str) -> tuple[list[dict], list[dict], str]:
    start = date.today() - timedelta(days=70)
    raw5 = fetch_closes_covering(symbol, "5m", start=start)
    raw30 = fetch_closes_covering(symbol, "30m", start=start)
    return wash_bars(raw5), wash_bars(raw30), raw5.get("source") or ""


def now_snapshot(bars_5, bars_30, i, params, panics, euphorias, vwaps, p70, p90, p70s, p90s, betas, spy_index, stamps, vol_zs=None, kelly_info=None) -> dict:
    snap = signal_at(bars_5, bars_30, i, params) or {}
    bar = bars_5[i]
    spy = spy_at(spy_index, bar["ts"], stamps) if spy_index else None
    panic = panics[i] if i < len(panics) else 0.0
    eph = euphorias[i] if i < len(euphorias) else 0.0
    vwap = vwaps[i] if i < len(vwaps) else None
    atr = atr_5(bars_5, i)
    z = ((vwap - bar["close"]) / atr) if vwap and atr else 0.0
    z_short = ((bar["close"] - vwap) / atr) if vwap and atr else 0.0
    frac = size_frac(panic, p70, p90, z)
    frac_short = size_frac(eph, p70s, p90s, z_short)
    halfway = bool(snap.get("local_min") and frac <= 0)
    halfway_short = bool(snap.get("local_max") and frac_short <= 0)
    confirm = confirm_break(bars_5, i)
    confirm_down = confirm_break_down(bars_5, i)
    prev_min = i > 0 and is_local_min(bars_5, i - 1)
    prev_max = i > 0 and is_local_max(bars_5, i - 1)
    gate_i = i - 1 if prev_min else i
    gate_vwap = vwaps[gate_i] if gate_i < len(vwaps) else vwap
    gate_panic = panics[gate_i] if gate_i < len(panics) else panic
    gate_atr = atr_5(bars_5, gate_i)
    gate_z = ((gate_vwap - bars_5[gate_i]["close"]) / gate_atr) if gate_vwap and gate_atr else 0.0
    gate_frac = size_frac(gate_panic, p70, p90, gate_z)
    gate_s = i - 1 if prev_max else i
    gate_s_vwap = vwaps[gate_s] if gate_s < len(vwaps) else vwap
    gate_eph = euphorias[gate_s] if gate_s < len(euphorias) else eph
    gate_s_atr = atr_5(bars_5, gate_s)
    gate_s_z = ((bars_5[gate_s]["close"] - gate_s_vwap) / gate_s_atr) if gate_s_vwap and gate_s_atr else 0.0
    gate_frac_short = size_frac(gate_eph, p70s, p90s, gate_s_z)
    f1 = snap.get("f1")
    f2 = snap.get("f2")
    mu = snap.get("mu")
    vol_z = vol_zs[i] if vol_zs and i < len(vol_zs) else None
    vol_z_peak = peak_vol_z(vol_zs or [], i) if vol_zs else None
    vol_th = float(params.get("vol_z_th") or VOL_Z_DEFAULT)
    vz_ok = vol_ok_at(vol_zs or [], i, bool(params.get("vol_confirm")), vol_th)
    fire = bool(prev_min and snap.get("day_ok") and gate_frac > 0 and confirm and vz_ok)
    fire_short = bool(
        params.get("allow_short")
        and prev_max
        and snap.get("day_ok_short")
        and gate_frac_short > 0
        and confirm_down
        and vz_ok
    )
    fire_trend = bool(snap.get("trend_long") and confirm and snap.get("time_ok") and vz_ok)
    fire_trend_short = bool(snap.get("trend_short") and confirm_down and snap.get("time_ok") and vz_ok)
    k_info = kelly_info or {}
    return {
        "time": et_time(bar["ts"]),
        "price": round(bar["close"], 4),
        "f1": round(f1, 4) if f1 is not None else None,
        "f2": round(f2, 4) if f2 is not None else None,
        "mu": round(mu, 5) if mu is not None else None,
        "local_min": bool(snap.get("local_min")),
        "local_max": bool(snap.get("local_max")),
        "shadow": round(panic, 5),
        "panic": round(panic, 5),
        "euphoria": round(eph, 5),
        "p70": round(p70, 5) if p70 is not None else None,
        "p90": round(p90, 5) if p90 is not None else None,
        "p70s": round(p70s, 5) if p70s is not None else None,
        "p90s": round(p90s, 5) if p90s is not None else None,
        "z": round(z, 2),
        "z_short": round(z_short, 2),
        "vwap": round(vwap, 4) if vwap else None,
        "frac": frac,
        "frac_short": frac_short,
        "halfway": halfway,
        "halfway_short": halfway_short,
        "confirm": confirm,
        "confirm_down": confirm_down,
        "integral_ok": frac > 0,
        "integral_ok_short": frac_short > 0,
        "beta": round(betas[i], 3) if i < len(betas) and betas[i] is not None else None,
        "spy": round(spy["close"], 4) if spy else None,
        "expect": round(snap["expect"], 4) if snap.get("expect") else None,
        "up": round(snap["up"], 4) if snap.get("up") else None,
        "down": round(snap["down"], 4) if snap.get("down") else None,
        "stop": round(snap["stop"], 4) if snap.get("stop") else None,
        "stop_short": round(snap["stop_short"], 4) if snap.get("stop_short") else None,
        "atr": round(snap["atr"], 4) if snap.get("atr") else None,
        "k_stop": snap.get("k_stop"),
        "garch_scale": round(snap["garch_scale"], 3) if snap.get("garch_scale") else None,
        "vol_tight": bool(snap.get("vol_tight", True)),
        "fade": bool(snap.get("fade")),
        "fade_short": bool(snap.get("fade_short")),
        "fire": fire,
        "fire_short": fire_short,
        "fire_trend": fire_trend,
        "fire_trend_short": fire_trend_short,
        "trend_long": bool(snap.get("trend_long")),
        "trend_short": bool(snap.get("trend_short")),
        "time_ok": bool(snap.get("time_ok", True)),
        "or_hi": snap.get("or_hi"),
        "or_lo": snap.get("or_lo"),
        "above_open": bool(snap.get("above_open", True)),
        "last_up": bool(snap.get("last_up", True)),
        "last_down": bool(snap.get("last_down", True)),
        "day_ok": bool(snap.get("day_ok", True)),
        "day_ok_short": bool(snap.get("day_ok_short", True)),
        "day_open": round(snap["day_open"], 4) if snap.get("day_open") else None,
        "last_30": round(snap["last_30"], 5) if snap.get("last_30") is not None else None,
        "n": remaining_5m(bar["ts"]),
        "volume": round(float(bar.get("volume") or 0.0), 0),
        "vol_z": round(vol_z, 2) if vol_z is not None else None,
        "vol_z_peak": round(vol_z_peak, 2) if vol_z_peak is not None else None,
        "vol_z_th": vol_th,
        "vol_ok": vz_ok,
        "kelly_scale": k_info.get("scale"),
        "kelly_p": k_info.get("p"),
        "kelly_b": k_info.get("b"),
        "kelly_n": k_info.get("n"),
        "kelly_f": k_info.get("f_star"),
    }


def _stage_name(frac: float, kind: str = "dip") -> str:
    if kind == "trend":
        return "顺势100%"
    if frac <= 0.25:
        return "试探20%"
    if frac <= 0.6:
        return "加仓50%"
    return "主力100%"


def _attach(
    open_pos,
    i,
    bar,
    snap,
    add_frac,
    day_budget,
    weight,
    betas,
    hedge,
    spy_index,
    spy_stamps,
    side: str = "long",
    kind: str = "dip",
):
    notional = day_budget * add_frac
    short = side == "short"
    entry = slip(bar["close"], "sell" if short else "buy")
    shares = notional / entry
    beta = betas[i] if i < len(betas) else None
    spy_buy = hedge_shares = None
    signed = -1.0 if short else 1.0
    if hedge and beta is not None:
        spy_bar = spy_at(spy_index, bar["ts"], spy_stamps)
        if spy_bar:
            raw_spy = spy_bar["close"]
            raw_hs = -signed * beta * (shares * entry) / raw_spy
            spy_buy = slip(raw_spy, "buy" if raw_hs > 0 else "sell")
            hedge_shares = -signed * beta * (shares * entry) / spy_buy
    stop = snap["stop_short"] if short else snap["stop"]
    if open_pos is None:
        return {
            "buy_i": i,
            "buy_time": et_time(bar["ts"]),
            "buy": entry,
            "shares": shares,
            "side": side,
            "stop": stop,
            "expect": snap["expect"],
            "up": snap["up"],
            "down": snap.get("down"),
            "atr": snap.get("atr") or 0.0,
            "k_tp": snap.get("k_tp") or K_TP,
            "k_fade": snap.get("k_fade") or K_FADE,
            "min_tp_abs": snap.get("min_tp_abs") or MIN_TP_ABS,
            "weight": round(weight, 3),
            "beta": round(beta, 3) if beta is not None else None,
            "shadow": round(snap.get("panic") or snap.get("euphoria") or 0.0, 5),
            "spy_buy": spy_buy,
            "hedge_shares": hedge_shares,
            "frac": round(add_frac, 3),
            "stage": _stage_name(add_frac, kind),
            "kind": kind,
            "kelly": round(float(snap.get("kelly") or 1.0), 3),
            "vol_z": round(snap["vol_z_peak"] if snap.get("vol_z_peak") is not None else snap.get("vol_z"), 2)
            if snap.get("vol_z_peak") is not None or snap.get("vol_z") is not None
            else None,
        }
    tot = open_pos["shares"] + shares
    open_pos["buy"] = (open_pos["buy"] * open_pos["shares"] + entry * shares) / tot
    open_pos["shares"] = tot
    open_pos["frac"] = round(min(1.0, open_pos["frac"] + add_frac), 3)
    open_pos["stage"] = _stage_name(open_pos["frac"], open_pos.get("kind", "dip"))
    if short:
        open_pos["stop"] = min(open_pos["stop"], stop)
    else:
        open_pos["stop"] = min(open_pos["stop"], stop)
    if hedge_shares and spy_buy:
        old_hs = open_pos.get("hedge_shares") or 0.0
        old_spy = open_pos.get("spy_buy") or spy_buy
        new_hs = old_hs + hedge_shares
        if abs(new_hs) > 1e-12:
            open_pos["spy_buy"] = (old_hs * old_spy + hedge_shares * spy_buy) / new_hs
        open_pos["hedge_shares"] = new_hs
    return open_pos


def _in_profit(pos, px: float) -> bool:
    if pos.get("side") == "short":
        return px < pos["buy"]
    return px > pos["buy"]


def _profit_per_share(pos, px: float) -> float:
    if pos.get("side") == "short":
        return pos["buy"] - px
    return px - pos["buy"]


def _fade_ok(pos, px: float) -> bool:
    """涨/跌速变慢：先赚满 2.5×ATR 或 $15/股，不吃几块钱的毛刺。"""
    if not _in_profit(pos, px):
        return False
    atr = pos.get("atr") or 0.0
    k_fade = pos.get("k_fade") or K_FADE
    min_abs = pos.get("min_tp_abs") or MIN_TP_ABS
    need = max(k_fade * atr, min_abs) if atr > 0 else min_abs
    return _profit_per_share(pos, px) >= need


def _adverse(pos, px: float) -> float:
    if pos.get("side") == "short":
        return px - pos["buy"]
    return pos["buy"] - px


def _reverse_cut(pos, i: int, px: float, f1: float, f2: float) -> bool:
    """浮亏磨到收盘的 8/6 那种：拿够 30 分钟、亏过 0.35×ATR、且还在反向加速。"""
    if i - pos["buy_i"] < MIN_REV_BARS:
        return False
    if _in_profit(pos, px):
        return False
    atr = pos.get("atr") or 0.0
    if atr <= 0 or _adverse(pos, px) < K_REV * atr:
        return False
    if pos.get("side") == "short":
        return f1 > 0 and f2 > 0
    return f1 < 0 and f2 < 0


def _flatten(open_pos, bar, fill, reason, day, hedge, spy_index, spy_stamps) -> dict:
    short = open_pos.get("side") == "short"
    exit_px = slip(fill, "buy" if short else "sell")
    if short:
        stock_pnl = (open_pos["buy"] - exit_px) * open_pos["shares"]
        pnl_pct = (open_pos["buy"] / exit_px - 1) * 100 if exit_px else 0.0
    else:
        stock_pnl = (exit_px - open_pos["buy"]) * open_pos["shares"]
        pnl_pct = (exit_px / open_pos["buy"] - 1) * 100 if open_pos["buy"] else 0.0
    hedge_pnl = 0.0
    spy_sell_px = None
    if hedge and open_pos.get("hedge_shares"):
        spy_bar = spy_at(spy_index, bar["ts"], spy_stamps)
        if spy_bar:
            hs = open_pos["hedge_shares"]
            spy_sell_px = slip(spy_bar["close"], "sell" if hs > 0 else "buy")
            hedge_pnl = hs * (spy_sell_px - open_pos["spy_buy"])
    net = stock_pnl + hedge_pnl
    return {
        "day": str(day),
        "side": "short" if short else "long",
        "buy_time": open_pos["buy_time"],
        "sell_time": et_time(bar["ts"]),
        "buy": round(open_pos["buy"], 4),
        "sell": round(exit_px, 4),
        "shares": round(open_pos["shares"], 4),
        "weight": open_pos["weight"],
        "frac": open_pos.get("frac", 1.0),
        "stage": open_pos.get("stage", "主力100%"),
        "beta": open_pos.get("beta"),
        "shadow": open_pos.get("shadow"),
        "stock_pnl": round(stock_pnl, 2),
        "hedge_pnl": round(hedge_pnl, 2),
        "pnl": round(net, 2),
        "pnl_pct": round(pnl_pct, 3),
        "exit": reason,
        "spy_buy": open_pos.get("spy_buy"),
        "spy_sell": round(spy_sell_px, 4) if spy_sell_px else None,
        "kelly": open_pos.get("kelly"),
        "vol_z": open_pos.get("vol_z"),
    }


def run_intraday_bt(
    symbol: str,
    days: int = 10,
    budget: float = DEFAULT_BUDGET,
    hedge: bool = True,
    integral: bool = True,
    gradient: bool = True,
    day_filter: bool = True,
    vol_adapt: bool = True,
    allow_short: bool = False,
    allow_trend: bool = False,
    vol_confirm: bool = False,
    vol_z_th: float = VOL_Z_DEFAULT,
    kelly: bool = False,
    hedge_symbol: str = HEDGE_DEFAULT,
) -> dict:
    symbol = (symbol or "").strip().upper()
    hedge_symbol = (hedge_symbol or HEDGE_DEFAULT).strip().upper()
    if not symbol:
        raise OhlcError("请填写股票代码")
    days = max(1, min(int(days or 10), 40))
    budget = max(200.0, float(budget or DEFAULT_BUDGET))
    vol_z_th = min(3.0, max(0.5, float(vol_z_th or VOL_Z_DEFAULT)))

    bars_5, bars_30, source = load_bars(symbol)
    if len(bars_5) < LOOKBACK_5 + INT_BARS or len(bars_30) < LOOKBACK_30:
        raise OhlcError("5min 或 30min K 线不够")

    spy_bars: list[dict] = []
    spy_index: dict[int, dict] = {}
    spy_stamps: list[int] = []
    hedge_note = ""
    if hedge:
        try:
            spy_bars = wash_bars(fetch_closes_covering(hedge_symbol, "5m", start=date.today() - timedelta(days=70)))
            spy_index = spy_map(spy_bars)
            spy_stamps = sorted(spy_index)
        except Exception as exc:
            hedge = False
            hedge_note = f"{hedge_symbol} 对冲腿没拉到（{exc}），本次只做股票腿。"

    all_days = rth_days(bars_5)
    window = last_n_days(all_days, days)
    day_set = set(window)
    rth_i = [i for i, bar in enumerate(bars_5) if is_rth(bar["ts"]) and et_day(bar["ts"]) in day_set]
    rth_by_day = defaultdict(list)
    for i in rth_i:
        rth_by_day[et_day(bars_5[i]["ts"])].append(i)

    params = {
        "trend_threshold": TREND_THRESHOLD,
        "lookback_5": LOOKBACK_5,
        "lookback_30": LOOKBACK_30,
        "day_filter": day_filter,
        "k_stop": K_STOP if vol_adapt else 0.0,
        "allow_short": allow_short,
        "allow_trend": allow_trend,
        "vol_confirm": vol_confirm,
        "vol_z_th": vol_z_th,
        "min_hold_bars": MIN_HOLD_BARS,
        "mu_index": mu_index(bars_30, LOOKBACK_30),
        "sigma_index": precompute_sigma(bars_5),
        "session_highs": precompute_session_highs(bars_5),
        "atr_index": atr_wilder_series(bars_5) if vol_adapt else None,
        "garch_index": garch_scale_series(bars_5) if vol_adapt else None,
    }
    params["or_highs"], params["or_lows"] = opening_range_series(bars_5)
    vwaps = session_vwaps(bars_5)
    vol_zs = volume_z_series(bars_5)
    panics = panic_series(bars_5, vwaps)
    euphorias = euphoria_series(bars_5, vwaps)
    betas = beta_series(bars_5, spy_index, spy_stamps) if hedge else [None] * len(bars_5)

    hist = []
    hist_s = []
    first_day = window[0]
    for i, bar in enumerate(bars_5):
        if et_day(bar["ts"]) >= first_day:
            break
        if not is_rth(bar["ts"]):
            continue
        if is_local_min(bars_5, i):
            hist.append(panics[i])
        if is_local_max(bars_5, i):
            hist_s.append(euphorias[i])

    trades = []
    open_pos = None
    pending = None
    weight = 1.0
    weights = {}
    last_p70 = last_p90 = last_p70s = last_p90s = None
    last_kelly = 1.0
    for day in window:
        weights[str(day)] = round(weight, 3)
        idxs = rth_by_day.get(day, [])
        day_budget, last_kelly, _, _, _, _ = sized_budget(budget, weight, trades, kelly)
        p70 = percentile(hist, 0.7) if integral else None
        p90 = percentile(hist, 0.9) if integral else None
        p70s = percentile(hist_s, 0.7) if integral else None
        p90s = percentile(hist_s, 0.9) if integral else None
        last_p70, last_p90, last_p70s, last_p90s = p70, p90, p70s, p90s
        pending = None
        for i in idxs:
            bar = bars_5[i]
            last_i = idxs[-1]
            snap = signal_at(bars_5, bars_30, i, params)
            panic = panics[i]
            eph = euphorias[i]
            vwap = vwaps[i]
            atr = atr_5(bars_5, i)
            z = ((vwap - bar["close"]) / atr) if vwap and atr else 0.0
            z_short = ((bar["close"] - vwap) / atr) if vwap and atr else 0.0
            if snap:
                snap["panic"] = panic
                snap["euphoria"] = eph
                snap["vol_z"] = vol_zs[i] if i < len(vol_zs) else None
                snap["vol_z_peak"] = peak_vol_z(vol_zs, i)

            if open_pos is not None and i > open_pos["buy_i"]:
                reason = fill = None
                short = open_pos.get("side") == "short"
                f1 = (snap or {}).get("f1") or 0.0
                f2 = (snap or {}).get("f2") or 0.0
                if short:
                    if bar["high"] >= open_pos["stop"]:
                        reason, fill = "止损", open_pos["stop"]
                    elif open_pos.get("down") and bar["low"] <= open_pos["down"]:
                        reason, fill = "波动止盈" if vol_adapt else "−1σ", open_pos["down"]
                    elif bar["low"] <= open_pos["expect"]:
                        reason, fill = "目标E", open_pos["expect"]
                    elif snap and snap.get("fade_short") and _fade_ok(open_pos, bar["close"]):
                        reason, fill = "跌速变慢", bar["close"]
                    elif _reverse_cut(open_pos, i, bar["close"], f1, f2):
                        reason, fill = "反向加速", bar["close"]
                    elif i == last_i:
                        reason, fill = "收盘平仓", bar["close"]
                else:
                    if bar["low"] <= open_pos["stop"]:
                        reason, fill = "止损", open_pos["stop"]
                    elif bar["high"] >= open_pos["up"]:
                        reason, fill = "波动止盈" if vol_adapt else "+1σ", open_pos["up"]
                    elif bar["high"] >= open_pos["expect"]:
                        reason, fill = "目标E", open_pos["expect"]
                    elif snap and snap["fade"] and _fade_ok(open_pos, bar["close"]):
                        reason, fill = "涨速变慢", bar["close"]
                    elif _reverse_cut(open_pos, i, bar["close"], f1, f2):
                        reason, fill = "反向加速", bar["close"]
                    elif i == last_i:
                        reason, fill = "收盘平仓", bar["close"]
                if reason:
                    trades.append(
                        _flatten(open_pos, bar, fill, reason, day, hedge, spy_index, spy_stamps)
                    )
                    open_pos = None

            if pending and i == pending["i"] + 1:
                side = pending["side"]
                ok = confirm_break_down(bars_5, i) if side == "short" else confirm_break(bars_5, i)
                same = open_pos is None or open_pos.get("side") == side
                time_ok = bool(snap and snap.get("time_ok"))
                need_time = pending.get("kind") == "trend"
                if ok and (not need_time or time_ok) and i != last_i and snap and same:
                    snap["panic"] = pending.get("panic", panic)
                    snap["euphoria"] = pending.get("euphoria", eph)
                    have = open_pos["frac"] if open_pos else 0.0
                    add = max(0.0, pending["frac"] - have)
                    if add > 0.05:
                        day_budget, last_kelly, _, _, _, _ = sized_budget(budget, weight, trades, kelly)
                        snap["kelly"] = last_kelly
                        open_pos = _attach(
                            open_pos, i, bar, snap, add, day_budget, weight,
                            betas, hedge, spy_index, spy_stamps, side=side,
                            kind=pending.get("kind", "dip"),
                        )
                pending = None

            held = open_pos.get("side") if open_pos else None
            vz_ok = vol_ok_at(vol_zs, i, vol_confirm, vol_z_th)
            trend_long = bool(allow_trend and snap and snap.get("trend_long") and held != "short" and vz_ok)
            trend_short = bool(allow_trend and snap and snap.get("trend_short") and held != "long" and vz_ok)
            long_ok = bool(
                snap
                and snap.get("local_min")
                and snap.get("day_ok")
                and snap.get("mu", 0) > TREND_THRESHOLD
                and held != "short"
                and vz_ok
            )
            short_ok = bool(
                allow_short
                and snap
                and snap.get("local_max")
                and snap.get("day_ok_short")
                and snap.get("mu", 0) < -TREND_THRESHOLD
                and held != "long"
                and vz_ok
            )
            if (trend_long or trend_short) and i != last_i:
                side = "short" if trend_short else "long"
                pending = {"i": i, "frac": 1.0, "panic": panic, "euphoria": eph, "side": side, "kind": "trend"}
            elif long_ok and i != last_i:
                if integral:
                    frac = size_frac(panic, p70, p90, z)
                    if frac > 0:
                        pending = {"i": i, "frac": frac, "panic": panic, "side": "long", "kind": "dip"}
                elif open_pos is None:
                    day_budget, last_kelly, _, _, _, _ = sized_budget(budget, weight, trades, kelly)
                    snap["kelly"] = last_kelly
                    open_pos = _attach(
                        None, i, bar, snap, 1.0, day_budget, weight,
                        betas, hedge, spy_index, spy_stamps, side="long", kind="dip",
                    )
            elif short_ok and i != last_i:
                if integral:
                    frac = size_frac(eph, p70s, p90s, z_short)
                    if frac > 0:
                        pending = {"i": i, "frac": frac, "euphoria": eph, "side": "short", "kind": "dip"}
                elif open_pos is None:
                    day_budget, last_kelly, _, _, _, _ = sized_budget(budget, weight, trades, kelly)
                    snap["kelly"] = last_kelly
                    open_pos = _attach(
                        None, i, bar, snap, 1.0, day_budget, weight,
                        betas, hedge, spy_index, spy_stamps, side="short", kind="dip",
                    )

            if is_rth(bar["ts"]) and is_local_min(bars_5, i):
                hist.append(panic)
            if is_rth(bar["ts"]) and is_local_max(bars_5, i):
                hist_s.append(eph)

        if gradient and idxs:
            vol = day_vol(bars_5, idxs)
            if vol > 1e-6:
                weight = min(1.4, max(0.35, TARGET_DAY_VOL / vol))
            else:
                weight = 1.0
        else:
            weight = 1.0

    if open_pos is not None and rth_i:
        last = bars_5[rth_i[-1]]
        trades.append(
            _flatten(open_pos, last, last["close"], "未平仓", et_day(last["ts"]), hedge, spy_index, spy_stamps)
        )

    by_day = defaultdict(lambda: {"stock": 0.0, "hedge": 0.0, "net": 0.0, "trades": 0, "wins": 0})
    for t in trades:
        row = by_day[t["day"]]
        row["stock"] += t["stock_pnl"]
        row["hedge"] += t["hedge_pnl"]
        row["net"] += t["pnl"]
        row["trades"] += 1
        if t["pnl"] > 0:
            row["wins"] += 1

    daily = []
    equity = peak = max_dd = 0.0
    for day in window:
        row = by_day[str(day)]
        equity += row["net"]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        daily.append(
            {
                "date": str(day),
                "daily_pnl": round(row["net"], 2),
                "stock_pnl": round(row["stock"], 2),
                "hedge_pnl": round(row["hedge"], 2),
                "cumulative_pnl": round(equity, 2),
                "trades": row["trades"],
                "wins": row["wins"],
                "weight": weights.get(str(day), 1.0),
            }
        )

    wins = [t for t in trades if t["pnl"] > 0]
    k_scale, k_p, k_b, k_n, k_f = kelly_from_trades(trades)
    kelly_info = {"scale": round(k_scale, 3), "p": round(k_p, 3) if k_p is not None else None, "b": round(k_b, 2) if k_b is not None else None, "n": k_n, "f_star": round(k_f, 3) if k_f is not None else None}
    last_i = rth_i[-1] if rth_i else len(bars_5) - 1
    now = now_snapshot(
        bars_5, bars_30, last_i, params, panics, euphorias, vwaps,
        last_p70, last_p90, last_p70s, last_p90s, betas, spy_index, spy_stamps,
        vol_zs=vol_zs, kelly_info=kelly_info,
    )
    stock_total = round(sum(t["stock_pnl"] for t in trades), 2)
    hedge_total = round(sum(t["hedge_pnl"] for t in trades), 2)
    net = round(sum(t["pnl"] for t in trades), 2)
    return {
        "symbol": symbol,
        "hedge_symbol": hedge_symbol if hedge else None,
        "source": source,
        "days": [str(d) for d in window],
        "n_days": len(window),
        "budget": budget,
        "options": {
            "hedge": hedge,
            "integral": integral,
            "gradient": gradient,
            "day_filter": day_filter,
            "vol_adapt": vol_adapt,
            "allow_short": allow_short,
            "allow_trend": allow_trend,
            "vol_confirm": vol_confirm,
            "vol_z_th": vol_z_th,
            "kelly": kelly,
        },
        "hedge_note": hedge_note,
        "n_trades": len(trades),
        "n_long": sum(1 for t in trades if t.get("side") != "short"),
        "n_short": sum(1 for t in trades if t.get("side") == "short"),
        "n_wins": len(wins),
        "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "stock_pnl": stock_total,
        "hedge_pnl": hedge_total,
        "total_pnl": net,
        "max_dd": round(max_dd, 2),
        "avg_pnl": round(net / len(trades), 2) if trades else 0.0,
        "now": now,
        "daily": daily,
        "trades": trades,
        "tools": [
            {
                "id": "deriv",
                "name": "导数 f′ / f″",
                "role": "速度表和油门",
                "now": f"f′={now['f1']:+.3f}  f″={now['f2']:+.3f}" if now.get("f1") is not None else "—",
                "note": "5min 一阶/二阶差分。局部底做多、局部顶做空。强势日另开顺势通道：收盘站上前 30 分钟高点且 f′>0、f″>0、μ>门槛，不等局部底、不走积分闸。距收盘不足 90 分钟不开新仓。同时只持一仓。",
            },
            {
                "id": "partial",
                "name": "偏导数 β",
                "role": "风险隔离盾",
                "now": f"β={now['beta']:.2f} vs {hedge_symbol}" if now.get("beta") is not None else "未开对冲",
                "note": "用近 78 根 5min 对数收益对 SPY 做 OLS。做多时对冲名义 = −β × 股票名义；做空时相反，买回 SPY 对冲。这是用 ETF 近似股指期货，不是真的 ES。",
            },
            {
                "id": "grad",
                "name": "梯度调仓",
                "role": "收盘后方向盘",
                "now": f"次日权重 {daily[-1]['weight'] if daily else 1:.2f}",
                "note": "不是宇宙最低点。按当日 5min 波动把次日本金缩到约 1.5% 目标波动，权重夹在 0.35–1.4。",
            },
            {
                "id": "integral",
                "name": "积分闸门 + 分批",
                "role": "防半山腰",
                "now": (
                    f"多 {now.get('panic'):.4f}/{now.get('p70') or 0:.4f}  "
                    f"空 {now.get('euphoria'):.4f}/{now.get('p70s') or 0:.4f}  "
                    + (
                        ("锁多 " if now.get("halfway") else "")
                        + ("锁空" if now.get("halfway_short") else "")
                        or f"多{int((now.get('frac') or 0)*100)}% 空{int((now.get('frac_short') or 0)*100)}%"
                    )
                ),
                "note": "多头看 VWAP 下陷面积，空头看 VWAP 上伸面积，门槛都是这只票自己过去拐点的 70/90 分位。多头过关等下一根突破前高，空头过关等下一根跌破前低。浅 20%，深 50%–100%。",
            },
            {
                "id": "vol",
                "name": "ATR 止损 + GARCH 止盈",
                "role": "防扫损 / 吃满波段",
                "now": (
                    f"ATR {now.get('atr') or 0:.2f}  止损 {now.get('stop') or 0:.2f}  "
                    f"止盈 {now.get('up') or 0:.2f}  GARCH×{now.get('garch_scale') or 1:.2f}"
                ),
                "note": "止损 2.5×ATR。波动止盈至少 3×ATR 或 $20/股。目标E 至少 2.5×ATR。涨/跌速变慢要先赚满 3×ATR 或 $20/股才许走。",
            },
            {
                "id": "volz",
                "name": "成交量 Z-Score",
                "role": "量能确认，不是 3 倍天量",
                "now": (
                    f"当根 {now.get('vol_z') if now.get('vol_z') is not None else '—'}  "
                    f"近3根最高 {now.get('vol_z_peak') if now.get('vol_z_peak') is not None else '—'}  "
                    f"门槛 {now.get('vol_z_th'):.1f}  "
                    + ("过" if now.get("vol_ok") else "量不够")
                ),
                "note": "局部底当根经常缩量。过滤器看近 3 根 5min 的最高成交量 Z，不是死卡 3 倍天量，也不是卡在拐点那一根。默认关：MU 上 Z≥1 几乎只剩尾盘竞价。",
            },
            {
                "id": "kelly",
                "name": "半凯利",
                "role": "用已平仓的真实 p、b 调仓",
                "now": (
                    "样本不够，先按满仓"
                    if now.get("kelly_p") is None
                    else (
                        f"p {now['kelly_p']*100:.0f}%  b {now['kelly_b']:.2f}  "
                        f"f* {now.get('kelly_f')}  半凯利 {now.get('kelly_scale')}"
                    )
                ),
                "note": "本金栏已经是当天额度，不是总身家。f*=(pb−(1−p))/b，再乘 0.5，夹在 0.35–1.4。未满 8 笔不动仓。不是写死 58%/2.0，也不是把 10 万滚成几何神话。",
            },
        ],
    }