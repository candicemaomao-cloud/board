from __future__ import annotations

from datetime import datetime, timezone

from app.services.indicators import _bar_hl, ema_path, rsi_pair, rsi_series


def _hlc(bar: dict) -> tuple[float, float, float]:
    close = float(bar["close"])
    high, low = _bar_hl(bar)
    return high, low, close


def _vol(bar: dict) -> float:
    return float(bar.get("volume") or 0)


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def _ema_series(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period or period <= 0:
        return out
    ema = sum(values[:period]) / period
    out[period - 1] = ema
    k = 2 / (period + 1)
    for i in range(period, n):
        ema = values[i] * k + ema * (1 - k)
        out[i] = ema
    return out


def _last_two(series: list) -> tuple:
    if not series:
        return None, None
    if len(series) == 1:
        return series[-1], None
    return series[-1], series[-2]


def _cross_up(a0, a1, b0=0, b1=0) -> bool:
    if a0 is None or a1 is None or b0 is None or b1 is None:
        return False
    return a0 <= b0 and a1 > b1


def _cross_down(a0, a1, b0=0, b1=0) -> bool:
    if a0 is None or a1 is None or b0 is None or b1 is None:
        return False
    return a0 >= b0 and a1 < b1


def _rising(now, prev) -> bool:
    return now is not None and prev is not None and now > prev


def _tr_series(bars: list[dict]) -> list[float]:
    out = []
    for i, bar in enumerate(bars):
        high, low, close = _hlc(bar)
        if i == 0:
            out.append(high - low)
            continue
        prev_c = float(bars[i - 1]["close"])
        out.append(max(high - low, abs(high - prev_c), abs(low - prev_c)))
    return out


def _wilder(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    acc = sum(values[:period])
    out[period - 1] = acc / period
    for i in range(period, n):
        acc = acc - acc / period + values[i]
        out[i] = acc / period
    return out


def _atr_series(bars: list[dict], period: int = 14) -> list[float | None]:
    return _wilder(_tr_series(bars), period)


def _macd(closes: list[float]) -> tuple[list, list, list]:
    fast = _ema_series(closes, 12)
    slow = _ema_series(closes, 26)
    line: list[float | None] = [None] * len(closes)
    for i, (a, b) in enumerate(zip(fast, slow)):
        if a is not None and b is not None:
            line[i] = a - b
    compact = [x for x in line if x is not None]
    sig_compact = _ema_series(compact, 9)
    signal: list[float | None] = [None] * len(closes)
    hist: list[float | None] = [None] * len(closes)
    j = 0
    for i, val in enumerate(line):
        if val is None:
            continue
        signal[i] = sig_compact[j]
        if sig_compact[j] is not None:
            hist[i] = val - sig_compact[j]
        j += 1
    return line, signal, hist


def _stoch(bars: list[dict], period: int = 14, smooth: int = 3) -> tuple[list, list]:
    k_raw: list[float | None] = [None] * len(bars)
    for i in range(len(bars)):
        if i + 1 < period:
            continue
        window = bars[i + 1 - period:i + 1]
        highs = [_hlc(b)[0] for b in window]
        lows = [_hlc(b)[1] for b in window]
        hh, ll = max(highs), min(lows)
        close = _hlc(bars[i])[2]
        k_raw[i] = 0.0 if hh == ll else (close - ll) / (hh - ll) * 100
    k: list[float | None] = [None] * len(bars)
    for i in range(len(bars)):
        chunk = [x for x in k_raw[max(0, i + 1 - smooth):i + 1] if x is not None]
        k[i] = sum(chunk) / len(chunk) if len(chunk) == smooth else None
    d: list[float | None] = [None] * len(bars)
    for i in range(len(bars)):
        chunk = [x for x in k[max(0, i + 1 - smooth):i + 1] if x is not None]
        d[i] = sum(chunk) / len(chunk) if len(chunk) == smooth else None
    return k, d


def _cci(bars: list[dict], period: int = 20) -> list[float | None]:
    tp = [(_hlc(b)[0] + _hlc(b)[1] + _hlc(b)[2]) / 3 for b in bars]
    out: list[float | None] = [None] * len(bars)
    for i in range(period - 1, len(bars)):
        window = tp[i + 1 - period:i + 1]
        avg = sum(window) / period
        md = sum(abs(x - avg) for x in window) / period
        out[i] = 0.0 if md == 0 else (tp[i] - avg) / (0.015 * md)
    return out


def _roc(closes: list[float], period: int = 12) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    for i in range(period, len(closes)):
        prev = closes[i - period]
        out[i] = None if not prev else (closes[i] / prev - 1) * 100
    return out


def _obv(bars: list[dict]) -> list[float]:
    out = [0.0]
    for i in range(1, len(bars)):
        prev_c = float(bars[i - 1]["close"])
        close = float(bars[i]["close"])
        vol = _vol(bars[i])
        if close > prev_c:
            out.append(out[-1] + vol)
        elif close < prev_c:
            out.append(out[-1] - vol)
        else:
            out.append(out[-1])
    return out


def _ad_line(bars: list[dict]) -> list[float]:
    acc = 0.0
    out = []
    for bar in bars:
        high, low, close = _hlc(bar)
        vol = _vol(bar)
        rng = high - low
        mfm = 0.0 if rng == 0 else ((close - low) - (high - close)) / rng
        acc += mfm * vol
        out.append(acc)
    return out


def _cmf(bars: list[dict], period: int = 20) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    mfv = []
    vols = []
    for bar in bars:
        high, low, close = _hlc(bar)
        vol = _vol(bar)
        rng = high - low
        mfm = 0.0 if rng == 0 else ((close - low) - (high - close)) / rng
        mfv.append(mfm * vol)
        vols.append(vol)
    for i in range(period - 1, len(bars)):
        den = sum(vols[i + 1 - period:i + 1])
        out[i] = 0.0 if den == 0 else sum(mfv[i + 1 - period:i + 1]) / den
    return out


def _mfi(bars: list[dict], period: int = 14) -> list[float | None]:
    tp = [(_hlc(b)[0] + _hlc(b)[1] + _hlc(b)[2]) / 3 for b in bars]
    pos = [0.0]
    neg = [0.0]
    for i in range(1, len(bars)):
        flow = tp[i] * _vol(bars[i])
        if tp[i] > tp[i - 1]:
            pos.append(flow)
            neg.append(0.0)
        elif tp[i] < tp[i - 1]:
            pos.append(0.0)
            neg.append(flow)
        else:
            pos.append(0.0)
            neg.append(0.0)
    out: list[float | None] = [None] * len(bars)
    for i in range(period, len(bars)):
        p = sum(pos[i + 1 - period:i + 1])
        n = sum(neg[i + 1 - period:i + 1])
        out[i] = 100.0 if n == 0 else 100 - 100 / (1 + p / n)
    return out


def _adx(bars: list[dict], period: int = 14) -> tuple[list, list, list]:
    plus_dm = [0.0]
    minus_dm = [0.0]
    for i in range(1, len(bars)):
        high, low, _c = _hlc(bars[i])
        ph, pl, _pc = _hlc(bars[i - 1])
        up = high - ph
        down = pl - low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    atr = _atr_series(bars, period)
    sm_plus = _wilder(plus_dm, period)
    sm_minus = _wilder(minus_dm, period)
    plus_di: list[float | None] = [None] * len(bars)
    minus_di: list[float | None] = [None] * len(bars)
    dx: list[float] = []
    dx_idx: list[int] = []
    for i in range(len(bars)):
        if atr[i] in (None, 0) or sm_plus[i] is None or sm_minus[i] is None:
            continue
        plus_di[i] = 100 * sm_plus[i] / atr[i]
        minus_di[i] = 100 * sm_minus[i] / atr[i]
        denom = plus_di[i] + minus_di[i]
        val = 0.0 if denom == 0 else abs(plus_di[i] - minus_di[i]) / denom * 100
        dx.append(val)
        dx_idx.append(i)
    adx: list[float | None] = [None] * len(bars)
    if len(dx) >= period:
        first = sum(dx[:period]) / period
        adx[dx_idx[period - 1]] = first
        acc = first
        for j in range(period, len(dx)):
            acc = (acc * (period - 1) + dx[j]) / period
            adx[dx_idx[j]] = acc
    return adx, plus_di, minus_di


def _sar_series(bars: list[dict], step: float = 0.02, maximum: float = 0.2) -> list[float]:
    if len(bars) < 2:
        return [_hlc(b)[2] for b in bars]
    high0, low0, _c0 = _hlc(bars[0])
    high1, low1, _c1 = _hlc(bars[1])
    up = high1 >= high0
    ep = high1 if up else low1
    sar = low0 if up else high0
    af = step
    out = [sar, sar]
    for i in range(2, len(bars)):
        high, low, _c = _hlc(bars[i])
        prev = sar
        sar = prev + af * (ep - prev)
        if up:
            sar = min(sar, _hlc(bars[i - 1])[1], _hlc(bars[i - 2])[1])
            if low < sar:
                up = False
                sar = ep
                ep = low
                af = step
            else:
                if high > ep:
                    ep = high
                    af = min(maximum, af + step)
        else:
            sar = max(sar, _hlc(bars[i - 1])[0], _hlc(bars[i - 2])[0])
            if high > sar:
                up = True
                sar = ep
                ep = high
                af = step
            else:
                if low < ep:
                    ep = low
                    af = min(maximum, af + step)
        out.append(sar)
    return out


def _donchian(bars: list[dict], period: int) -> tuple[float | None, float | None]:
    if len(bars) < period + 1:
        return None, None
    window = bars[-(period + 1):-1]
    return max(_hlc(b)[0] for b in window), min(_hlc(b)[1] for b in window)


def _highest(bars: list[dict], period: int, include_last: bool = True) -> float | None:
    if len(bars) < period:
        return None
    chunk = bars[-period:] if include_last else bars[-(period + 1):-1]
    if not chunk:
        return None
    return max(_hlc(b)[0] for b in chunk)


def _lowest(bars: list[dict], period: int, include_last: bool = True) -> float | None:
    if len(bars) < period:
        return None
    chunk = bars[-period:] if include_last else bars[-(period + 1):-1]
    if not chunk:
        return None
    return min(_hlc(b)[1] for b in chunk)


def _calendar_low(bars: list[dict], kind: str) -> float | None:
    if not bars:
        return None
    ts = bars[-1].get("ts")
    if not ts:
        n = 5 if kind == "week" else 21
        return _lowest(bars, min(n, len(bars)), include_last=True)
    last = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    lows = []
    for bar in reversed(bars):
        stamp = bar.get("ts")
        if not stamp:
            continue
        dt = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
        if kind == "week":
            if dt.isocalendar()[:2] != last.isocalendar()[:2]:
                break
        elif (dt.year, dt.month) != (last.year, last.month):
            break
        lows.append(_hlc(bar)[1])
    return min(lows) if lows else None


def _ichimoku(bars: list[dict]) -> dict:
    def mid(period, idx):
        if idx < 0 or idx + 1 < period:
            return None
        window = bars[idx + 1 - period:idx + 1]
        return (max(_hlc(b)[0] for b in window) + min(_hlc(b)[1] for b in window)) / 2

    i = len(bars) - 1
    tenkan = mid(9, i)
    kijun = mid(26, i)
    tenkan_p = mid(9, i - 1) if i else None
    kijun_p = mid(26, i - 1) if i else None
    cloud_i = i - 26
    senkou_a = senkou_b = None
    if cloud_i >= 51:
        t = mid(9, cloud_i)
        k = mid(26, cloud_i)
        senkou_a = None if t is None or k is None else (t + k) / 2
        senkou_b = mid(52, cloud_i)
    close = _hlc(bars[i])[2]
    cloud_top = cloud_bot = None
    if senkou_a is not None and senkou_b is not None:
        cloud_top = max(senkou_a, senkou_b)
        cloud_bot = min(senkou_a, senkou_b)
    return {
        "above": cloud_top is not None and close > cloud_top,
        "below": cloud_bot is not None and close < cloud_bot,
        "tk_golden": _cross_up(tenkan_p, tenkan, kijun_p, kijun),
        "tk_death": _cross_down(tenkan_p, tenkan, kijun_p, kijun),
        "tenkan": None if tenkan is None else round(tenkan, 4),
        "kijun": None if kijun is None else round(kijun, 4),
        "cloud_top": None if cloud_top is None else round(cloud_top, 4),
        "cloud_bot": None if cloud_bot is None else round(cloud_bot, 4),
    }


def _session_vwap(bars: list[dict], timeframe: str) -> tuple[float | None, float | None]:
    if not bars:
        return None, None
    if timeframe in {"5m", "30m", "4h"}:
        last_day = datetime.fromtimestamp(int(bars[-1].get("ts") or 0), tz=timezone.utc).date()
        session = [
            b for b in bars
            if datetime.fromtimestamp(int(b.get("ts") or 0), tz=timezone.utc).date() == last_day
        ]
        if not session:
            session = bars[-20:]
    else:
        session = bars[-20:]
    num = den = 0.0
    prev_v = None
    for bar in session:
        high, low, close = _hlc(bar)
        tp = (high + low + close) / 3
        vol = _vol(bar)
        if den > 0:
            prev_v = num / den
        num += tp * vol
        den += vol
    now = None if den == 0 else num / den
    return now, prev_v


def _div_bull(price: list[float], osc: list, lookback: int = 30) -> bool:
    if len(price) < lookback or len(osc) < lookback:
        return False
    p = price[-lookback:]
    o = osc[-lookback:]
    usable = [x for x in o if x is not None]
    if len(usable) < 5 or o[-1] is None:
        return False
    return p[-1] <= min(p) * 1.003 and o[-1] > min(usable)


def _div_bear(price: list[float], osc: list, lookback: int = 30) -> bool:
    if len(price) < lookback or len(osc) < lookback:
        return False
    p = price[-lookback:]
    o = osc[-lookback:]
    usable = [x for x in o if x is not None]
    if len(usable) < 5 or o[-1] is None:
        return False
    return p[-1] >= max(p) * 0.997 and o[-1] < max(usable)


def _ret(closes: list[float], period: int) -> float | None:
    if len(closes) <= period or not closes[-period - 1]:
        return None
    return closes[-1] / closes[-period - 1] - 1


def _rs_win(stock: list[float], bench: list[float] | None, period: int) -> bool | None:
    if not bench:
        return None
    a, b = _ret(stock, period), _ret(bench, period)
    if a is None or b is None:
        return None
    return a > b


def compute(ohlc: dict, extra: dict | None = None) -> dict:
    extra = extra or {}
    bars = list(ohlc.get("ohlc_bars") or [])
    closes = list(ohlc.get("closes") or [])
    n = len(bars)
    if n < 5 or not closes:
        return {"values": {}, "flags": {}}

    close = float(closes[-1])
    close_p = float(closes[-2]) if n > 1 else None
    tf = ohlc.get("timeframe") or "1d"
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    ema20_p, ema20 = ema_path(closes, 20)
    _e50p, ema50 = ema_path(closes, 50)
    rsi, rsi_p = rsi_pair(closes)
    rsis = rsi_series(closes)
    macd, signal, hist = _macd(closes)
    macd_n, macd_p = _last_two(macd)
    sig_n, sig_p = _last_two(signal)
    hist_n, hist_p = _last_two(hist)
    adx_s, pdi_s, mdi_s = _adx(bars)
    adx_n, adx_p = _last_two(adx_s)
    pdi_n, _p = _last_two(pdi_s)
    mdi_n, _m = _last_two(mdi_s)
    atr_s = _atr_series(bars)
    atr_n, atr_p = _last_two(atr_s)
    atr_ma = _sma([x for x in atr_s[-21:-1] if x is not None], 20) if n > 21 else None
    k_s, d_s = _stoch(bars)
    k_n, k_p = _last_two(k_s)
    d_n, d_p = _last_two(d_s)
    j_n = None if k_n is None or d_n is None else 3 * k_n - 2 * d_n
    cci_s = _cci(bars)
    cci_n, cci_p = _last_two(cci_s)
    roc_s = _roc(closes)
    roc_n, roc_p = _last_two(roc_s)
    obv_s = _obv(bars)
    obv_n, obv_p = _last_two(obv_s)
    ad_s = _ad_line(bars)
    ad_n, ad_p = _last_two(ad_s)
    cmf_s = _cmf(bars)
    cmf_n, _c = _last_two(cmf_s)
    mfi_s = _mfi(bars)
    mfi_n, mfi_p = _last_two(mfi_s)
    sar_s = _sar_series(bars)
    sar_n, sar_p = _last_two(sar_s)
    ichi = _ichimoku(bars)
    vwap, vwap_p = _session_vwap(bars, tf)
    vols = [_vol(b) for b in bars]
    vol_n = vols[-1]
    vol_ma = _sma(vols[:-1], 20) if n > 20 else _sma(vols, 20)
    vol_p = vols[-2] if n > 1 else None
    don_h20, don_l20 = _donchian(bars, 20)
    hi20 = _highest(bars, 20, include_last=False)
    lo20 = _lowest(bars, 20, include_last=False)
    zone_high20 = _highest(bars, 20, include_last=True)
    zone_low20 = _lowest(bars, 20, include_last=True)
    hi50 = _highest(bars, 50, include_last=False)
    lo50 = _lowest(bars, 50, include_last=False)
    daily = extra.get("1d") or (ohlc if tf == "1d" else None)
    d_bars = list((daily or {}).get("ohlc_bars") or [])
    day_src = d_bars or bars
    day_close = float(day_src[-1]["close"]) if day_src else close
    hi5 = _highest(day_src, 5, include_last=False)
    lo5 = _lowest(day_src, 5, include_last=False)
    low5 = _lowest(day_src, 5, include_last=True)
    low10 = _lowest(day_src, 10, include_last=True)
    low20 = _lowest(day_src, 20, include_last=True)
    high5 = _highest(day_src, 5, include_last=True)
    high10 = _highest(day_src, 10, include_last=True)
    high20 = _highest(day_src, 20, include_last=True)
    week_low = _calendar_low(day_src, "week")
    month_low = _calendar_low(day_src, "month")
    don_h5, don_l5 = _donchian(day_src, 5)
    hi52 = _highest(d_bars, 252, include_last=False) if d_bars else None
    lo52 = _lowest(d_bars, 252, include_last=False) if d_bars else None
    atr_pct = None if not close or atr_n is None else atr_n / close * 100
    kelt_up = kelt_dn = kelt_up_p = kelt_dn_p = None
    if ema20 is not None and atr_n is not None:
        kelt_up = ema20 + 2 * atr_n
        kelt_dn = ema20 - 2 * atr_n
    if ema20_p is not None and atr_p is not None:
        kelt_up_p = ema20_p + 2 * atr_p
        kelt_dn_p = ema20_p - 2 * atr_p
    kelt_bw = None if kelt_up is None or kelt_dn is None or not ema20 else (kelt_up - kelt_dn) / ema20
    kelt_bw_p = None if kelt_up_p is None or kelt_dn_p is None or not ema20_p else (kelt_up_p - kelt_dn_p) / ema20_p
    bb_mid = _sma(closes, 20)
    bb_std = None
    if len(closes) >= 20:
        chunk = closes[-20:]
        avg = sum(chunk) / 20
        bb_std = (sum((x - avg) ** 2 for x in chunk) / 19) ** 0.5
    bb_up = None if bb_mid is None or bb_std is None else bb_mid + 2 * bb_std
    bb_dn = None if bb_mid is None or bb_std is None else bb_mid - 2 * bb_std
    bb_bw = None if bb_mid in (None, 0) or bb_up is None or bb_dn is None else (bb_up - bb_dn) / bb_mid
    bb_mid_p = _sma(closes[:-1], 20) if len(closes) > 20 else None
    bb_bw_p = None
    if len(closes) > 20 and bb_mid_p not in (None, 0):
        chunk = closes[-21:-1]
        avg = sum(chunk) / 20
        sd = (sum((x - avg) ** 2 for x in chunk) / 19) ** 0.5
        bb_bw_p = 4 * sd / bb_mid_p
    bw_hist = []
    if len(closes) >= 60:
        for i in range(len(closes) - 60, len(closes)):
            if i < 19:
                continue
            w = closes[i - 19:i + 1]
            m = sum(w) / 20
            sd = (sum((x - m) ** 2 for x in w) / 19) ** 0.5
            if m:
                bw_hist.append(4 * sd / m)
    bb_squeeze = bool(bb_bw is not None and bw_hist and bb_bw <= sorted(bw_hist)[max(0, len(bw_hist) // 5)])
    spy = list((extra.get("spy") or {}).get("closes") or [])
    qqq = list((extra.get("qqq") or {}).get("closes") or [])
    rs_spy_20 = _rs_win(closes, spy, 20)
    rs_spy_60 = _rs_win(closes, spy, 60)
    rs_spy_120 = _rs_win(closes, spy, 120)
    rs_qqq_20 = _rs_win(closes, qqq, 20)
    rs_qqq_60 = _rs_win(closes, qqq, 60)
    obv_hi = max(obv_s[-20:]) if n >= 20 else None
    obv_lo = min(obv_s[-20:]) if n >= 20 else None
    px_hi = max(closes[-20:]) if n >= 20 else None
    px_lo = min(closes[-20:]) if n >= 20 else None

    flags = {
        "sma20_above": sma20 is not None and close > sma20,
        "sma50_above": sma50 is not None and close > sma50,
        "sma200_above": sma200 is not None and close > sma200,
        "sma20_gt_sma50": sma20 is not None and sma50 is not None and sma20 > sma50,
        "sma50_gt_sma200": sma50 is not None and sma200 is not None and sma50 > sma200,
        "ema20_above": ema20 is not None and close > ema20,
        "ema50_above": ema50 is not None and close > ema50,
        "ema20_up": _rising(ema20, ema20_p),
        "macd_golden": _cross_up(macd_p, macd_n, sig_p, sig_n),
        "macd_death": _cross_down(macd_p, macd_n, sig_p, sig_n),
        "macd_pos": macd_n is not None and macd_n > 0,
        "macd_neg": macd_n is not None and macd_n < 0,
        "macd_hist_up": _rising(hist_n, hist_p),
        "macd_hist_down": hist_n is not None and hist_p is not None and hist_n < hist_p,
        "macd_bull_div": _div_bull(closes, macd),
        "macd_bear_div": _div_bear(closes, macd),
        "adx_lt15": adx_n is not None and adx_n < 15,
        "adx_15_20": adx_n is not None and 15 <= adx_n < 20,
        "adx_gt20": adx_n is not None and adx_n > 20,
        "adx_gt25": adx_n is not None and adx_n > 25,
        "adx_gt40": adx_n is not None and adx_n > 40,
        "adx_up": _rising(adx_n, adx_p),
        "plus_di_lead": pdi_n is not None and mdi_n is not None and pdi_n > mdi_n,
        "minus_di_lead": pdi_n is not None and mdi_n is not None and mdi_n > pdi_n,
        "ichimoku_above_cloud": bool(ichi["above"]),
        "ichimoku_below_cloud": bool(ichi["below"]),
        "ichimoku_tk_golden": bool(ichi["tk_golden"]),
        "ichimoku_tk_death": bool(ichi["tk_death"]),
        "sar_up": sar_n is not None and close > sar_n,
        "sar_down": sar_n is not None and close < sar_n,
        "sar_flip_up": sar_p is not None and close_p is not None and close_p <= sar_p and close > sar_n,
        "sar_flip_down": sar_p is not None and close_p is not None and close_p >= sar_p and close < sar_n,
        "rsi_gt70": rsi is not None and rsi > 70,
        "rsi_gt80": rsi is not None and rsi > 80,
        "rsi_lt30": rsi is not None and rsi < 30,
        "rsi_lt20": rsi is not None and rsi < 20,
        "rsi_gt50": rsi is not None and rsi > 50,
        "rsi_lt50": rsi is not None and rsi < 50,
        "rsi_cross_up_30": _cross_up(rsi_p, rsi, 30, 30),
        "rsi_cross_down_70": _cross_down(rsi_p, rsi, 70, 70),
        "rsi_cross_up_50": _cross_up(rsi_p, rsi, 50, 50),
        "rsi_cross_down_50": _cross_down(rsi_p, rsi, 50, 50),
        "rsi_bull_div": _div_bull(closes, rsis),
        "rsi_bear_div": _div_bear(closes, rsis),
        "stoch_oversold": k_n is not None and k_n < 20,
        "stoch_overbought": k_n is not None and k_n > 80,
        "stoch_golden": _cross_up(k_p, k_n, d_p, d_n) and k_n is not None and k_n < 30,
        "stoch_death": _cross_down(k_p, k_n, d_p, d_n) and k_n is not None and k_n > 70,
        "kdj_golden": _cross_up(k_p, k_n, d_p, d_n),
        "kdj_death": _cross_down(k_p, k_n, d_p, d_n),
        "kdj_above": k_n is not None and d_n is not None and k_n > d_n,
        "kdj_below": k_n is not None and d_n is not None and k_n < d_n,
        "kdj_overbought": j_n is not None and j_n > 100,
        "kdj_oversold": j_n is not None and j_n < 0,
        "cci_gt100": cci_n is not None and cci_n > 100,
        "cci_lt_m100": cci_n is not None and cci_n < -100,
        "cci_cross_up_100": _cross_up(cci_p, cci_n, 100, 100),
        "cci_cross_down_m100": _cross_down(cci_p, cci_n, -100, -100),
        "roc_pos": roc_n is not None and roc_n > 0,
        "roc_neg": roc_n is not None and roc_n < 0,
        "roc_up": _rising(roc_n, roc_p),
        "bb_squeeze": bb_squeeze,
        "bb_expand": bb_bw is not None and bb_bw_p is not None and bb_bw > bb_bw_p,
        "bb_above_upper": bb_up is not None and close > bb_up,
        "bb_below_lower": bb_dn is not None and close < bb_dn,
        "atr_up": _rising(atr_n, atr_p),
        "atr_down": atr_n is not None and atr_p is not None and atr_n < atr_p,
        "atr_spike": atr_n is not None and atr_ma is not None and atr_n > 1.5 * atr_ma,
        "atr_drop": atr_n is not None and atr_ma is not None and atr_n < 0.7 * atr_ma,
        "atr_pct_high": atr_pct is not None and atr_pct > 3,
        "keltner_above": kelt_up is not None and close > kelt_up,
        "keltner_below": kelt_dn is not None and close < kelt_dn,
        "keltner_squeeze": kelt_bw is not None and kelt_bw_p is not None and kelt_bw < kelt_bw_p,
        "keltner_break_up": kelt_up_p is not None and close_p is not None and close_p <= kelt_up_p and kelt_up is not None and close > kelt_up,
        "keltner_break_down": kelt_dn_p is not None and close_p is not None and close_p >= kelt_dn_p and kelt_dn is not None and close < kelt_dn,
        "vol_up": vol_p is not None and vol_n > vol_p,
        "vol_down": vol_p is not None and vol_n < vol_p,
        "vol_high": vol_ma is not None and vol_n > 1.5 * vol_ma,
        "vol_low": vol_ma is not None and vol_n < 0.6 * vol_ma,
        "obv_up": _rising(obv_n, obv_p),
        "obv_down": obv_n is not None and obv_p is not None and obv_n < obv_p,
        "obv_high": obv_hi is not None and obv_n == obv_hi,
        "obv_low": obv_lo is not None and obv_n == obv_lo,
        "price_high_obv_flat": px_hi is not None and obv_hi is not None and close >= px_hi * 0.999 and obv_n < obv_hi,
        "price_low_obv_flat": px_lo is not None and obv_lo is not None and close <= px_lo * 1.001 and obv_n > obv_lo,
        "mfi_gt80": mfi_n is not None and mfi_n > 80,
        "mfi_lt20": mfi_n is not None and mfi_n < 20,
        "mfi_up": _rising(mfi_n, mfi_p),
        "mfi_down": mfi_n is not None and mfi_p is not None and mfi_n < mfi_p,
        "mfi_cross_down_80": _cross_down(mfi_p, mfi_n, 80, 80),
        "mfi_cross_up_20": _cross_up(mfi_p, mfi_n, 20, 20),
        "cmf_pos": cmf_n is not None and cmf_n > 0,
        "cmf_neg": cmf_n is not None and cmf_n < 0,
        "ad_up": _rising(ad_n, ad_p),
        "ad_down": ad_n is not None and ad_p is not None and ad_n < ad_p,
        "vwap_above": vwap is not None and close > vwap,
        "vwap_below": vwap is not None and close < vwap,
        "vwap_cross_up": vwap_p is not None and close_p is not None and close_p <= vwap_p and vwap is not None and close > vwap,
        "vwap_cross_down": vwap_p is not None and close_p is not None and close_p >= vwap_p and vwap is not None and close < vwap,
        "vwap_up": _rising(vwap, vwap_p),
        "vwap_down": vwap is not None and vwap_p is not None and vwap < vwap_p,
        "high_5": hi5 is not None and day_close > hi5,
        "low_5": lo5 is not None and day_close < lo5,
        "donchian5_break_up": don_h5 is not None and day_close > don_h5,
        "donchian5_break_down": don_l5 is not None and day_close < don_l5,
        "high_20": hi20 is not None and close > hi20,
        "high_zone_20": zone_high20 is not None and zone_high20 > 0 and close >= zone_high20 * 0.97,
        "low_20": lo20 is not None and close < lo20,
        "low_zone_20": zone_low20 is not None and zone_low20 > 0 and close <= zone_low20 * 1.03,
        "high_50": hi50 is not None and close > hi50,
        "low_50": lo50 is not None and close < lo50,
        "high_52w": hi52 is not None and close > hi52,
        "low_52w": lo52 is not None and close < lo52,
        "donchian_break_up": don_h20 is not None and close > don_h20,
        "donchian_break_down": don_l20 is not None and close < don_l20,
        "rs_spy_20_win": bool(rs_spy_20),
        "rs_spy_20_lose": rs_spy_20 is False,
        "rs_qqq_20_win": bool(rs_qqq_20),
        "rs_qqq_20_lose": rs_qqq_20 is False,
        "rs_spy_60_win": bool(rs_spy_60),
        "rs_qqq_60_win": bool(rs_qqq_60),
        "rs_spy_120_win": bool(rs_spy_120),
    }
    values = {
        "sma20": None if sma20 is None else round(sma20, 4),
        "sma50": None if sma50 is None else round(sma50, 4),
        "sma200": None if sma200 is None else round(sma200, 4),
        "ema50": None if ema50 is None else round(ema50, 4),
        "macd": None if macd_n is None else round(macd_n, 4),
        "macd_signal": None if sig_n is None else round(sig_n, 4),
        "macd_hist": None if hist_n is None else round(hist_n, 4),
        "adx": None if adx_n is None else round(adx_n, 2),
        "plus_di": None if pdi_n is None else round(pdi_n, 2),
        "minus_di": None if mdi_n is None else round(mdi_n, 2),
        "atr": None if atr_n is None else round(atr_n, 4),
        "atr_pct": None if atr_pct is None else round(atr_pct, 2),
        "stoch_k": None if k_n is None else round(k_n, 1),
        "stoch_d": None if d_n is None else round(d_n, 1),
        "stoch_k_prev": None if k_p is None else round(k_p, 1),
        "stoch_d_prev": None if d_p is None else round(d_p, 1),
        "kdj_j": None if j_n is None else round(j_n, 1),
        "cci": None if cci_n is None else round(cci_n, 1),
        "roc": None if roc_n is None else round(roc_n, 2),
        "mfi": None if mfi_n is None else round(mfi_n, 1),
        "cmf": None if cmf_n is None else round(cmf_n, 3),
        "vwap": None if vwap is None else round(vwap, 4),
        "sar": None if sar_n is None else round(sar_n, 4),
        "keltner_upper": None if kelt_up is None else round(kelt_up, 4),
        "keltner_lower": None if kelt_dn is None else round(kelt_dn, 4),
        "donchian5_high": None if don_h5 is None else round(don_h5, 4),
        "donchian5_low": None if don_l5 is None else round(don_l5, 4),
        "donchian_high": None if don_h20 is None else round(don_h20, 4),
        "donchian_low": None if don_l20 is None else round(don_l20, 4),
        "hi5": None if hi5 is None else round(hi5, 4),
        "lo5": None if lo5 is None else round(lo5, 4),
        "low5": None if low5 is None else round(low5, 4),
        "low10": None if low10 is None else round(low10, 4),
        "low20": None if low20 is None else round(low20, 4),
        "high5": None if high5 is None else round(high5, 4),
        "high10": None if high10 is None else round(high10, 4),
        "high20": None if high20 is None else round(high20, 4),
        "week_low": None if week_low is None else round(week_low, 4),
        "month_low": None if month_low is None else round(month_low, 4),
        "hi20": None if hi20 is None else round(hi20, 4),
        "lo20": None if lo20 is None else round(lo20, 4),
        "zone_high20": None if zone_high20 is None else round(zone_high20, 4),
        "zone_low20": None if zone_low20 is None else round(zone_low20, 4),
        "hi50": None if hi50 is None else round(hi50, 4),
        "lo50": None if lo50 is None else round(lo50, 4),
        "hi52": None if hi52 is None else round(hi52, 4),
        "lo52": None if lo52 is None else round(lo52, 4),
        "tenkan": ichi.get("tenkan"),
        "kijun": ichi.get("kijun"),
        "cloud_top": ichi.get("cloud_top"),
        "cloud_bot": ichi.get("cloud_bot"),
        "bb_bandwidth": None if bb_bw is None else round(bb_bw * 100, 2),
    }
    return {"values": values, "flags": flags}
