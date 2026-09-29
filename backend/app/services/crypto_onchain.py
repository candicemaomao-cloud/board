"""链上/衍生品代理数据：大户异动、交易所持仓与资金费率。"""

from __future__ import annotations

from app.services.binance import normalize_symbol
from app.services.crypto_market import CryptoMarketError, _cached, _http_get

BN_F = "https://fapi.binance.com"
BN_S = "https://api.binance.com"
MEMPOOL = "https://mempool.space/api"


DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


def _sym(raw: str) -> str:
    return normalize_symbol(raw or "BTC")


def open_interest(symbol: str = "BTCUSDT") -> dict:
    sym = _sym(symbol)

    def fetch():
        try:
            now = _http_get(f"{BN_F}/fapi/v1/openInterest", {"symbol": sym}, timeout=12.0)
            hist = _http_get(
                f"{BN_F}/futures/data/openInterestHist",
                {"symbol": sym, "period": "1h", "limit": 24},
                timeout=12.0,
            )
        except (CryptoMarketError, Exception) as exc:
            raise CryptoMarketError(f"持仓数据失败：{exc}") from exc
        oi = float((now or {}).get("openInterest") or 0) if isinstance(now, dict) else 0
        series = []
        if isinstance(hist, list):
            for row in hist:
                try:
                    series.append(
                        {
                            "ts": int(row.get("timestamp") or 0) // 1000,
                            "oi": float(row.get("sumOpenInterest") or 0),
                            "oi_value": float(row.get("sumOpenInterestValue") or 0),
                        }
                    )
                except (TypeError, ValueError):
                    continue
        change = None
        if len(series) >= 2 and series[0]["oi"]:
            change = (series[-1]["oi"] - series[0]["oi"]) / series[0]["oi"] * 100
        return {
            "symbol": sym,
            "open_interest": oi,
            "change_24h_pct": round(change, 2) if change is not None else None,
            "series": series,
            "note": "合约未平仓量（交易所侧代理大户/杠杆持仓）",
        }

    return _cached(f"oi:{sym}", 120, fetch)


def funding_rates(symbol: str = "BTCUSDT") -> dict:
    sym = _sym(symbol)

    def fetch():
        try:
            data = _http_get(
                f"{BN_F}/fapi/v1/fundingRate",
                {"symbol": sym, "limit": 12},
                timeout=12.0,
            )
        except (CryptoMarketError, Exception) as exc:
            raise CryptoMarketError(f"资金费率失败：{exc}") from exc
        rows = []
        if isinstance(data, list):
            for row in data:
                try:
                    rows.append(
                        {
                            "ts": int(row.get("fundingTime") or 0) // 1000,
                            "rate": float(row.get("fundingRate") or 0) * 100,
                        }
                    )
                except (TypeError, ValueError):
                    continue
        latest = rows[-1]["rate"] if rows else None
        return {
            "symbol": sym,
            "latest_pct": round(latest, 4) if latest is not None else None,
            "history": rows,
            "note": "资金费率为正偏多、为负偏空（合约情绪代理）",
        }

    return _cached(f"fund:{sym}", 120, fetch)


def whale_trades(symbol: str = "BTCUSDT", min_quote: float = 200_000) -> dict:
    """大额成交近似「大户异动」（现货/合约聚合成交）。"""
    sym = _sym(symbol)
    min_quote = max(50_000, float(min_quote or 200_000))

    def fetch():
        trades = []
        for url in (
            f"{BN_F}/fapi/v1/aggTrades",
            f"{BN_S}/api/v3/aggTrades",
        ):
            try:
                data = _http_get(url, {"symbol": sym, "limit": 500}, timeout=12.0)
            except (CryptoMarketError, Exception):
                continue
            if not isinstance(data, list):
                continue
            for row in data:
                try:
                    price = float(row.get("p") or 0)
                    qty = float(row.get("q") or 0)
                    quote = price * qty
                    if quote < min_quote:
                        continue
                    trades.append(
                        {
                            "ts": int(row.get("T") or 0) // 1000,
                            "price": price,
                            "qty": qty,
                            "quote": round(quote, 2),
                            "side": "卖出" if row.get("m") else "买入",
                            "source": "futures" if "fapi" in url else "spot",
                        }
                    )
                except (TypeError, ValueError):
                    continue
            if trades:
                break
        trades.sort(key=lambda x: x["ts"], reverse=True)
        buy = sum(t["quote"] for t in trades if t["side"] == "买入")
        sell = sum(t["quote"] for t in trades if t["side"] == "卖出")
        return {
            "symbol": sym,
            "min_quote": min_quote,
            "items": trades[:40],
            "buy_quote": round(buy, 2),
            "sell_quote": round(sell, 2),
            "net_quote": round(buy - sell, 2),
            "note": "近期大额成交净买入≈大户偏多（非严格链上）",
        }

    return _cached(f"whale:{sym}:{int(min_quote)}", 60, fetch)


def exchange_flow_proxy(symbols: list[str] | None = None) -> dict:
    """用 24h 成交额 + OI 变动近似「交易所活跃度/资金进出」。"""
    syms = [_sym(s) for s in (symbols or DEFAULT_SYMBOLS)]

    def fetch():
        rows = []
        for sym in syms:
            try:
                t = _http_get(f"{BN_F}/fapi/v1/ticker/24hr", {"symbol": sym}, timeout=10.0)
                oi = open_interest(sym)
            except (CryptoMarketError, Exception):
                continue
            if not isinstance(t, dict):
                continue
            try:
                vol = float(t.get("quoteVolume") or 0)
                change = float(t.get("priceChangePercent") or 0)
                price = float(t.get("lastPrice") or 0)
            except (TypeError, ValueError):
                continue
            oi_chg = oi.get("change_24h_pct")
            # 价涨 + OI 升 ≈ 资金流入杠杆；价跌 + OI 升 ≈ 空头涌入
            bias = "中性"
            if oi_chg is not None:
                if change > 0 and oi_chg > 0:
                    bias = "多头流入"
                elif change < 0 and oi_chg > 0:
                    bias = "空头流入"
                elif oi_chg < -1:
                    bias = "仓位撤离"
            rows.append(
                {
                    "symbol": sym.replace("USDT", ""),
                    "binance_symbol": sym,
                    "price": price,
                    "change_24h": change,
                    "volume_24h": vol,
                    "oi_change_24h": oi_chg,
                    "bias": bias,
                }
            )
        rows.sort(key=lambda x: abs(float(x.get("volume_24h") or 0)), reverse=True)
        return {
            "items": rows,
            "note": "用合约成交额与未平仓变动近似交易所净流入/流出方向",
        }

    return _cached(f"flow:{','.join(syms)}", 90, fetch)


def btc_mempool() -> dict:
    def fetch():
        try:
            fees = _http_get(f"{MEMPOOL}/v1/fees/recommended", timeout=12.0)
            tip = _http_get(f"{MEMPOOL}/blocks/tip/height", timeout=12.0)
        except (CryptoMarketError, Exception) as exc:
            raise CryptoMarketError(f"BTC 链上 mempool 失败：{exc}") from exc
        height = tip if isinstance(tip, int) else (int(tip) if str(tip).isdigit() else None)
        return {
            "height": height,
            "fees": fees if isinstance(fees, dict) else {},
            "note": "BTC mempool 推荐手续费（链上拥堵代理）",
            "source": "mempool.space",
        }

    return _cached("mempool:btc", 60, fetch)


def overview(symbol: str = "BTCUSDT") -> dict:
    from app.services.crypto_sentiment import sentiment_bundle

    sym = _sym(symbol)
    out = {"symbol": sym}
    for key, fn in (
        ("open_interest", lambda: open_interest(sym)),
        ("funding", lambda: funding_rates(sym)),
        ("whales", lambda: whale_trades(sym)),
        ("flows", exchange_flow_proxy),
    ):
        try:
            out[key] = fn()
        except CryptoMarketError as exc:
            out[key] = {"error": str(exc)}
    if sym.startswith("BTC"):
        try:
            out["mempool"] = btc_mempool()
        except CryptoMarketError as exc:
            out["mempool"] = {"error": str(exc)}
    try:
        out["sentiment"] = sentiment_bundle(sym)
    except Exception as exc:  # noqa: BLE001
        out["sentiment"] = {"error": str(exc)}
    return out
