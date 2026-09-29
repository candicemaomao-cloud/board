from __future__ import annotations

import asyncio
import json
import random
import re
import string
import threading
from datetime import date, datetime, time as dtime, timedelta
from time import time
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")
PRE_OPEN = dtime(4, 0)
REG_OPEN = dtime(9, 30)
REG_CLOSE = dtime(16, 0)
POST_CLOSE = dtime(20, 0)
EQUITY_TYPES = {"stock", "fund", "dr"}
SESSION_LABELS = {
    "pre": "盘前",
    "regular": "盘中",
    "post": "盘后",
    "closed": "休市",
    "crypto": "24h",
}
US_HOLIDAYS = {
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}

TV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
    "Content-Type": "application/json",
}
COLUMNS = [
    "name",
    "close",
    "change",
    "change_abs",
    "Perf.W",
    "EMA5",
    "EMA10",
    "EMA20",
    "exchange",
    "description",
    "type",
    "currency",
    "premarket_close",
    "premarket_change",
    "postmarket_close",
    "postmarket_change",
]
ALIASES = {
    "ES=F": "CME_MINI:ES1!",
    "ES": "CME_MINI:ES1!",
    "NQ=F": "CME_MINI:NQ1!",
    "NQ": "CME_MINI:NQ1!",
    "^GSPC": "SP:SPX",
    "GSPC": "SP:SPX",
    "SPX": "SP:SPX",
    "^NDX": "NASDAQ:NDX",
    "NDX": "NASDAQ:NDX",
    "SPY": "AMEX:SPY",
    "QQQ": "NASDAQ:QQQ",
    "SOXX": "NASDAQ:SOXX",
    "SMH": "NASDAQ:SMH",
    "USO": "AMEX:USO",
    "GLD": "AMEX:GLD",
    "DIA": "AMEX:DIA",
    "IWM": "AMEX:IWM",
    "XLK": "AMEX:XLK",
    "XLC": "AMEX:XLC",
    "XLY": "AMEX:XLY",
    "XLP": "AMEX:XLP",
    "XLE": "AMEX:XLE",
    "XLF": "AMEX:XLF",
    "XLV": "AMEX:XLV",
    "XLI": "AMEX:XLI",
    "XLB": "AMEX:XLB",
    "XLRE": "AMEX:XLRE",
    "XLU": "AMEX:XLU",
    "^VIX": "TVC:VIX",
    "VIX": "TVC:VIX",
    "^MOVE": "TVC:MOVE",
    "MOVE": "TVC:MOVE",
    "ICE_MOVE": "TVC:MOVE",
    "AAII_BULL": "AAII:BULLISH",
    "AAII_BEAR": "AAII:BEARISH",
    "DX-Y.NYB": "TVC:DXY",
    "DXY": "TVC:DXY",
    "^TNX": "TVC:US10Y",
    "TNX": "TVC:US10Y",
    "US10Y": "TVC:US10Y",
    "USDJPY": "FX_IDC:USDJPY",
    "USDJPY=X": "FX_IDC:USDJPY",
    "HYG": "AMEX:HYG",
    "DFII10": "FRED:DFII10",
    "HYOAS": "FRED:BAMLH0A0HYM2",
    "NFCI": "FRED:NFCI",
    "JP10Y": "TVC:JP10Y",
    "DFF": "FRED:DFF",
    "FEDFUNDS": "FRED:FEDFUNDS",
    "DFEDTARU": "FRED:DFEDTARU",
    "DFEDTARL": "FRED:DFEDTARL",
    "ZQ1": "CBOT:ZQ1!",
    "MORTGAGE30US": "FRED:MORTGAGE30US",
    "SPCS20RSA": "FRED:SPCS20RSA",
    "DRSFRMACBS": "FRED:DRSFRMACBS",
    "TDSP": "FRED:TDSP",
    "DRCRELEXFACBS": "FRED:DRCRELEXFACBS",
    "IGOAS": "FRED:BAMLC0A0CM",
    "KRE": "AMEX:KRE",
    "VNQ": "AMEX:VNQ",
    "GC=F": "COMEX:GC1!",
    "GOLD": "COMEX:GC1!",
    "CL=F": "NYMEX:CL1!",
    "WTI": "NYMEX:CL1!",
    "NVDA": "NASDAQ:NVDA",
    "AMD": "NASDAQ:AMD",
    "AVGO": "NASDAQ:AVGO",
    "AMAT": "NASDAQ:AMAT",
    "LRCX": "NASDAQ:LRCX",
    "AMZN": "NASDAQ:AMZN",
    "META": "NASDAQ:META",
    "JPM": "NYSE:JPM",
    "XOM": "NYSE:XOM",
    "AAPL": "NASDAQ:AAPL",
    "TSLA": "NASDAQ:TSLA",
    "PODD": "NASDAQ:PODD",
    "MU": "NASDAQ:MU",
    "INTC": "NASDAQ:INTC",
    "SPCX": "NASDAQ:SPCX",
    "GOOG": "NASDAQ:GOOG",
    "GOOGL": "NASDAQ:GOOGL",
    "MUD": "NASDAQ:MUD",
    "MSFT": "NASDAQ:MSFT",
    "TSM": "NYSE:TSM",
}
EXCHANGE_RANK = {
    "NASDAQ": 0,
    "NYSE": 1,
    "AMEX": 2,
    "NYSEARCA": 3,
    "CBOE": 4,
    "BATS": 5,
    "KRX": 6,
    "BINANCE": 8,
}
RESOLVE_TTL = 24 * 3600
_resolve_cache: dict[str, tuple[float, str]] = {}


def _client() -> httpx.Client:
    return httpx.Client(timeout=15.0, headers=TV_HEADERS, follow_redirects=True)


def _is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in US_HOLIDAYS


def _session_key(now: datetime) -> str:
    if not _is_trading_day(now.date()):
        return "closed"
    clock = now.time()
    if PRE_OPEN <= clock < REG_OPEN:
        return "pre"
    if REG_OPEN <= clock < REG_CLOSE:
        return "regular"
    if REG_CLOSE <= clock < POST_CLOSE:
        return "post"
    return "closed"


def _next_premarket(now: datetime) -> datetime:
    today_pre = datetime(now.year, now.month, now.day, 4, 0, tzinfo=ET)
    if _is_trading_day(now.date()) and now < today_pre:
        return today_pre
    day = now.date() + timedelta(days=1)
    while not _is_trading_day(day):
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, 4, 0, tzinfo=ET)


def us_equity_session(now: datetime | None = None) -> dict:
    now = now or datetime.now(ET)
    key = _session_key(now)
    live = key != "closed"
    payload = {
        "key": key,
        "label": SESSION_LABELS[key],
        "live": live,
        "tz": "America/New_York",
        "as_of": now.isoformat(),
        "next_open": None if live else _next_premarket(now).timestamp(),
    }
    return payload


def tv_cache_ttl(session: dict | None = None) -> float:
    info = session or us_equity_session()
    return 15 if info.get("live") else 6 * 3600


def resolve_ticker(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if not raw:
        raise RuntimeError("空代码")
    if ":" in raw:
        return raw
    hit = _resolve_cache.get(raw)
    if hit and time() - hit[0] < RESOLVE_TTL:
        return hit[1]
    ticker = ALIASES.get(raw) or _search(raw)
    _resolve_cache[raw] = (time(), ticker)
    return ticker


def ticker_candidates(symbol: str) -> list[str]:
    raw = (symbol or "").strip().upper()
    if not raw:
        return []
    if ":" in raw:
        exch, _, code = raw.partition(":")
        alts = [raw]
        for other in ("NASDAQ", "AMEX", "NYSE", "NYSEARCA"):
            cand = f"{other}:{code or raw}"
            if cand not in alts:
                alts.append(cand)
        return alts
    primary = ALIASES.get(raw)
    out = [primary] if primary else []
    for exch in ("NASDAQ", "AMEX", "NYSE"):
        cand = f"{exch}:{raw}"
        if cand not in out:
            out.append(cand)
    return out


def _search(symbol: str) -> str:
    with _client() as client:
        res = client.get(
            "https://symbol-search.tradingview.com/symbol_search/",
            params={"text": symbol, "lang": "en", "domain": "production", "sort_by_country": "US"},
        )
        res.raise_for_status()
        hits = res.json()
    if not isinstance(hits, list) or not hits:
        raise RuntimeError(f"TradingView 找不到 {symbol}")
    exact = [h for h in hits if str(h.get("symbol") or "").upper() == symbol]
    pool = exact or hits

    def score(hit: dict) -> int:
        exch = str(hit.get("exchange") or "").upper()
        typ = str(hit.get("type") or "")
        country = str(hit.get("country") or "")
        rank = EXCHANGE_RANK.get(exch, 40)
        if exch in {"PYTH", "FOREXCOM"}:
            rank += 50
        if typ in {"stock", "fund", "dr"}:
            rank -= 10
        if country == "US":
            rank -= 5
        if hit.get("is_primary_listing"):
            rank -= 3
        return rank

    best = min(pool, key=score)
    exch = best.get("exchange")
    sym = best.get("symbol")
    if not exch or not sym:
        raise RuntimeError(f"TradingView 找不到 {symbol}")
    return f"{exch}:{sym}"


def scan_quotes(tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    body = {
        "symbols": {"tickers": tickers, "query": {"types": []}},
        "columns": COLUMNS,
    }
    with _client() as client:
        res = client.post("https://scanner.tradingview.com/global/scan", json=body)
        res.raise_for_status()
        payload = res.json()
    out: dict[str, dict] = {}
    for row in payload.get("data") or []:
        ticker = row.get("s") or ""
        values = row.get("d") or []
        out[ticker] = dict(zip(COLUMNS, values))
    return out


def _kind(tv_type: str | None, symbol: str, ticker: str) -> str:
    raw = (symbol or "").upper()
    if raw in {"^TNX", "TNX", "US10Y"} or ticker == "TVC:US10Y" or tv_type == "bond":
        return "yield"
    if tv_type == "crypto":
        return "crypto"
    if tv_type in {"index", "futures"}:
        return "index"
    return "stock"


def _digits(kind: str, ticker: str) -> int:
    if kind == "yield" or ticker == "TVC:US10Y":
        return 4
    if ticker == "TVC:DXY":
        return 3
    return 2


def _num(value, digits: int | None = None):
    if value is None:
        return None
    number = float(value)
    return round(number, digits) if digits is not None else number


def _prev_close(regular_close, regular_change):
    if regular_close is None or regular_change is None:
        return None
    denom = 1 + float(regular_change) / 100
    if denom == 0:
        return None
    return float(regular_close) / denom


def _pick_session_price(fields: dict, kind: str, session: dict) -> tuple[float | None, float | None, str]:
    regular = fields.get("close")
    regular_chg = fields.get("change")
    pre = fields.get("premarket_close")
    pre_chg = fields.get("premarket_change")
    post = fields.get("postmarket_close")
    key = session.get("key") or "closed"
    equity = kind == "stock" or fields.get("type") in EQUITY_TYPES

    if not equity:
        return regular, regular_chg, key if key == "regular" else ("regular" if regular is not None else key)

    if key == "pre" and pre is not None:
        return pre, pre_chg, "pre"
    if key == "regular" and regular is not None:
        return regular, regular_chg, "regular"
    if key in {"post", "closed"} and post is not None:
        prev = _prev_close(regular, regular_chg)
        if prev:
            return post, (float(post) - prev) / prev * 100, "post" if key == "post" else "closed"
        return post, fields.get("postmarket_change"), "post" if key == "post" else "closed"
    if key == "post" and regular is not None:
        return regular, regular_chg, "regular"
    return regular, regular_chg, key


def fetch_tv_quotes(items: list[tuple[str, str]]) -> list[dict]:
    session = us_equity_session()
    resolved: list[tuple[str, str, str | None, str | None]] = []
    tickers: list[str] = []
    for symbol, name in items:
        try:
            ticker = resolve_ticker(symbol)
            resolved.append((symbol, name, ticker, None))
            tickers.append(ticker)
        except Exception as exc:
            resolved.append((symbol, name, None, str(exc)))
    scanned = scan_quotes(tickers)
    rows = []
    for symbol, name, ticker, error in resolved:
        fields = scanned.get(ticker or "") if ticker else None
        kind = _kind((fields or {}).get("type"), symbol, ticker or "")
        digits = _digits(kind, ticker or "")
        price, day_chg, shown = _pick_session_price(fields or {}, kind, session) if fields else (None, None, session["key"])
        if price is None:
            rows.append({
                "symbol": symbol,
                "name": name,
                "price": None,
                "day_pct": 0,
                "week_pct": 0,
                "kind": kind,
                "source": "tradingview",
                "requested": symbol,
                "ticker": ticker,
                "session": session["key"],
                "session_label": SESSION_LABELS.get(shown) or session["label"],
                "error": error or f"TradingView 无报价 {ticker or symbol}",
                "ema5": None,
                "ema10": None,
                "ema20": None,
            })
            continue
        perf_w = fields.get("Perf.W")
        regular = fields.get("close")
        rows.append({
            "symbol": fields.get("name") or symbol,
            "name": name,
            "price": round(float(price), digits),
            "day_pct": round(float(day_chg), 2) if day_chg is not None else 0,
            "week_pct": round(float(perf_w), 2) if perf_w is not None else 0,
            "regular_close": _num(regular, digits),
            "kind": kind,
            "source": "tradingview",
            "requested": symbol,
            "ticker": ticker,
            "session": session["key"],
            "session_label": SESSION_LABELS.get(shown) or session["label"],
            "ema5": _num(fields.get("EMA5"), 4),
            "ema10": _num(fields.get("EMA10"), 4),
            "ema20": _num(fields.get("EMA20"), 4),
        })
    return rows


TV_INTERVAL = {
    "5m": "5",
    "30m": "30",
    "4h": "240",
    "1d": "1D",
    "1w": "1W",
}
TV_BARS = {
    "5m": 6000,  # TradingView 这档实测上限约 5100 根（~3 个月），3000 太保守，砍掉了更早的区间
    "30m": 2000,
    "4h": 800,
    "1d": 520,
    "1w": 260,
}
_ohlc_cache: dict[str, tuple[float, list[dict]]] = {}
_ohlc_lock = threading.Lock()


def _ws_id(prefix: str) -> str:
    return prefix + "".join(random.choice(string.ascii_lowercase) for _ in range(12))


def _ws_msg(func: str, params: list) -> str:
    body = json.dumps({"m": func, "p": params}, separators=(",", ":"))
    return f"~m~{len(body)}~m~{body}"


def _ws_frames(raw: str) -> list[dict]:
    out = []
    for part in re.split(r"~m~\d+~m~", raw):
        part = part.strip()
        if not part.startswith("{"):
            continue
        try:
            obj = json.loads(part)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _bars_from_raw(raw: str) -> list[dict]:
    bars = []
    for obj in _ws_frames(raw):
        if obj.get("m") not in {"timescale_update", "du"}:
            continue
        payload = (obj.get("p") or [None, {}])[1] or {}
        if not isinstance(payload, dict):
            continue
        for series in payload.values():
            if not isinstance(series, dict):
                continue
            for row in series.get("s") or []:
                values = (row or {}).get("v") or []
                if len(values) < 5:
                    continue
                bars.append({
                    "ts": int(values[0]),
                    "open": float(values[1]),
                    "high": float(values[2]),
                    "low": float(values[3]),
                    "close": float(values[4]),
                    "volume": float(values[5]) if len(values) > 5 and values[5] is not None else 0.0,
                })
    uniq = {bar["ts"]: bar for bar in bars}
    return [uniq[key] for key in sorted(uniq)]


async def _tv_history(ticker: str, interval: str, n_bars: int) -> list[dict]:
    import websockets

    cs = _ws_id("cs_")
    spec = f'={{"symbol":"{ticker}","adjustment":"splits","session":"regular"}}'
    raw = ""
    async with websockets.connect(
        "wss://data.tradingview.com/socket.io/websocket",
        additional_headers={"Origin": "https://data.tradingview.com"},
        open_timeout=12,
        close_timeout=3,
    ) as ws:
        await ws.send(_ws_msg("set_auth_token", ["unauthorized_user_token"]))
        await ws.send(_ws_msg("chart_create_session", [cs, ""]))
        await ws.send(_ws_msg("resolve_symbol", [cs, "symbol_1", spec]))
        await ws.send(_ws_msg("create_series", [cs, "s1", "s1", "symbol_1", interval, n_bars]))
        deadline = time() + 12
        while time() < deadline:
            try:
                chunk = await asyncio.wait_for(ws.recv(), timeout=6)
            except TimeoutError:
                break
            if isinstance(chunk, bytes):
                chunk = chunk.decode()
            if "~h~" in chunk[:24]:
                await ws.send(chunk)
                continue
            raw += chunk
            if "symbol_error" in chunk or "series_error" in chunk:
                raise RuntimeError(f"TradingView 没有 {ticker} 的 K 线")
            if "series_completed" in chunk:
                break
    bars = _bars_from_raw(raw)
    if not bars:
        raise RuntimeError(f"TradingView 拉不到 {ticker} 的 K 线")
    return bars


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}

    def worker():
        try:
            box["v"] = asyncio.run(coro)
        except Exception as exc:
            box["e"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(18)
    if thread.is_alive():
        raise RuntimeError("TradingView K 线超时")
    if box.get("e"):
        raise box["e"]
    if "v" not in box:
        raise RuntimeError("TradingView K 线线程失败")
    return box["v"]


def fetch_tv_ohlc(symbol: str, timeframe: str, n_bars: int | None = None) -> list[dict]:
    interval = TV_INTERVAL.get(timeframe, "1D")
    n_bars = int(n_bars or TV_BARS.get(timeframe, 520))
    last = None
    for ticker in ticker_candidates(symbol):
        key = f"{ticker}:{interval}:{n_bars}"
        ttl = tv_cache_ttl()
        with _ohlc_lock:
            hit = _ohlc_cache.get(key)
            if hit and time() - hit[0] < ttl:
                return hit[1]
        try:
            bars = _run_async(_tv_history(ticker, interval, n_bars))
        except Exception as exc:
            last = exc
            continue
        if bars:
            raw = (symbol or "").strip().upper()
            if ":" not in raw:
                _resolve_cache[raw] = (time(), ticker)
            with _ohlc_lock:
                _ohlc_cache[key] = (time(), bars)
            return bars
        last = RuntimeError(f"TradingView 拉不到 {ticker} 的 K 线")
    raise last or RuntimeError(f"TradingView 拉不到 {symbol} 的 K 线")

