from __future__ import annotations

EMA_PERIODS = (5, 10, 20, 144, 169)
DEFAULT_MA_PERIODS = list(EMA_PERIODS)
DEFAULT_CROSS_PAIRS = [(5, 10), (10, 20), (5, 20), (144, 169)]
CROSS_NEAR = 2.0
RSI_PERIOD = 14
BB_PERIOD = 20
BB_K = 2.0
RANGE_LOOKBACK = 20
RANGE_PCT = 0.08
EMA_FLAT_PCT = 0.015
Z_WINDOW = 20
Z_OVERSOLD = -2.0
Z_STOP = -3.0
LEVEL_NEAR = 2.0
SWING_LEFT = 2
SWING_RIGHT = 2
RECENT_LEVELS = 2
VOL_DIVERGE = 0.8
VOL_Z_TOP = 2.0
STALL_PCT = 0.01
SHADOW_TOP = 0.5
SHADOW_COMBO = 0.4
VOL_SHADOW_MULT = 1.5
VOL_Z_DRY = -1.5
DROP_NARROW = 0.6
NEAR_LOW_PCT = 0.02
ATR_PERIOD = 14
ATR_BASE = 60
ATR_SQUEEZE = 0.6
SQUEEZE_BARS = 6


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    var = sum((x - avg) ** 2 for x in values) / (len(values) - 1)
    return var ** 0.5


def ema_path(closes: list[float], period: int) -> tuple[float | None, float | None]:
    if len(closes) < period or period <= 0:
        return None, None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    prev = None
    for price in closes[period:]:
        prev = ema
        ema = price * k + ema * (1 - k)
    return prev, ema


def ema_last(closes: list[float], period: int) -> float | None:
    _prev, last = ema_path(closes, period)
    return last


def ema_map(closes: list[float], periods: tuple[int, ...] = EMA_PERIODS) -> dict[str, float | None]:
    out = {}
    for n in periods:
        value = ema_last(closes, n)
        out[f"ema{n}"] = None if value is None else round(value, 4)
    return out


def rsi_pair(closes: list[float], period: int = RSI_PERIOD) -> tuple[float | None, float | None]:
    if len(closes) < period + 2:
        return None, None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_g = _mean([max(d, 0.0) for d in deltas[:period]])
    avg_l = _mean([max(-d, 0.0) for d in deltas[:period]])
    prev = None
    last = None
    for delta in deltas[period:]:
        avg_g = (avg_g * (period - 1) + max(delta, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-delta, 0.0)) / period
        rs = avg_g / avg_l if avg_l else (100.0 if avg_g else 0.0)
        prev = last
        last = 100.0 - (100.0 / (1.0 + rs)) if avg_l or avg_g else 50.0
    return last, prev


def rsi_last(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    last, _prev = rsi_pair(closes, period)
    return last


def rsi_series(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    avg_g = _mean([max(d, 0.0) for d in deltas[:period]])
    avg_l = _mean([max(-d, 0.0) for d in deltas[:period]])
    rs = avg_g / avg_l if avg_l else (100.0 if avg_g else 0.0)
    out[period] = 100.0 - (100.0 / (1.0 + rs)) if avg_l or avg_g else 50.0
    idx = period
    for delta in deltas[period:]:
        idx += 1
        avg_g = (avg_g * (period - 1) + max(delta, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-delta, 0.0)) / period
        rs = avg_g / avg_l if avg_l else (100.0 if avg_g else 0.0)
        out[idx] = 100.0 - (100.0 / (1.0 + rs)) if avg_l or avg_g else 50.0
    return out


def cross_price(fast_ema: float, slow_ema: float, fast_n: int, slow_n: int) -> float | None:
    kf = 2 / (fast_n + 1)
    ks = 2 / (slow_n + 1)
    denom = kf - ks
    if abs(denom) < 1e-12:
        return None
    price = (slow_ema * (1 - ks) - fast_ema * (1 - kf)) / denom
    if price <= 0:
        return None
    return round(price, 2)


def ema_crosses(
    closes: list[float],
    price: float | None,
    pairs: list[tuple[int, int]] | None = None,
    near_usd: float = CROSS_NEAR,
) -> list[dict]:
    pairs = pairs or DEFAULT_CROSS_PAIRS
    paths = {n: ema_path(closes, n) for pair in pairs for n in pair}
    rows = []
    for fast_n, slow_n in pairs:
        prev_fast, fast = paths.get(fast_n, (None, None))
        prev_slow, slow = paths.get(slow_n, (None, None))
        if fast is None or slow is None:
            rows.append({
                "pair": f"EMA{fast_n} / EMA{slow_n}",
                "fast": fast_n,
                "slow": slow_n,
                "kind": None,
                "state": "K 线不够",
                "price": None,
                "distance": None,
                "near": False,
            })
            continue

        happened = None
        if prev_fast is not None and prev_slow is not None:
            if prev_fast < prev_slow and fast >= slow:
                happened = "金叉"
            elif prev_fast > prev_slow and fast <= slow:
                happened = "死叉"

        if happened and prev_fast is not None and prev_slow is not None:
            level = cross_price(prev_fast, prev_slow, fast_n, slow_n)
        else:
            level = cross_price(fast, slow, fast_n, slow_n)

        if happened:
            kind = happened
            state = f"本根已{happened}"
        elif fast < slow:
            kind = "金叉"
            state = "待金叉"
        elif fast > slow:
            kind = "死叉"
            state = "待死叉"
        else:
            kind = "金叉"
            state = "均线重合"

        distance = None
        near = False
        if level is not None and price is not None:
            distance = round(level - price, 2)
            near = abs(distance) <= near_usd or abs(fast - slow) <= near_usd
        rows.append({
            "pair": f"EMA{fast_n} / EMA{slow_n}",
            "fast": fast_n,
            "slow": slow_n,
            "kind": kind,
            "state": state,
            "price": level,
            "distance": distance,
            "near": near,
        })
    return rows


def range_box(bars: list[dict], closes: list[float], lookback: int = RANGE_LOOKBACK) -> dict:
    window = bars[-lookback:] if len(bars) >= lookback else bars
    if not window:
        return {
            "low": None,
            "high": None,
            "mid": None,
            "pct": None,
            "sideways": False,
            "lookback": lookback,
        }
    highs = [float(bar.get("high") if bar.get("high") is not None else bar["close"]) for bar in window]
    lows = [float(bar.get("low") if bar.get("low") is not None else bar["close"]) for bar in window]
    high = max(highs)
    low = min(lows)
    mid = (high + low) / 2
    pct = ((high - low) / mid) if mid else 0.0
    ema5 = ema_last(closes, 5)
    ema20 = ema_last(closes, 20)
    close = closes[-1] if closes else mid
    flat = (
        ema5 is not None
        and ema20 is not None
        and close
        and abs(ema5 - ema20) / close <= EMA_FLAT_PCT
    )
    sideways = pct <= RANGE_PCT and flat
    return {
        "low": round(low, 4),
        "high": round(high, 4),
        "mid": round(mid, 4),
        "pct": round(pct * 100, 2),
        "sideways": sideways,
        "lookback": lookback,
    }


def bollinger(closes: list[float], period: int = BB_PERIOD, k: float = BB_K) -> dict:
    empty = {
        "upper": None,
        "middle": None,
        "lower": None,
        "bandwidth": None,
        "up": False,
        "down": False,
    }
    if len(closes) < period + 1:
        return empty
    now = [float(x) for x in closes[-period:]]
    prev = [float(x) for x in closes[-(period + 1):-1]]
    middle = _mean(now)
    prev_mid = _mean(prev)
    std = _std(now)
    prev_std = _std(prev)
    if middle <= 0:
        return empty
    upper = middle + k * std
    lower = middle - k * std
    prev_upper = prev_mid + k * prev_std
    prev_lower = prev_mid - k * prev_std
    bandwidth = (upper - lower) / middle
    prev_bw = (prev_upper - prev_lower) / prev_mid if prev_mid else 0.0
    close = float(closes[-1])
    expanding = bandwidth >= prev_bw
    up = middle > prev_mid and close > middle and expanding
    down = middle < prev_mid and close < middle and expanding
    return {
        "upper": round(upper, 4),
        "middle": round(middle, 4),
        "lower": round(lower, 4),
        "bandwidth": round(bandwidth * 100, 2),
        "up": up,
        "down": down,
    }


def z_score(closes: list[float], window: int = Z_WINDOW,
            z_entry: float = Z_OVERSOLD, z_stop: float = Z_STOP) -> dict | None:
    if len(closes) < window:
        return None
    sample = [float(x) for x in closes[-window:]]
    ma = _mean(sample)
    std = _std(sample)
    close = sample[-1]
    z = (close - ma) / std if std else 0.0
    return {
        "close": round(close, 4),
        "ma20": round(ma, 4),
        "std": round(std, 4),
        "z": round(z, 2),
        "entry_ok": z < z_entry,
        "stop_price": round(ma + z_stop * std, 4) if std else None,
        "tp_price": round(ma, 4),
    }


def is_oversold(closes: list[float], window: int = Z_WINDOW,
                threshold: float = Z_OVERSOLD, timeframe: str = "") -> dict:
    row = z_score(closes, window)
    if row is None:
        return {
            "oversold": False,
            "z": None,
            "ma20": None,
            "threshold": threshold,
            "timeframe": timeframe,
            "shortage": True,
        }
    return {
        "oversold": row["z"] < threshold,
        "z": row["z"],
        "ma20": row["ma20"],
        "threshold": threshold,
        "timeframe": timeframe,
        "shortage": False,
    }


def intraday_oversold(closes_4h: list[float]) -> dict:
    """日内是否超跌：用 4h K 线的 20 根 Z 分数，z < -2。"""
    return is_oversold(closes_4h, timeframe="4h")


def week_oversold(closes_1d: list[float]) -> dict:
    """周内是否超跌：用日线 K 线的 20 根 Z 分数，z < -2。"""
    return is_oversold(closes_1d, timeframe="1d")


def ema_align(emas: dict) -> dict:
    e5, e10, e20 = emas.get("ema5"), emas.get("ema10"), emas.get("ema20")
    e144, e169 = emas.get("ema144"), emas.get("ema169")
    if e5 is None or e10 is None or e20 is None:
        return {"align": None, "label": "K 线不够", "detail": ""}
    if e5 > e10 > e20:
        align = "多头"
        label = "多头排列"
        detail = "EMA5 > EMA10 > EMA20"
    elif e5 < e10 < e20:
        align = "空头"
        label = "空头排列"
        detail = "EMA5 < EMA10 < EMA20"
    else:
        align = "纠缠"
        label = "未排好"
        detail = "短均线纠缠"
    if e144 is not None and e169 is not None:
        if e144 > e169:
            detail += " · 长周期 EMA144 > EMA169"
        elif e144 < e169:
            detail += " · 长周期 EMA144 < EMA169"
    return {"align": align, "label": label, "detail": detail}


def volume_levels(bars: list[dict], price: float | None, near: float = LEVEL_NEAR) -> dict:
    empty = {
        "resistance": None,
        "support": None,
        "total_volume": 0.0,
    }
    if not bars or price is None:
        return empty
    nodes: list[dict] = []
    total = 0.0
    ranked = sorted(bars, key=lambda bar: float(bar.get("volume") or 0), reverse=True)
    for bar in ranked:
        close = bar.get("close")
        vol = float(bar.get("volume") or 0)
        if close is None or vol <= 0:
            continue
        px = float(close)
        total += vol
        hit = None
        for node in nodes:
            if abs(px - node["price"]) <= near:
                hit = node
                break
        if hit is None:
            nodes.append({"price": px, "volume": vol, "pv": px * vol})
        else:
            hit["volume"] += vol
            hit["pv"] += px * vol
            hit["price"] = hit["pv"] / hit["volume"]

    for node in nodes:
        node["price"] = round(node["price"], 2)
        node["volume"] = round(node["volume"], 2)
        node["share"] = round(node["volume"] / total * 100, 1) if total else 0.0

    above = [n for n in nodes if n["price"] > price + near]
    below = [n for n in nodes if n["price"] < price - near]
    resistance = max(above, key=lambda n: n["volume"]) if above else None
    support = max(below, key=lambda n: n["volume"]) if below else None
    return {
        "resistance": None if resistance is None else {
            "price": resistance["price"],
            "volume": resistance["volume"],
            "share": resistance["share"],
        },
        "support": None if support is None else {
            "price": support["price"],
            "volume": support["volume"],
            "share": support["share"],
        },
        "total_volume": round(total, 2),
    }


def level_signals(bars: list[dict]) -> dict:
    """用上一根之前的量能档 / 摆动点，判断本根有没有突破压力、站稳支撑。"""
    empty = {"break_resistance": False, "hold_support": False, "level_res": None, "level_sup": None}
    if len(bars) < 8:
        return empty
    prev_close = float(bars[-2]["close"])
    prior = bars[:-1]
    prior_levels = volume_levels(prior, prev_close)
    prior_recent = recent_levels(prior, prev_close)
    res_pts = []
    sup_pts = []
    if prior_levels.get("resistance") and prior_levels["resistance"].get("price") is not None:
        res_pts.append(float(prior_levels["resistance"]["price"]))
    if prior_levels.get("support") and prior_levels["support"].get("price") is not None:
        sup_pts.append(float(prior_levels["support"]["price"]))
    for row in prior_recent.get("recent_resistance") or []:
        if row.get("price") is not None:
            res_pts.append(float(row["price"]))
    for row in prior_recent.get("recent_support") or []:
        if row.get("price") is not None:
            sup_pts.append(float(row["price"]))
    res = min((p for p in res_pts if p > prev_close), default=None)
    sup = max((p for p in sup_pts if p < prev_close), default=None)
    open_px, high, low, close, _vol = _bar_ohlcv(bars[-1])
    near = max(LEVEL_NEAR, abs(close) * 0.008)
    break_res = res is not None and prev_close <= res + 1e-9 and close > res
    hold_sup = (
        sup is not None
        and low <= sup + near
        and close > sup
        and close >= open_px
    )
    return {
        "break_resistance": bool(break_res),
        "hold_support": bool(hold_sup),
        "level_res": None if res is None else round(res, 4),
        "level_sup": None if sup is None else round(sup, 4),
    }


def _bar_hl(bar: dict) -> tuple[float, float]:
    close = float(bar["close"])
    high = bar.get("high")
    low = bar.get("low")
    return (
        float(high) if high is not None else close,
        float(low) if low is not None else close,
    )


def recent_levels(
    bars: list[dict],
    price: float | None,
    count: int = RECENT_LEVELS,
    near: float = LEVEL_NEAR,
) -> dict:
    """最近摆动高点 / 低点，各取 2 个互不相同的价格。"""
    empty = {"recent_resistance": [], "recent_support": []}
    left, right = SWING_LEFT, SWING_RIGHT
    if len(bars) < left + right + 1:
        return empty
    highs = []
    lows = []
    for i in range(left, len(bars) - right):
        high, low = _bar_hl(bars[i])
        left_h = [_bar_hl(bars[i - k])[0] for k in range(1, left + 1)]
        right_h = [_bar_hl(bars[i + k])[0] for k in range(1, right + 1)]
        left_l = [_bar_hl(bars[i - k])[1] for k in range(1, left + 1)]
        right_l = [_bar_hl(bars[i + k])[1] for k in range(1, right + 1)]
        if all(high >= h for h in left_h) and all(high > h for h in right_h):
            highs.append(round(high, 2))
        if all(low <= lv for lv in left_l) and all(low < lv for lv in right_l):
            lows.append(round(low, 2))

    def pick(values: list[float]) -> list[dict]:
        picked = []
        for px in reversed(values):
            if any(abs(px - item["price"]) <= near for item in picked):
                continue
            side = None
            if price is not None:
                if px > price + near:
                    side = "上方"
                elif px < price - near:
                    side = "下方"
                else:
                    side = "附近"
            picked.append({"price": px, "side": side})
            if len(picked) >= count:
                break
        return picked

    return {
        "recent_resistance": pick(highs),
        "recent_support": pick(lows),
    }


def _bar_ohlcv(bar: dict) -> tuple[float, float, float, float, float]:
    close = float(bar["close"])
    open_px = float(bar["open"]) if bar.get("open") is not None else close
    high, low = _bar_hl(bar)
    vol = float(bar.get("volume") or 0)
    return open_px, high, low, close, vol


def _swing_highs(bars: list[dict]) -> list[dict]:
    left, right = SWING_LEFT, SWING_RIGHT
    out = []
    if len(bars) < left + right + 1:
        return out
    for i in range(left, len(bars) - right):
        _o, high, _l, close, vol = _bar_ohlcv(bars[i])
        left_h = [_bar_hl(bars[i - k])[0] for k in range(1, left + 1)]
        right_h = [_bar_hl(bars[i + k])[0] for k in range(1, right + 1)]
        if all(high >= h for h in left_h) and all(high > h for h in right_h):
            out.append({"i": i, "price": high, "close": close, "volume": vol})
    return out


def _swing_lows(bars: list[dict]) -> list[dict]:
    left, right = SWING_LEFT, SWING_RIGHT
    out = []
    if len(bars) < left + right + 1:
        return out
    for i in range(left, len(bars) - right):
        _o, _h, low, close, vol = _bar_ohlcv(bars[i])
        left_l = [_bar_hl(bars[i - k])[1] for k in range(1, left + 1)]
        right_l = [_bar_hl(bars[i + k])[1] for k in range(1, right + 1)]
        if all(low <= lv for lv in left_l) and all(low < lv for lv in right_l):
            out.append({"i": i, "price": low, "close": close, "volume": vol})
    return out


def _true_range(bars: list[dict], i: int) -> float:
    _o, high, low, close, _v = _bar_ohlcv(bars[i])
    if i <= 0:
        return high - low
    prev_c = float(bars[i - 1]["close"])
    return max(high - low, abs(high - prev_c), abs(low - prev_c))


def _atr(bars: list[dict], end: int, period: int = ATR_PERIOD) -> float | None:
    start = end - period + 1
    if start < 1:
        return None
    trs = [_true_range(bars, i) for i in range(start, end + 1)]
    return _mean(trs) if trs else None


def _drop_from_prior_high(bars: list[dict], lows: list[dict], idx: int) -> float | None:
    n = len(lows)
    if idx < 0:
        idx = n + idx
    if idx < 0 or idx >= n:
        return None
    low = lows[idx]
    high_px = None
    for j in range(low["i"] - 1, -1, -1):
        high, _l = _bar_hl(bars[j])
        if high_px is None or high > high_px:
            high_px = high
        if idx > 0 and j <= lows[idx - 1]["i"]:
            break
    if not high_px:
        return None
    return (high_px - low["price"]) / high_px


def selling_exhaustion(bars: list[dict], closes: list[float]) -> dict:
    """空头砸不动了：地量 / 跌幅收窄 / 长下影 / ATR收缩 / RSI底背离。综合需 (地量或收窄) 且下影>0.4。"""
    empty = {
        "ok": False,
        "dry": False,
        "narrow": False,
        "lower_shadow": False,
        "squeeze": False,
        "rsi_div": False,
        "volume_z": None,
        "shadow_ratio": None,
        "atr_ratio": None,
        "state": "K 线不够",
    }
    if len(bars) < 22 or len(closes) < 22:
        return empty

    open_px, high, low, close, vol = _bar_ohlcv(bars[-1])
    prior_vol = [float(b.get("volume") or 0) for b in bars[-21:-1]]
    avg_vol = _mean(prior_vol)
    std_vol = _std(prior_vol)
    volume_z = (vol - avg_vol) / std_vol if std_vol else 0.0
    low_20 = min(_bar_hl(b)[1] for b in bars[-21:-1])
    near_low = low <= low_20 * (1 + NEAR_LOW_PCT) or close <= low_20 * (1 + NEAR_LOW_PCT)
    dry = volume_z < VOL_Z_DRY and near_low

    rng = high - low
    body_bot = min(open_px, close)
    shadow_ratio = ((body_bot - low) / rng) if rng > 0 else 0.0
    lower_shadow = shadow_ratio > SHADOW_TOP
    shadow_combo = shadow_ratio > SHADOW_COMBO

    lows = _swing_lows(bars)
    narrow = False
    if len(lows) >= 2:
        d1 = _drop_from_prior_high(bars, lows, -2)
        d2 = _drop_from_prior_high(bars, lows, -1)
        if (
            d1 is not None and d2 is not None
            and lows[-1]["price"] < lows[-2]["price"]
            and d2 < d1 * DROP_NARROW
        ):
            narrow = True
    if lows and low < lows[-1]["price"] and len(lows) >= 1:
        # 本根还在创新低，用本根对上一个低点比较跌幅
        fake = lows + [{"i": len(bars) - 1, "price": low, "close": close, "volume": vol}]
        d1 = _drop_from_prior_high(bars, fake, -2)
        d2 = _drop_from_prior_high(bars, fake, -1)
        if d1 is not None and d2 is not None and d2 < d1 * DROP_NARROW:
            narrow = True

    atr_now = _atr(bars, len(bars) - 1, ATR_PERIOD)
    atr_base = None
    if len(bars) >= ATR_PERIOD + ATR_BASE:
        past = [
            _atr(bars, i, ATR_PERIOD)
            for i in range(len(bars) - ATR_BASE, len(bars) - 1)
        ]
        past = [x for x in past if x]
        atr_base = _mean(past) if past else None
    atr_ratio = (atr_now / atr_base) if atr_now and atr_base else None
    window = bars[-SQUEEZE_BARS:]
    w_high = max(_bar_hl(b)[0] for b in window)
    w_low = min(_bar_hl(b)[1] for b in window)
    mid = (w_high + w_low) / 2 if w_high + w_low else 0
    tight = mid > 0 and (w_high - w_low) / mid <= 0.05
    squeeze = atr_ratio is not None and atr_ratio < ATR_SQUEEZE and tight

    rsi_div = False
    rsis = rsi_series(closes)
    rsi_now = rsis[-1]
    if lows and rsi_now is not None:
        prev = lows[-1]
        prev_rsi = rsis[prev["i"]] if prev["i"] < len(rsis) else None
        if prev_rsi is not None and low < prev["price"] and rsi_now > prev_rsi:
            rsi_div = True
        elif len(lows) >= 2:
            a, b = lows[-2], lows[-1]
            ra, rb = rsis[a["i"]], rsis[b["i"]]
            if ra is not None and rb is not None and b["price"] < a["price"] and rb > ra:
                rsi_div = True

    confirmed = (dry or narrow) and shadow_combo
    parts = []
    if dry:
        parts.append("地量")
    if narrow:
        parts.append("跌幅收窄")
    if lower_shadow:
        parts.append("长下影")
    if squeeze:
        parts.append("波动收缩")
    if rsi_div:
        parts.append("RSI底背离")
    if confirmed:
        state = "空头衰竭 · " + " / ".join(parts) + "（先观察，不立刻抄底）"
    elif parts:
        state = "单项有 · " + " / ".join(parts) + "（综合还不够）"
    else:
        state = "没有衰竭信号"

    return {
        "ok": confirmed,
        "dry": dry,
        "narrow": narrow,
        "lower_shadow": lower_shadow,
        "squeeze": squeeze,
        "rsi_div": rsi_div,
        "volume_z": round(volume_z, 2),
        "shadow_ratio": round(shadow_ratio, 2),
        "atr_ratio": None if atr_ratio is None else round(atr_ratio, 2),
        "state": state,
    }


def volume_climax_top(bars: list[dict], closes: list[float]) -> dict:
    """放量见顶：量价背离 / 巨量滞涨 / 长上影 / 假突破，综合需 (背离或滞涨) 且上影>0.4。"""
    empty = {
        "ok": False,
        "divergence": False,
        "stall": False,
        "upper_shadow": False,
        "false_break": False,
        "rsi_div": False,
        "volume_z": None,
        "shadow_ratio": None,
        "change_pct": None,
        "state": "K 线不够",
    }
    if len(bars) < 22 or len(closes) < 22:
        return empty

    open_px, high, low, close, vol = _bar_ohlcv(bars[-1])
    prev_close = float(closes[-2])
    change = (close - prev_close) / prev_close if prev_close else 0.0
    yin = close < open_px
    prior_vol = [float(b.get("volume") or 0) for b in bars[-21:-1]]
    avg_vol = _mean(prior_vol)
    std_vol = _std(prior_vol)
    volume_z = (vol - avg_vol) / std_vol if std_vol else 0.0
    rng = high - low
    body_top = max(open_px, close)
    shadow_ratio = ((high - body_top) / rng) if rng > 0 else 0.0

    stall = volume_z > VOL_Z_TOP and (change < STALL_PCT or yin)
    upper_shadow = shadow_ratio > SHADOW_TOP and avg_vol > 0 and vol >= avg_vol * VOL_SHADOW_MULT
    shadow_combo = shadow_ratio > SHADOW_COMBO

    swings = _swing_highs(bars)
    divergence = False
    if len(swings) >= 2:
        first, second = swings[-2], swings[-1]
        if second["price"] > first["price"] and first["volume"] > 0:
            divergence = second["volume"] < first["volume"] * VOL_DIVERGE
    if swings:
        prev_high = swings[-1]
        if high > prev_high["price"] and prev_high["volume"] > 0:
            if vol < prev_high["volume"] * VOL_DIVERGE:
                divergence = True

    prior_high = max(_bar_hl(b)[0] for b in bars[-21:-1])
    false_break = high > prior_high and close < prior_high and avg_vol > 0 and vol >= avg_vol * VOL_SHADOW_MULT

    rsi_div = False
    rsis = rsi_series(closes)
    rsi_now = rsis[-1]
    if swings and rsi_now is not None:
        prev = swings[-1]
        prev_rsi = rsis[prev["i"]] if prev["i"] < len(rsis) else None
        if prev_rsi is not None and high > prev["price"] and rsi_now < prev_rsi:
            rsi_div = True
        elif len(swings) >= 2:
            a, b = swings[-2], swings[-1]
            ra, rb = rsis[a["i"]], rsis[b["i"]]
            if ra is not None and rb is not None and b["price"] > a["price"] and rb < ra:
                rsi_div = True

    confirmed = (divergence or stall) and shadow_combo
    parts = []
    if divergence:
        parts.append("量价背离")
    if stall:
        parts.append("巨量滞涨")
    if upper_shadow:
        parts.append("长上影巨量")
    if false_break:
        parts.append("假突破")
    if rsi_div:
        parts.append("RSI顶背离")
    if confirmed:
        state = "放量见顶 · " + " / ".join(parts)
    elif parts:
        state = "单项有 · " + " / ".join(parts) + "（综合还不够）"
    else:
        state = "没有见顶信号"

    return {
        "ok": confirmed,
        "divergence": divergence,
        "stall": stall,
        "upper_shadow": upper_shadow,
        "false_break": false_break,
        "rsi_div": rsi_div,
        "volume_z": round(volume_z, 2),
        "shadow_ratio": round(shadow_ratio, 2),
        "change_pct": round(change * 100, 2),
        "state": state,
    }


def snapshot(ohlc: dict, extra: dict | None = None) -> dict:
    closes = list(ohlc.get("closes") or [])
    bars = list(ohlc.get("ohlc_bars") or [])
    price = ohlc.get("price")
    rsi_now, rsi_prev = rsi_pair(closes)
    emas = ema_map(closes)
    box = range_box(bars, closes)
    bb = bollinger(closes)
    crosses = ema_crosses(closes, price)
    golden = any(row["state"] == "本根已金叉" for row in crosses)
    death = any(row["state"] == "本根已死叉" for row in crosses)
    extra = extra or {}
    h4 = extra.get("4h") or (ohlc if ohlc.get("timeframe") == "4h" else None)
    d1 = extra.get("1d") or (ohlc if ohlc.get("timeframe") == "1d" else None)
    day = intraday_oversold(list((h4 or {}).get("closes") or []))
    week = week_oversold(list((d1 or {}).get("closes") or []))
    align = ema_align(emas)
    levels = volume_levels(bars, price)
    recent = recent_levels(bars, price)
    signals = level_signals(bars)
    climax = volume_climax_top(bars, closes)
    exhaustion = selling_exhaustion(bars, closes)
    from app.services.ta_conditions import compute as compute_ta

    ta = compute_ta(ohlc, extra)
    flags = {
        "golden": golden,
        "death": death,
        "align_bull": align["align"] == "多头",
        "align_bear": align["align"] == "空头",
        "align_tangle": align["align"] == "纠缠",
        "bb_up": bool(bb["up"]),
        "bb_down": bool(bb["down"]),
        "sideways": bool(box["sideways"]),
        "intraday_oversold": bool(day.get("oversold")),
        "week_oversold": bool(week.get("oversold")),
        "climax_top": bool(climax.get("ok")),
        "exhaustion": bool(exhaustion.get("ok")),
        "break_resistance": bool(signals.get("break_resistance")),
        "hold_support": bool(signals.get("hold_support")),
        **(ta.get("flags") or {}),
    }
    return {
        **emas,
        **(ta.get("values") or {}),
        "rsi": None if rsi_now is None else round(rsi_now, 1),
        "rsi_prev": None if rsi_prev is None else round(rsi_prev, 1),
        "crosses": crosses,
        "golden": golden,
        "death": death,
        "range": box,
        "bollinger": bb,
        "bb_up": bb["up"],
        "bb_down": bb["down"],
        "sideways": box["sideways"],
        "range_low": box["low"],
        "range_high": box["high"],
        "intraday_oversold": day,
        "week_oversold": week,
        "align": align["align"],
        "align_label": align["label"],
        "align_detail": align["detail"],
        "resistance": levels["resistance"],
        "support": levels["support"],
        "total_volume": levels["total_volume"],
        "recent_resistance": recent["recent_resistance"],
        "recent_support": recent["recent_support"],
        "climax_top": climax,
        "exhaustion": exhaustion,
        "break_level": signals.get("level_res"),
        "hold_level": signals.get("level_sup"),
        "flags": flags,
    }
