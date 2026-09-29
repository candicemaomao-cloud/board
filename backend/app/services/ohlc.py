from __future__ import annotations

from datetime import date, datetime, timezone
from time import time
from urllib.parse import quote

import httpx

from app.services.quotes import HEADERS, guess_source
from app.services.tradingview import fetch_tv_ohlc, tv_cache_ttl

YAHOO_TF = {
    "5m": ("5m", "60d"),
    "30m": ("30m", "60d"),
    "1h": ("60m", "730d"),
    "4h": ("60m", "1y"),
    "1d": ("1d", "2y"),
    "1w": ("1wk", "5y"),
}
YAHOO_ALIAS = {
    "USDJPY": "USDJPY=X",
    "DXY": "DX-Y.NYB",
    "VIX": "^VIX",
    "SPX": "^GSPC",
    "GSPC": "^GSPC",
    "NDX": "^NDX",
    "ES": "ES=F",
    "NQ": "NQ=F",
}
BINANCE_TF = {
    "5m": ("5m", 1000),
    "30m": ("30m", 1000),
    "1h": ("1h", 1000),
    "4h": ("4h", 500),
    "1d": ("1d", 500),
    "1w": ("1w", 200),
}


class OhlcError(Exception):
    pass


def _pack(symbol: str, source: str, timeframe: str, bars: list[dict], price: float | None) -> dict:
    if price is not None and bars:
        last = dict(bars[-1])
        last["close"] = float(price)
        last["buy"] = True
        high = last.get("high")
        low = last.get("low")
        if high is None or float(price) > float(high):
            last["high"] = float(price)
        if low is None or float(price) < float(low):
            last["low"] = float(price)
        bars = bars[:-1] + [last]
    closes = [bar["close"] for bar in bars]
    live = float(price) if price is not None else (closes[-1] if closes else None)
    return {
        "symbol": symbol,
        "source": source,
        "timeframe": timeframe,
        "price": live,
        "closes": closes,
        "ohlc_bars": bars,
        "bars": len(closes),
    }


def _parse_yahoo_chart(payload: dict) -> tuple[list[dict], float | None]:
    result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
    meta = result.get("meta") or {}
    ts = result.get("timestamp") or []
    quote_row = (result.get("indicators") or {}).get("quote", [{}])[0] or {}
    opens = quote_row.get("open") or []
    highs = quote_row.get("high") or []
    lows = quote_row.get("low") or []
    closes = quote_row.get("close") or []
    volumes = quote_row.get("volume") or []
    bars = []
    for i, t in enumerate(ts):
        if t is None or i >= len(closes) or closes[i] is None:
            continue
        close = float(closes[i])
        open_px = opens[i] if i < len(opens) and opens[i] is not None else close
        high = highs[i] if i < len(highs) and highs[i] is not None else close
        low = lows[i] if i < len(lows) and lows[i] is not None else close
        vol = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
        bars.append({
            "ts": int(t),
            "open": float(open_px),
            "high": float(high),
            "low": float(low),
            "close": close,
            "volume": float(vol),
        })
    live = meta.get("regularMarketPrice")
    return bars, (float(live) if live is not None else None)


def _yahoo_bars(symbol: str, interval: str, range_: str) -> tuple[list[dict], float | None]:
    from time import sleep

    last = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = (
            f"https://{host}/v8/finance/chart/{quote(symbol, safe='')}"
            f"?interval={interval}&range={range_}"
        )
        for attempt in range(3):
            try:
                with httpx.Client(timeout=12.0, headers=HEADERS, follow_redirects=True) as client:
                    res = client.get(url)
                    if res.status_code == 429:
                        sleep(1.2 * (attempt + 1))
                        last = Exception("Yahoo 429")
                        continue
                    res.raise_for_status()
                    payload = res.json()
            except Exception as exc:
                last = exc
                sleep(0.4 * (attempt + 1))
                continue
            return _parse_yahoo_chart(payload)
    raise last or OhlcError(f"Yahoo 拉不到 {symbol}")


def _resample_4h(bars: list[dict]) -> list[dict]:
    buckets: dict[int, dict] = {}
    order: list[int] = []
    for bar in bars:
        dt = datetime.fromtimestamp(bar["ts"], tz=timezone.utc)
        hour = (dt.hour // 4) * 4
        key = int(datetime(dt.year, dt.month, dt.day, hour, tzinfo=timezone.utc).timestamp())
        if key not in buckets:
            order.append(key)
            buckets[key] = {
                "ts": key,
                "open": bar["open"],
                "high": bar.get("high", bar["close"]),
                "low": bar.get("low", bar["close"]),
                "close": bar["close"],
                "volume": bar["volume"],
            }
        else:
            buckets[key]["high"] = max(buckets[key]["high"], bar.get("high", bar["close"]))
            buckets[key]["low"] = min(buckets[key]["low"], bar.get("low", bar["close"]))
            buckets[key]["close"] = bar["close"]
            buckets[key]["volume"] += bar["volume"]
    return [buckets[k] for k in order]


_cache: dict[str, tuple[float, dict]] = {}


def _cached(key: str, builder):
    ttl = tv_cache_ttl()
    hit = _cache.get(key)
    if hit and time() - hit[0] < ttl:
        return hit[1]
    value = builder()
    _cache[key] = (time(), value)
    return value


def fetch_closes(symbol: str, timeframe: str, apply_live: bool = True) -> dict:
    raw = (symbol or "").strip().upper()
    if not raw:
        raise OhlcError("请输入股票代码")
    tf = timeframe if timeframe in YAHOO_TF else "1d"
    source = guess_source(raw)
    cache_key = f"{source}:{raw}:{tf}:{'live' if apply_live else 'raw'}"
    return _cached(cache_key, lambda: _fetch_closes(raw, tf, source, apply_live))


def fetch_daily_history(symbol: str, range_: str = "10y") -> dict:
    """Long daily history for validation; falls back to the normal feed."""
    raw = (symbol or "").strip().upper()
    if not raw:
        raise OhlcError("请输入股票代码")
    if guess_source(raw) == "binance":
        return fetch_closes(raw, "1d", apply_live=False)

    def build():
        try:
            yahoo_sym = YAHOO_ALIAS.get(raw, raw)
            bars, _live = _yahoo_bars(yahoo_sym, "1d", range_)
            if bars:
                return _pack(raw, f"yahoo · {range_}", "1d", bars, None)
        except Exception:
            pass
        return fetch_closes(raw, "1d", apply_live=False)

    return _cached(f"history:{raw}:1d:{range_}", build)


def _bar_day(ts) -> date:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()


#  fetch_closes_covering 对 5m/30m/4h 强制去雅虎直接再拉一遍做合并（保证今天的
#  K 线是全的），这一步没有走上面 fetch_closes() 的缓存，每次调用都是一次真实网络
#  请求。对回测这种一次性调用没问题，但被 current_signal() 这种每隔几十秒轮询一次
#  的实时监听高频调用时，每次都要真打一遍网络（还可能因为 429 重试、sleep 退避），
#  单次就能拖到 15-30s，跟 interval_sec=30s 的轮询节奏对不上，导致"实时"信号一直
#  排队、最后全变成补发。这里单独给 covering 结果加一层短 TTL 缓存：5min K 线本来
#  就是 5 分钟才出一根新的，缓存 60s 完全跟得上，同时把同一 symbol 短时间内的重复
#  轮询、以及同一 symbol 被多个股票监听共用时的重复请求都合并掉。
COVERING_CACHE_TTL = 90.0
_covering_cache: dict[str, tuple[float, dict]] = {}


def fetch_closes_covering(symbol: str, timeframe: str, start: date | None = None) -> dict:
    raw = (symbol or "").strip().upper()
    cache_key = f"{raw}:{timeframe}:{start.isoformat() if start else 'none'}"
    hit = _covering_cache.get(cache_key)
    if hit and time() - hit[0] < COVERING_CACHE_TTL:
        return hit[1]
    value = _fetch_closes_covering_uncached(symbol, timeframe, start)
    _covering_cache[cache_key] = (time(), value)
    return value


def _fetch_closes_covering_uncached(symbol: str, timeframe: str, start: date | None = None) -> dict:
    ohlc = fetch_closes(symbol, timeframe, apply_live=False)
    bars = list(ohlc.get("ohlc_bars") or [])
    raw = (symbol or "").strip().upper()
    if guess_source(raw) == "binance":
        try:
            from app.services.binance import public_kline_bars_covering

            covered = public_kline_bars_covering(raw, BINANCE_TF.get(timeframe, ("5m", 1000))[0], start=start)
            if covered:
                return _pack(ohlc.get("symbol") or raw, "binance", timeframe, covered, None)
        except Exception:
            pass
        return ohlc
    first = _bar_day(bars[0]["ts"]) if bars else None
    need_more = start is not None and (not first or first > start)
    if timeframe in {"5m", "30m", "4h"}:
        need_more = True
    if not need_more:
        return ohlc
    try:
        interval, range_ = YAHOO_TF.get(timeframe, YAHOO_TF["1d"])
        ybars, _live = _yahoo_bars(raw, interval, range_)
        if timeframe == "4h":
            ybars = _resample_4h(ybars)
        if not ybars:
            return ohlc
        merged = {int(row["ts"]): row for row in ybars}
        for row in bars:
            merged[int(row["ts"])] = row
        combined = [merged[key] for key in sorted(merged)]
        if len(combined) > len(bars):
            return _pack(raw, "yahoo" if not bars else ohlc.get("source") or "yahoo", timeframe, combined, None)
    except Exception:
        pass
    return ohlc


def _fetch_closes(raw: str, tf: str, source: str, apply_live: bool = True) -> dict:
    if source == "binance":
        from app.services.binance import public_kline_bars, public_ticker

        interval, limit = BINANCE_TF[tf]
        bars = public_kline_bars(raw, interval, limit)
        price = None
        if apply_live:
            try:
                price = public_ticker(raw).get("price")
            except Exception:
                price = bars[-1]["close"] if bars else None
        if not bars:
            raise OhlcError(f"币安拉不到 {raw} 的 {tf} K 线")
        code = raw if raw.endswith(("USDT", "USDC")) else raw + "USDT"
        return _pack(code, "binance", tf, bars, price)

    try:
        bars = fetch_tv_ohlc(raw, tf)
        live = bars[-1]["close"] if bars else None
        if not bars:
            raise OhlcError(f"TradingView 没有 {raw} 的 {tf} 数据")
        return _pack(raw, "tradingview", tf, bars, live if apply_live else None)
    except Exception as tv_exc:
        try:
            interval, range_ = YAHOO_TF[tf]
            yahoo_sym = YAHOO_ALIAS.get(raw, raw)
            bars, live = _yahoo_bars(yahoo_sym, interval, range_)
            if tf == "4h":
                bars = _resample_4h(bars)
            if bars:
                return _pack(raw, "yahoo", tf, bars, live if apply_live else None)
        except Exception:
            pass
        raise OhlcError(f"TradingView 拉不到 {raw}：{tv_exc}") from tv_exc
