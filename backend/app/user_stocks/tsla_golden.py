"""局部底买入 + 漂移预算卖出。

买：5min 局部最低  S'=0、S''>0（f' 从负转正且 f''>0），且当天 30min μ>门槛。
空：5min 局部最高  S'=0、S''<0（f' 从正转负且 f''<0），且当天 30min μ<−门槛。
    过滤：μ 只用当天已收盘的 30min；多头要求最近 30min 上涨，空头要求最近 30min 下跌。
    不要求现价高于/低于当天开盘。
    可选：相对当日高点回撤 ≥ k_dip·ATR（因股制宜，见 param_matrix）。
顺势：开盘 30min 区间突破 + f′>0 且 f″>0（仍在加速），不等局部底，不走积分闸。
    距收盘不足 90 分钟（剩余 5min 根数 < 18）不开新仓。
多头卖：止盈至少买价 + 3·ATR（再和 GARCH 上沿取远的），目标E 至少 2.5·ATR 或 $15/股。
空头平：对称。止损 2.5·ATR。涨/跌速变慢要先赚满 2.5·ATR 或 $15/股，不吃几块钱的毛刺。
    z 可被 5min GARCH 倍率拉开。μ_5 = μ_30 / 6。
"""

from __future__ import annotations

import bisect
import math
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

STRATEGY = {
    "name": "MU 局部底+预算卖出",
    "notes": "5min 导数拐点买，30min μ 预算涨多少卖。",
    "symbol": "MU",
    "side": "long",
    "timeframe": "5min",
}

TREND_THRESHOLD = 0.001
LOOKBACK_30 = 4
LOOKBACK_5 = 5
Z = 1.0  # ±1σ
BARS_PER_30 = 6  # 一根 30min = 6 根 5min
K_STOP = 2.5  # 止损 = 买价 − k·ATR；MU 5min 呼吸大，2× 太容易扫
K_TP = 3.0  # 波动止盈至少 3·ATR（MU 常对应 $9–18/股）
K_TP_EXPECT = 2.5  # 目标E 至少 2.5·ATR，不吃 0.5×ATR 那种不够手续费
K_FADE = 3.0  # 涨/跌速变慢要先赚满这么多 ATR
MIN_TP_ABS = 20.0  # 每股美元下限：MU 日内常见 $20–30，不吃 $1–5 毛刺
GARCH_TIGHT = 1.08  # GARCH σ / 无条件 σ 低于此才允许涨/跌速变慢
OR_BARS = 6  # 开盘区间 = 前 30 分钟（6 根 5min）
MIN_HOLD_BARS = 18  # 距收盘不足 90 分钟不开新仓，否则只能收到尾盘残羹


def et_time(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(ET).strftime("%H:%M")


def et_dt(ts: int):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(ET)


def et_day(ts: int):
    return et_dt(ts).date()


def is_rth(ts: int) -> bool:
    t = et_dt(ts).time()
    return time(9, 30) <= t < time(16, 0)


def log_returns(closes: list[float]) -> list[float]:
    return [math.log(max(closes[i], 1e-12) / max(closes[i - 1], 1e-12)) for i in range(1, len(closes))]


def stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def gradient(xs: list[float]) -> list[float]:
    n = len(xs)
    if n == 0:
        return []
    if n == 1:
        return [0.0]
    out = [0.0] * n
    out[0] = xs[1] - xs[0]
    out[-1] = xs[-1] - xs[-2]
    for i in range(1, n - 1):
        out[i] = (xs[i + 1] - xs[i - 1]) / 2
    return out


def remaining_5m(ts: int) -> int:
    now = et_dt(ts)
    end = now.replace(hour=16, minute=0, second=0, microsecond=0)
    mins = (end - now).total_seconds() / 60
    return max(1, int(mins // 5))


def targets(buy: float, mu_30: float, sigma_5: float, n: int, z: float | None = None) -> dict:
    """S * exp(μ_5 n ± z σ √n)，μ_5 = μ_30 / 6。"""
    z = Z if z is None else z
    mu_5 = mu_30 / BARS_PER_30
    center = buy * math.exp(mu_5 * n)
    band = math.exp(z * sigma_5 * math.sqrt(n)) if sigma_5 > 0 else 1.0
    return {
        "n": n,
        "mu_5": mu_5,
        "expect": center,
        "up": center * band,
        "down": center / band,
        "pct": (center / buy - 1) * 100 if buy else 0.0,
    }


def mu_index(bars_30: list[dict], lookback: int | None = None):
    """每根已收盘 30min 的「当天」μ 和最近一根 30min 收益。"""
    lb = LOOKBACK_30 if lookback is None else lookback
    stamps: list[int] = []
    values: list[float | None] = []
    last_rets: list[float | None] = []
    for i, bar in enumerate(bars_30):
        stamps.append(int(bar["ts"]))
        mu, last = _same_day_mu(bars_30, i, lb)
        values.append(mu)
        last_rets.append(last)
    return stamps, values, last_rets


def _same_day_mu(bars_30: list[dict], i: int, lookback: int) -> tuple[float | None, float | None]:
    day = et_day(bars_30[i]["ts"])
    win = []
    for j in range(i, -1, -1):
        row = bars_30[j]
        if et_day(row["ts"]) != day:
            break
        if is_rth(row["ts"]):
            win.append(row)
    win.reverse()
    if len(win) < 2:
        return None, None
    rets = log_returns([row["close"] for row in win[-lookback:]])
    if not rets:
        return None, None
    return sum(rets) / len(rets), rets[-1]


def _index_at(index, ts: int):
    stamps = index[0]
    j = bisect.bisect_right(stamps, int(ts)) - 1
    if j < 0:
        return None
    return j


def mu_at(bars_30: list[dict], ts: int, lookback: int | None = None, index=None) -> float | None:
    lb = LOOKBACK_30 if lookback is None else lookback
    if index is not None:
        j = _index_at(index, ts)
        if j is None:
            return None
        return index[1][j]
    closed = [bar for bar in bars_30 if int(bar["ts"]) <= int(ts)]
    if not closed:
        return None
    mu, _last = _same_day_mu(closed, len(closed) - 1, lb)
    return mu


def last_30_ret(bars_30: list[dict], ts: int, lookback: int | None = None, index=None) -> float | None:
    lb = LOOKBACK_30 if lookback is None else lookback
    if index is not None and len(index) > 2:
        j = _index_at(index, ts)
        if j is None:
            return None
        return index[2][j]
    closed = [bar for bar in bars_30 if int(bar["ts"]) <= int(ts)]
    if not closed:
        return None
    _mu, last = _same_day_mu(closed, len(closed) - 1, lb)
    return last


def sigma_5(bars_5: list[dict], ts: int) -> float:
    closed = [bar for bar in bars_5 if int(bar["ts"]) <= int(ts)][-78:]
    px = [bar["close"] for bar in closed]
    return stdev(log_returns(px))


def session_high(bars_5: list[dict], i: int) -> float:
    day = et_day(bars_5[i]["ts"])
    hi = 0.0
    j = i
    while j >= 0 and et_day(bars_5[j]["ts"]) == day:
        if is_rth(bars_5[j]["ts"]):
            hi = max(hi, bars_5[j]["high"])
        j -= 1
    return hi or bars_5[i]["high"]


def _true_range(bars_5: list[dict], j: int) -> float:
    bar = bars_5[j]
    if j < 1:
        return max(bar["high"] - bar["low"], 1e-9)
    prev_c = bars_5[j - 1]["close"]
    return max(bar["high"] - bar["low"], abs(bar["high"] - prev_c), abs(bar["low"] - prev_c))


def atr_5(bars_5: list[dict], i: int, n: int = 14) -> float:
    if i < 1:
        return _true_range(bars_5, i)
    start = max(1, i - n + 1)
    trs = [_true_range(bars_5, j) for j in range(start, i + 1)]
    return sum(trs) / len(trs) if trs else 1e-9


def atr_wilder(bars_5: list[dict], i: int, n: int = 14) -> float:
    """Wilder ATR：先 SMA 再 (ATR_{t-1}*(n-1)+TR_t)/n。比简单平均更跟得上近期呼吸。"""
    if i < 1:
        return _true_range(bars_5, i)
    start = max(1, i - n * 3)
    acc = []
    atr = None
    for j in range(start, i + 1):
        tr = _true_range(bars_5, j)
        if atr is None:
            acc.append(tr)
            atr = sum(acc) / len(acc)
            if len(acc) >= n:
                acc = []
        else:
            atr = (atr * (n - 1) + tr) / n
    return atr or 1e-9


def atr_wilder_series(bars_5: list[dict], n: int = 14) -> list[float]:
    out = [0.0] * len(bars_5)
    acc = []
    atr = None
    for i in range(len(bars_5)):
        tr = _true_range(bars_5, i)
        if atr is None:
            acc.append(tr)
            atr = sum(acc) / len(acc)
            if len(acc) >= n:
                acc = []
        else:
            atr = (atr * (n - 1) + tr) / n
        out[i] = atr
    return out


def confirm_break(bars_5: list[dict], i: int) -> bool:
    """下一根 5min 收盘站上前高，或至少走出 0.5 个 5min ATR，才算真转头向上。"""
    if i < 1:
        return False
    prev = bars_5[i - 1]
    now = bars_5[i]
    return now["close"] > prev["high"] or now["close"] > prev["close"] + 0.5 * atr_5(bars_5, i)


def confirm_break_down(bars_5: list[dict], i: int) -> bool:
    """下一根 5min 收盘跌破前低，或至少走出 0.5 个 5min ATR，才算真转头向下。"""
    if i < 1:
        return False
    prev = bars_5[i - 1]
    now = bars_5[i]
    return now["close"] < prev["low"] or now["close"] < prev["close"] - 0.5 * atr_5(bars_5, i)


def session_open(bars_5: list[dict], i: int) -> float | None:
    day = et_day(bars_5[i]["ts"])
    first = None
    j = i
    while j >= 0 and et_day(bars_5[j]["ts"]) == day:
        if is_rth(bars_5[j]["ts"]):
            first = bars_5[j]["open"]
        j -= 1
    return first


def opening_range_series(bars_5: list[dict], n: int = OR_BARS) -> tuple[list[float | None], list[float | None]]:
    """每个时点当天前 n 根 5min 的开盘区间高低。未走完区间时为 None。"""
    hi_out: list[float | None] = [None] * len(bars_5)
    lo_out: list[float | None] = [None] * len(bars_5)
    day = None
    bucket_h: list[float] = []
    bucket_l: list[float] = []
    frozen_h = frozen_l = None
    for i, bar in enumerate(bars_5):
        if not is_rth(bar["ts"]):
            hi_out[i] = frozen_h
            lo_out[i] = frozen_l
            continue
        d = et_day(bar["ts"])
        if d != day:
            day = d
            bucket_h, bucket_l = [], []
            frozen_h = frozen_l = None
        if frozen_h is None:
            bucket_h.append(bar["high"])
            bucket_l.append(bar["low"])
            if len(bucket_h) >= n:
                frozen_h = max(bucket_h)
                frozen_l = min(bucket_l)
        hi_out[i] = frozen_h
        lo_out[i] = frozen_l
    return hi_out, lo_out


def signal_at(bars_5: list[dict], bars_30: list[dict], i: int, params: dict | None = None) -> dict | None:
    cfg = params or {}
    lb5 = int(cfg.get("lookback_5", LOOKBACK_5))
    thresh = float(cfg.get("trend_threshold", TREND_THRESHOLD))
    z = float(cfg.get("z", Z))
    day_filter = bool(cfg.get("day_filter", True))
    if i < lb5 - 1:
        return None
    win = bars_5[i - lb5 + 1 : i + 1]
    px = [bar["close"] for bar in win]
    speed = gradient(px)
    accel = gradient(speed)
    mu_idx = cfg.get("mu_index")
    mu = mu_at(bars_30, bars_5[i]["ts"], lookback=cfg.get("lookback_30"), index=mu_idx)
    last_30 = last_30_ret(bars_30, bars_5[i]["ts"], lookback=cfg.get("lookback_30"), index=mu_idx)
    if mu is None or len(speed) < 2:
        return None
    f1, f2, prev = speed[-1], accel[-1], speed[-2]
    local_min = prev < 0 and f1 >= 0 and f2 > 0
    local_max = prev > 0 and f1 <= 0 and f2 < 0
    bar = bars_5[i]
    px_now = bar["close"]
    atr = float(cfg.get("atr") or 0.0)
    atrs = cfg.get("atr_index")
    if atrs is not None:
        atr = float(atrs[i] or 0.0)
    elif atr <= 0:
        atr = atr_wilder(bars_5, i)
    k_dip = float(cfg.get("k_dip") or 0.0)
    highs = cfg.get("session_highs")
    hi = highs[i] if highs is not None else session_high(bars_5, i)
    dip_ok = k_dip <= 0 or atr <= 0 or (hi - px_now) >= k_dip * atr
    opens = cfg.get("session_opens")
    day_open = opens[i] if opens is not None else session_open(bars_5, i)
    above_open = day_open is None or px_now >= day_open
    last_up = last_30 is None or last_30 > 0
    last_down = last_30 is None or last_30 < 0
    day_ok = (not day_filter) or last_up
    day_ok_short = (not day_filter) or last_down
    allow_short = bool(cfg.get("allow_short", False))
    n = remaining_5m(bar["ts"])
    min_hold = int(cfg.get("min_hold_bars", MIN_HOLD_BARS))
    time_ok = n >= min_hold
    or_highs = cfg.get("or_highs")
    or_lows = cfg.get("or_lows")
    or_hi = or_highs[i] if or_highs is not None else None
    or_lo = or_lows[i] if or_lows is not None else None
    allow_trend = bool(cfg.get("allow_trend", False))
    accel_up = f1 > 0 and f2 > 0
    accel_dn = f1 < 0 and f2 < 0
    trend_long = bool(
        allow_trend and time_ok and or_hi and px_now > or_hi and accel_up and mu > thresh and day_ok
    )
    trend_short = bool(
        allow_trend
        and allow_short
        and time_ok
        and or_lo
        and px_now < or_lo
        and accel_dn
        and mu < -thresh
        and day_ok_short
    )
    fire = mu > thresh and local_min and dip_ok and day_ok
    fire_short = allow_short and mu < -thresh and local_max and day_ok_short
    sigs = cfg.get("sigma_index")
    sig5 = sigs[i] if sigs is not None else sigma_5(bars_5, bar["ts"])
    garchs = cfg.get("garch_index")
    garch_scale = 1.0
    if garchs is not None and i < len(garchs) and garchs[i]:
        garch_scale = float(garchs[i])
    tgt = targets(px_now, mu, sig5, n, z=z * garch_scale)
    k_tp = float(cfg["k_tp"]) if "k_tp" in cfg else K_TP
    k_tp_e = float(cfg["k_tp_expect"]) if "k_tp_expect" in cfg else K_TP_EXPECT
    if atr > 0 and k_tp > 0:
        band = max(k_tp * atr, MIN_TP_ABS)
        tgt["up"] = max(tgt["up"], px_now + band)
        tgt["down"] = min(tgt["down"], px_now - band)
    if atr > 0 and k_tp_e > 0:
        band_e = max(k_tp_e * atr, MIN_TP_ABS)
        tgt["expect"] = max(tgt["expect"], px_now + band_e) if mu >= 0 else min(tgt["expect"], px_now - band_e)
    tgt["pct"] = (tgt["up"] / px_now - 1) * 100 if px_now else 0.0
    fade = f1 > 0 and f2 < 0
    fade_short = f1 < 0 and f2 > 0
    win_stop = min(row["low"] for row in win)
    win_stop_short = max(row["high"] for row in win)
    k_stop = float(cfg["k_stop"]) if "k_stop" in cfg else K_STOP
    if k_stop > 0 and atr > 0:
        atr_stop = px_now - k_stop * atr
        atr_stop_short = px_now + k_stop * atr
    else:
        atr_stop = win_stop
        atr_stop_short = win_stop_short
    return {
        "mu": mu,
        "last_30": last_30,
        "f1": f1,
        "f2": f2,
        "prev": prev,
        "local_min": local_min,
        "local_max": local_max,
        "dip_ok": dip_ok,
        "above_open": above_open,
        "last_up": last_up,
        "last_down": last_down,
        "day_ok": day_ok,
        "day_ok_short": day_ok_short,
        "day_open": day_open,
        "fire": fire,
        "fire_short": fire_short,
        "trend_long": trend_long,
        "trend_short": trend_short,
        "time_ok": time_ok,
        "or_hi": round(or_hi, 4) if or_hi else None,
        "or_lo": round(or_lo, 4) if or_lo else None,
        "fade": fade,
        "fade_short": fade_short,
        "bar": bar,
        "stop": atr_stop,
        "stop_short": atr_stop_short,
        "atr": atr,
        "k_stop": k_stop,
        "k_tp": k_tp,
        "k_tp_expect": k_tp_e,
        "k_fade": float(cfg["k_fade"]) if "k_fade" in cfg else K_FADE,
        "min_tp_abs": float(cfg["min_tp_abs"]) if "min_tp_abs" in cfg else MIN_TP_ABS,
        "garch_scale": garch_scale,
        "vol_tight": garch_scale < GARCH_TIGHT,
        "sigma": sig5,
        **tgt,
    }


def buy_points_today(bars_5: list[dict], bars_30: list[dict]) -> list[dict]:
    today = et_day(bars_5[-1]["ts"])
    points = []
    for i, bar in enumerate(bars_5):
        if et_day(bar["ts"]) != today or not is_rth(bar["ts"]):
            continue
        snap = signal_at(bars_5, bars_30, i)
        if snap and snap["fire"]:
            points.append(snap)
    return points


def run(ctx):
    sym = ctx.symbol
    bars_5 = ctx.bars(sym, "5m")
    bars_30 = ctx.bars(sym, "30m")
    if len(bars_5) < LOOKBACK_5 + 2 or len(bars_30) < LOOKBACK_30:
        return ctx.result(False, "5min 或 30min K 线不够")

    now_i = len(bars_5) - 1
    now = signal_at(bars_5, bars_30, now_i)
    if not now:
        return ctx.result(False, "还算不出 μ / 导数")

    last = now["bar"]["close"]
    mu, f1, f2 = now["mu"], now["f1"], now["f2"]
    entries = buy_points_today(bars_5, bars_30)
    latest = entries[-1] if entries else None

    if now["fire"]:
        action = f"现在买 {sym} @ {last:.2f}"
        why = (
            f"局部底（f′ 负→非负，f′′>0）。"
            f"止盈 {now['up']:.2f}（+{now['pct']:.2f}%）  止损 {now['stop']:.2f}  剩 {now['n']} 根5min。"
        )
        hit = True
    elif latest and now["fade"] and now.get("vol_tight", True):
        action = f"涨速变慢，考虑卖"
        why = (
            f"f′>0 但 f′′<0。买点 {latest['bar']['close']:.2f} 目标 {latest['expect']:.2f}，"
            f"现价 {last:.2f}。"
        )
        hit = False
    elif latest and f1 > 0:
        action = f"已有买点，持有"
        why = (
            f"最近买 {et_time(latest['bar']['ts'])} @ {latest['bar']['close']:.2f} → "
            f"止盈 {latest['up']:.2f}（+{latest['pct']:.2f}%）。现价 {last:.2f}。"
        )
        hit = True
    elif now.get("local_min") and not now.get("day_ok"):
        action = "过滤：不买"
        bits = []
        if not now.get("last_up"):
            bits.append("最近一根 30min 还在跌")
        why = "；".join(bits) or "当天方向过滤未过。"
        hit = False
    elif mu <= 0:
        action = "现在不买"
        why = "30min μ≤0，没有买点。"
        hit = False
    else:
        action = "等局部底"
        why = f"μ={mu:.4f} 已向上。等 5min f′ 从负转正且 f′′>0，参考 {last:.2f}。"
        hit = False

    if getattr(ctx, "cli", False):
        print("========= 局部底买入 · 漂移预算卖出 =========")
        print(f"{sym}  现价 {last:.2f}   μ_30={mu:.4f}  μ_5={mu / BARS_PER_30:.5f}  σ_5={now['sigma']:.5f}")
        print(f"f′={f1:+.3f}  f′′={f2:+.3f}  局部底={now['local_min']}  30min↑={now.get('last_up')}  剩 {now['n']} 根5min")
        print("-" * 56)
        print("【今天买点】当天 μ、最近 30min 须上涨（允许低于开盘）")
        print(f"{'时间':<8} {'买入':>10} {'止盈':>10} {'止损':>10} {'预算':>8}")
        if not entries:
            print("  今天还没有局部底。")
        for snap in entries:
            bar = snap["bar"]
            print(
                f"  {et_time(bar['ts']):<6} {bar['close']:10.2f} {snap['up']:10.2f} "
                f"{snap['stop']:10.2f} {snap['pct']:+7.2f}%"
            )
        print("-" * 56)
        print(action)
        print(why)
        print("卖：2.5×ATR 止损；止盈至少 3×ATR 或 $15/股。涨/跌速变慢要先赚满 2.5×ATR 或 $15/股。")
        print("=" * 56)

    note = f"{action} · {why}"
    return ctx.result(hit, note)


def _standalone():
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.services.user_stock_files import preview_module

    preview_module(STRATEGY, run, n=0)


if __name__ == "__main__":
    _standalone()
