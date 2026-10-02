from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from time import time
from urllib.parse import quote

import httpx

from app.services.tradingview import fetch_tv_quotes, tv_cache_ttl, us_equity_session

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}
TAPE_TTL = 60
YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
YAHOO_SYMBOL_ALIASES = {
    "USDJPY": "JPY=X",
}

TAPE = [
    ("SPY", "标普"),
    ("QQQ", "纳指"),
    ("DIA", "道指"),
    ("IWM", "小盘"),
    ("^VIX", "VIX"),
    ("DX-Y.NYB", "美元"),
    ("^TNX", "美债10年"),
    ("GC=F", "黄金"),
    ("CL=F", "原油"),
    ("USDJPY", "美元/日元"),
]

_cache: dict[str, tuple[float, object]] = {}


def _has_prices(value) -> bool:
    rows = []
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = (value.get("indices") or []) + (value.get("watchlist") or [])
    if not rows:
        return True
    return any((row or {}).get("price") is not None for row in rows)


def _cached(key: str, ttl: float, builder):
    hit = _cache.get(key)
    if hit and time() - hit[0] < ttl and _has_prices(hit[1]):
        return hit[1]
    value = builder()
    _cache[key] = (time(), value)
    return value


def _pairs(result: dict) -> list[tuple[int, float]]:
    ts = result.get("timestamp") or []
    closes = (result.get("indicators") or {}).get("quote", [{}])[0].get("close") or []
    out = []
    for t, c in zip(ts, closes):
        if t is not None and c is not None:
            out.append((int(t), float(c)))
    return out


def _week_base(pairs: list[tuple[int, float]]) -> float | None:
    if not pairs:
        return None
    last_dt = datetime.fromtimestamp(pairs[-1][0], tz=timezone.utc)
    monday = last_dt.date() - timedelta(days=last_dt.weekday())
    for ts, close in reversed(pairs[:-1]):
        day = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        if day < monday:
            return close
    return pairs[0][1]


def _ema_last(closes: list[float], period: int) -> float | None:
    value = _ema_core(closes, period)
    return None if value is None else round(value, 4)


def _attach_ema(row: dict, closes: list[float], live_price: float | None = None) -> dict:
    series = list(closes)
    if live_price and series:
        series[-1] = float(live_price)
    elif live_price:
        series = [float(live_price)]
    row["ema5"] = _ema_last(series, 5)
    row["ema10"] = _ema_last(series, 10)
    row["ema20"] = _ema_last(series, 20)
    return row


def _fetch_one(symbol: str, name: str) -> dict:
    payload = None
    last_exc = None
    encoded = quote(symbol, safe="")
    for host in YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{encoded}?interval=1d&range=3mo"
        try:
            with httpx.Client(timeout=12.0, headers=HEADERS, follow_redirects=True) as client:
                res = client.get(url)
                res.raise_for_status()
                payload = res.json()
            break
        except Exception as exc:
            last_exc = exc
    if payload is None:
        raise last_exc or RuntimeError(f"Yahoo 无数据 {symbol}")
    err = (payload.get("chart") or {}).get("error")
    if err:
        raise RuntimeError(err.get("description") or str(err))
    result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
    meta = result.get("meta") or {}
    pairs = _pairs(result)
    raw_px = meta.get("regularMarketPrice")
    if raw_px is None and pairs:
        raw_px = pairs[-1][1]
    if raw_px is None:
        raise RuntimeError(f"Yahoo 无最新价 {symbol}")
    price = float(raw_px)
    prev = pairs[-2][1] if len(pairs) >= 2 else price
    week_base = _week_base(pairs) or prev
    day_chg = ((price - prev) / prev * 100) if prev else 0.0
    week_chg = ((price - week_base) / week_base * 100) if week_base else 0.0
    kind = "yield" if symbol == "^TNX" else "index"
    row = {
        "symbol": symbol,
        "name": name,
        "price": round(price, 4 if kind == "yield" else 2),
        "day_pct": round(day_chg, 2),
        "week_pct": round(week_chg, 2),
        "kind": kind,
    }
    return _attach_ema(row, [c for _ts, c in pairs], price)


def fetch_quotes(symbols: list[tuple[str, str]]) -> list[dict]:
    try:
        rows = fetch_tv_quotes(symbols)
    except Exception:
        rows = []
    by_req = {row.get("requested") or row.get("symbol"): row for row in rows}

    missing = [(sym, name) for sym, name in symbols if not (by_req.get(sym) or {}).get("price")]
    if missing:
        try:
            retry_rows = fetch_tv_quotes(missing)
        except Exception:
            retry_rows = []
        for row in retry_rows:
            key = row.get("requested") or row.get("symbol")
            if key and row.get("price") is not None:
                by_req[key] = row
        missing = [(sym, name) for sym, name in missing if not (by_req.get(sym) or {}).get("price")]

    if missing:
        def _fallback(sym: str, name: str) -> dict:
            yahoo_symbol = YAHOO_SYMBOL_ALIASES.get(sym, sym)
            row = _fetch_one(yahoo_symbol, name)
            session = us_equity_session()
            row.update({
                "symbol": sym,
                "requested": sym,
                "source": "yahoo",
                "session": session["key"],
                "session_label": session["label"],
            })
            return row

        with ThreadPoolExecutor(max_workers=min(10, len(missing))) as pool:
            jobs = {pool.submit(_fallback, sym, name): (sym, name) for sym, name in missing}
            for future in as_completed(jobs):
                sym, _name = jobs[future]
                try:
                    by_req[sym] = future.result()
                except Exception as exc:
                    previous = by_req.get(sym) or {}
                    if previous:
                        previous["error"] = previous.get("error") or f"Yahoo 回退失败: {exc}"
                        by_req[sym] = previous

    out = []
    for sym, name in symbols:
        row = by_req.get(sym)
        if row and row.get("price") is not None:
            out.append(row)
        else:
            out.append({
                "symbol": sym,
                "name": name,
                "price": None,
                "day_pct": 0,
                "week_pct": 0,
                "kind": "index",
                "source": "tradingview",
                "requested": sym,
                "ema5": None,
                "ema10": None,
                "ema20": None,
                "error": (row or {}).get("error") or "TradingView 与 Yahoo 均无报价",
            })
    return out


def guess_source(symbol: str) -> str:
    from app.services.binance import looks_like_crypto

    raw = (symbol or "").strip().upper()
    if looks_like_crypto(raw):
        return "binance"
    return "tradingview"


def _normalize_source(source: str, symbol: str) -> str:
    raw = (source or "").strip().lower()
    if raw in {"binance"}:
        return "binance"
    if raw in {"yahoo", "tv", "tradingview", ""}:
        return "tradingview"
    return guess_source(symbol)


def _yahoo_symbol(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if raw.endswith("USDT"):
        return raw[:-4]
    if raw.endswith("USDC"):
        return raw[:-4]
    return raw


def _binance_symbol(symbol: str) -> str:
    from app.services.binance import normalize_symbol

    return normalize_symbol(symbol)


def _fetch_binance(symbol: str, name: str) -> dict:
    from app.services.binance import public_daily_closes, public_ticker

    ticker = public_ticker(_binance_symbol(symbol))
    closes = public_daily_closes(ticker["symbol"])
    row = {
        "symbol": ticker["symbol"],
        "name": name,
        "price": ticker["price"],
        "day_pct": ticker.get("day_pct") or 0,
        "week_pct": 0,
        "kind": "crypto",
        "source": "binance",
    }
    return _attach_ema(row, closes, ticker["price"])


def _fetch_yahoo(symbol: str, name: str) -> dict:
    row = _fetch_one(_yahoo_symbol(symbol), name)
    row["source"] = "yahoo"
    row["kind"] = "stock"
    return row


def fetch_mixed(items: list[dict]) -> list[dict]:
    jobs: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for item in items:
        symbol = (item.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        name = (item.get("name") or symbol).strip() or symbol
        source = _normalize_source(item.get("source") or "", symbol)
        jobs.append((symbol, name, source))
    if not jobs:
        return []
    session = us_equity_session()
    by_sym: dict[str, dict] = {}
    tv_jobs = [(symbol, name) for symbol, name, source in jobs if source != "binance"]
    bn_jobs = [(symbol, name) for symbol, name, source in jobs if source == "binance"]
    if tv_jobs:
        tv_key = f"tv:{session['key']}:" + ",".join(symbol for symbol, _name in tv_jobs)
        try:
            rows = _cached(tv_key, tv_cache_ttl(session), lambda: fetch_tv_quotes(tv_jobs))
            for row in rows:
                by_sym[row.get("requested") or row.get("symbol")] = row
        except Exception as exc:
            for symbol, name in tv_jobs:
                by_sym[symbol] = _empty_quote(symbol, name, "tradingview", f"TradingView: {exc}")
    if bn_jobs:
        bn_key = "bn:" + ",".join(symbol for symbol, _name in bn_jobs)
        for row in _cached(bn_key, 15, lambda: _fetch_binance_jobs(bn_jobs)):
            by_sym[row.get("requested") or row.get("symbol")] = row
    return [
        by_sym.get(symbol) or _empty_quote(symbol, name, source, "无报价")
        for symbol, name, source in jobs
    ]


def _empty_quote(symbol: str, name: str, source: str, error: str) -> dict:
    crypto = source == "binance"
    return {
        "symbol": symbol,
        "name": name,
        "price": None,
        "day_pct": 0,
        "week_pct": 0,
        "kind": "crypto" if crypto else "stock",
        "source": source,
        "requested": symbol,
        "session": "crypto" if crypto else us_equity_session()["key"],
        "session_label": "24h" if crypto else us_equity_session()["label"],
        "error": error,
        "ema5": None,
        "ema10": None,
        "ema20": None,
    }


def _fetch_binance_jobs(jobs: list[tuple[str, str]]) -> list[dict]:
    def _one(symbol: str, name: str) -> dict:
        try:
            row = _fetch_binance(symbol, name)
        except Exception as exc:
            return _empty_quote(symbol, name, "binance", f"binance: {exc}")
        row["requested"] = symbol
        row["session"] = "crypto"
        row["session_label"] = "24h"
        if row.get("price") is None:
            row["error"] = row.get("error") or "无报价"
        return row

    by_sym: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_one, symbol, name): symbol for symbol, name in jobs}
        for fut in as_completed(futs):
            symbol = futs[fut]
            try:
                by_sym[symbol] = fut.result()
            except Exception:
                name = next((n for s, n in jobs if s == symbol), symbol)
                by_sym[symbol] = _empty_quote(symbol, name, "binance", "行情拉取失败")
    return [by_sym[symbol] for symbol, _name in jobs]


def build_tape(watchlist: list[str] | None = None) -> dict:
    def _load():
        names = watchlist or []
        watched = [(s.upper(), s.upper()) for s in names if s.strip()][:8]
        return {
            "indices": fetch_quotes(TAPE),
            "watchlist": fetch_quotes(watched) if watched else [],
            "market": us_equity_session(),
        }

    session = us_equity_session()
    key = f"tape:{session['key']}:" + ",".join(watchlist or [])
    return _cached(key, tv_cache_ttl(session), _load)
