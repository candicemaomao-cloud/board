"""手写策略用的拉数 / 洗数。指标和进出场在策略文件里自己写。"""

from __future__ import annotations


def wash_bars(raw) -> list[dict]:
    """丢掉缺 OHLC、非正价格、时间倒退的 K 线。"""
    rows = raw.get("ohlc_bars") if isinstance(raw, dict) else raw
    out = []
    prev_ts = None
    for bar in rows or []:
        try:
            ts = int(bar["ts"])
            open_px = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if min(open_px, high, low, close) <= 0 or high < low:
            continue
        if prev_ts is not None and ts <= prev_ts:
            continue
        prev_ts = ts
        try:
            volume = float(bar.get("volume") or 0)
        except (TypeError, ValueError):
            volume = 0.0
        out.append(
            {
                "ts": ts,
                "open": open_px,
                "high": high,
                "low": low,
                "close": close,
                "volume": max(volume, 0.0),
            }
        )
    return out


def closes(bars: list[dict]) -> list[float]:
    return [float(bar["close"]) for bar in bars]


def log_returns(prices: list[float]) -> list[float]:
    import math

    out = []
    for i, px in enumerate(prices):
        if i == 0:
            out.append(0.0)
            continue
        prev = max(prices[i - 1], 1e-12)
        out.append(math.log(max(px, 1e-12) / prev))
    return out
