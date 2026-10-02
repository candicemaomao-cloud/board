"""虚拟币行情：CoinGecko 榜单 + 币安 K 线 / 报价（外网失败可回退）。"""

from __future__ import annotations

import time

import httpx

from app.http_outbound import http_client
from app.services.binance import BinanceError, normalize_symbol, public_kline_bars, public_ticker

CG_BASE = "https://api.coingecko.com/api/v3"
BN_FUTURES = "https://fapi.binance.com"
BN_SPOT = "https://api.binance.com"
HEADERS = {"User-Agent": "PnlBoard/1.0", "Accept": "application/json"}

DEFAULT_COINS = [
    {"symbol": "BTC", "name": "Bitcoin", "coingecko_id": "bitcoin", "binance_symbol": "BTCUSDT"},
    {"symbol": "ETH", "name": "Ethereum", "coingecko_id": "ethereum", "binance_symbol": "ETHUSDT"},
    {"symbol": "BNB", "name": "BNB", "coingecko_id": "binancecoin", "binance_symbol": "BNBUSDT"},
    {"symbol": "SOL", "name": "Solana", "coingecko_id": "solana", "binance_symbol": "SOLUSDT"},
    {"symbol": "XRP", "name": "XRP", "coingecko_id": "ripple", "binance_symbol": "XRPUSDT"},
    {"symbol": "DOGE", "name": "Dogecoin", "coingecko_id": "dogecoin", "binance_symbol": "DOGEUSDT"},
    {"symbol": "ADA", "name": "Cardano", "coingecko_id": "cardano", "binance_symbol": "ADAUSDT"},
    {"symbol": "AVAX", "name": "Avalanche", "coingecko_id": "avalanche-2", "binance_symbol": "AVAXUSDT"},
    {"symbol": "DOT", "name": "Polkadot", "coingecko_id": "polkadot", "binance_symbol": "DOTUSDT"},
    {"symbol": "LINK", "name": "Chainlink", "coingecko_id": "chainlink", "binance_symbol": "LINKUSDT"},
]

_cache: dict[str, tuple[float, object]] = {}


class CryptoMarketError(ValueError):
    pass


def _cached(key: str, ttl: float, factory):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = factory()
    _cache[key] = (now, value)
    return value


def _http_get(url: str, params: dict | None = None, timeout: float = 10.0) -> object:
    """外网 GET；本地有 .env 代理时走 Clash，服务器无代理时直连。"""
    with http_client(timeout=timeout, headers=HEADERS) as client:
        res = client.get(url, params=params or {})
    try:
        data = res.json()
    except Exception as exc:  # noqa: BLE001
        raise CryptoMarketError("行情接口返回无法解析") from exc
    if res.status_code >= 400:
        msg = data.get("error") or data.get("msg") if isinstance(data, dict) else res.text
        raise CryptoMarketError(msg or f"HTTP {res.status_code}")
    return data


def _cg_get(path: str, params: dict | None = None) -> object:
    try:
        return _http_get(f"{CG_BASE}{path}", params=params)
    except httpx.HTTPError as exc:
        raise CryptoMarketError(f"无法连接 CoinGecko：{exc}") from exc


def search_coin(query: str) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        data = _cg_get("/search", {"query": q})
        coins = (data or {}).get("coins") if isinstance(data, dict) else []
        out = []
        for row in coins[:12]:
            symbol = str(row.get("symbol") or "").upper()
            cid = str(row.get("id") or "")
            if not symbol or not cid:
                continue
            out.append(
                {
                    "symbol": symbol,
                    "name": str(row.get("name") or symbol),
                    "coingecko_id": cid,
                    "binance_symbol": normalize_symbol(symbol),
                }
            )
        if out:
            return out
    except CryptoMarketError:
        pass
    ql = q.lower()
    return [
        {
            "symbol": c["symbol"],
            "name": c["name"],
            "coingecko_id": c.get("coingecko_id"),
            "binance_symbol": c.get("binance_symbol"),
        }
        for c in DEFAULT_COINS
        if ql in c["symbol"].lower() or ql in c["name"].lower() or ql in (c.get("coingecko_id") or "")
    ]


def _bn_ticker_rows() -> list[dict]:
    def fetch():
        for base in (BN_FUTURES + "/fapi/v1/ticker/24hr", BN_SPOT + "/api/v3/ticker/24hr"):
            try:
                data = _http_get(base, timeout=12.0)
            except (CryptoMarketError, httpx.HTTPError):
                continue
            if isinstance(data, list):
                return data
        return []

    return _cached("bn:ticker24h", 45, fetch)


def _rankings_from_binance(kind: str, limit: int) -> list[dict]:
    rows = _bn_ticker_rows()
    usdt = []
    for row in rows:
        sym = str(row.get("symbol") or "")
        if not sym.endswith("USDT"):
            continue
        try:
            price = float(row.get("lastPrice") or 0)
            change = float(row.get("priceChangePercent") or 0)
            vol = float(row.get("quoteVolume") or row.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        usdt.append(
            {
                "symbol": sym[:-4],
                "name": sym[:-4],
                "coingecko_id": None,
                "price": price,
                "change_24h": change,
                "market_cap": None,
                "volume_24h": vol,
                "image": None,
            }
        )
    key = "volume_24h" if kind == "volume" else "volume_24h"
    # 无市值时用成交量近似排行，保证页面有数据
    usdt.sort(key=lambda x: float(x.get(key) or 0), reverse=True)
    out = []
    for i, row in enumerate(usdt[:limit], start=1):
        row = dict(row)
        row["rank"] = i
        out.append(row)
    return out


def rankings(kind: str = "market_cap", limit: int = 30) -> list[dict]:
    order = "market_cap_desc" if kind != "volume" else "volume_desc"
    limit = max(5, min(int(limit or 30), 50))

    def fetch_cg():
        data = _cg_get(
            "/coins/markets",
            {
                "vs_currency": "usd",
                "order": order,
                "per_page": limit,
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "24h",
            },
        )
        if not isinstance(data, list):
            return []
        rows = []
        for i, row in enumerate(data, start=1):
            rows.append(
                {
                    "rank": i,
                    "symbol": str(row.get("symbol") or "").upper(),
                    "name": str(row.get("name") or ""),
                    "coingecko_id": row.get("id"),
                    "price": row.get("current_price"),
                    "change_24h": row.get("price_change_percentage_24h"),
                    "market_cap": row.get("market_cap"),
                    "volume_24h": row.get("total_volume"),
                    "image": row.get("image"),
                }
            )
        return rows

    try:
        return _cached(f"rank:{order}:{limit}", 60, fetch_cg)
    except CryptoMarketError:
        return _cached(f"rank:bn:{kind}:{limit}", 45, lambda: _rankings_from_binance(kind, limit))


def quotes_by_ids(coingecko_ids: list[str]) -> dict[str, dict]:
    ids = [x for x in coingecko_ids if x]
    if not ids:
        return {}
    key = ",".join(sorted(set(ids)))

    def fetch():
        data = _cg_get(
            "/coins/markets",
            {
                "vs_currency": "usd",
                "ids": key,
                "order": "market_cap_desc",
                "per_page": max(len(ids), 1),
                "page": 1,
                "sparkline": "true",
                "price_change_percentage": "24h",
            },
        )
        out = {}
        if not isinstance(data, list):
            return out
        for row in data:
            cid = row.get("id")
            if not cid:
                continue
            spark = ((row.get("sparkline_in_7d") or {}).get("price")) or []
            out[cid] = {
                "price": row.get("current_price"),
                "change_24h": row.get("price_change_percentage_24h"),
                "market_cap": row.get("market_cap"),
                "volume_24h": row.get("total_volume"),
                "market_cap_rank": row.get("market_cap_rank"),
                "image": row.get("image"),
                "sparkline": [round(float(x), 6) for x in spark[-48:]] if spark else [],
            }
        return out

    try:
        return _cached(f"quotes:{key}", 45, fetch)
    except CryptoMarketError:
        return {}


def _binance_quote(binance_symbol: str | None, symbol: str | None) -> dict | None:
    bn = binance_symbol or normalize_symbol(symbol or "")
    if not bn:
        return None
    try:
        t = public_ticker(bn)
    except BinanceError:
        # 再试 spot
        try:
            data = _http_get(f"{BN_SPOT}/api/v3/ticker/24hr", {"symbol": bn}, timeout=10.0)
        except (CryptoMarketError, httpx.HTTPError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            price = float(data.get("lastPrice") or 0)
            change = float(data.get("priceChangePercent") or 0)
            vol = float(data.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            return None
        return {
            "price": price,
            "change_24h": change,
            "volume_24h": vol,
            "sparkline": [],
            "quote_source": "binance",
        }
    # futures ticker 无量时从全表补
    vol = None
    for row in _bn_ticker_rows():
        if str(row.get("symbol") or "") == bn:
            try:
                vol = float(row.get("quoteVolume") or 0)
            except (TypeError, ValueError):
                vol = None
            break
    return {
        "price": t.get("price"),
        "change_24h": t.get("day_pct"),
        "volume_24h": vol,
        "sparkline": [],
        "quote_source": "binance",
    }


def _sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period or period <= 0:
        return None
    return round(sum(closes[-period:]) / period, 4)


def moving_averages(binance_symbol: str | None, symbol: str | None = None) -> dict:
    """日线 MA5 / MA10 / MA20。"""
    bn = binance_symbol or normalize_symbol(symbol or "")
    if not bn:
        return {"ma5": None, "ma10": None, "ma20": None}

    def fetch():
        try:
            bars = klines(bn, interval="1d", limit=30)
        except CryptoMarketError:
            return {"ma5": None, "ma10": None, "ma20": None}
        closes = [float(b["close"]) for b in bars if b.get("close") is not None]
        return {
            "ma5": _sma(closes, 5),
            "ma10": _sma(closes, 10),
            "ma20": _sma(closes, 20),
        }

    return _cached(f"ma:1d:{bn}", 120, fetch)


def enrich_coin_row(row: dict) -> dict:
    """给币列表行补行情；CoinGecko 失败时回退币安 ticker。"""
    return enrich_coin_rows([row])[0]


def _bn_quote_map() -> dict[str, dict]:
    """一次拉全市场 ticker，避免列表接口对每个币串行请求。"""
    out: dict[str, dict] = {}
    for row in _bn_ticker_rows():
        sym = str(row.get("symbol") or "")
        if not sym:
            continue
        try:
            price = float(row.get("lastPrice") or 0)
            change = float(row.get("priceChangePercent") or 0)
            vol = float(row.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            continue
        out[sym] = {
            "price": price,
            "change_24h": change,
            "volume_24h": vol,
            "sparkline": [],
            "quote_source": "binance",
        }
    return out


def enrich_coin_rows(rows: list[dict], *, with_ma: bool = True) -> list[dict]:
    ids = [r.get("coingecko_id") for r in rows if r.get("coingecko_id")]
    quotes = quotes_by_ids(ids) if ids else {}
    need_bn = any(not quotes.get(r.get("coingecko_id")) for r in rows)
    bn_map = _bn_quote_map() if need_bn else {}
    out = []
    for row in rows:
        item = dict(row)
        cid = row.get("coingecko_id")
        q = quotes.get(cid) if cid else None
        if q:
            item.update(q)
            item["quote_source"] = "coingecko"
        else:
            bn = normalize_symbol(row.get("binance_symbol") or row.get("symbol") or "")
            bn_q = bn_map.get(bn) if bn else None
            if not bn_q:
                bn_q = _binance_quote(row.get("binance_symbol"), row.get("symbol"))
            if bn_q:
                item.update(bn_q)
            else:
                item["quote_source"] = None
                item["error"] = "行情暂不可用"
        if with_ma:
            ma = moving_averages(item.get("binance_symbol"), item.get("symbol"))
            item.update(ma)
        out.append(item)
    return out


def klines(binance_symbol: str | None, interval: str = "1d", limit: int = 90) -> list[dict]:
    symbol = normalize_symbol(binance_symbol or "")
    if not symbol:
        raise CryptoMarketError("缺少交易对")
    bars = public_kline_bars(symbol, interval=interval, limit=limit)
    if not bars:
        # spot 回退
        url = (
            f"{BN_SPOT}/api/v3/klines?symbol={symbol}"
            f"&interval={interval}&limit={max(20, min(int(limit), 1000))}"
        )
        try:
            data = _http_get(url, timeout=12.0)
        except (CryptoMarketError, httpx.HTTPError):
            data = []
        if isinstance(data, list):
            bars = []
            for row in data:
                if not isinstance(row, list) or len(row) < 6 or row[4] is None:
                    continue
                bars.append(
                    {
                        "ts": int(row[0] // 1000),
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5] or 0),
                    }
                )
    if not bars:
        raise CryptoMarketError(f"拉不到 {symbol} 的 K 线")
    return bars
