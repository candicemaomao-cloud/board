from __future__ import annotations

import hashlib
import hmac
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.http_outbound import http_client
from app.models import Side, TradeLog
from app.services.tags import get_or_create_tags

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"
HEADERS_UA = {"User-Agent": "PnlBoard/1.0"}

# 常见虚拟币代码（无 USDT 后缀时也按币安行情）
CRYPTO_BASES = {
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX", "DOT", "LINK",
    "MATIC", "POL", "ATOM", "NEAR", "APT", "ARB", "OP", "SUI", "PEPE", "WIF",
    "TRX", "LTC", "BCH", "UNI", "AAVE", "FIL", "ICP", "TON", "SHIB", "APT",
}


class BinanceError(Exception):
    pass


def normalize_symbol(raw: str) -> str:
    symbol = (raw or "").strip().upper().replace("/", "").replace("-", "")
    if not symbol:
        return ""
    if symbol.endswith(("USDT", "USDC")):
        return symbol
    return symbol + "USDT"


def looks_like_crypto(raw: str) -> bool:
    """裸代码 BTC/ETH 或 BTCUSDT 等，都视为虚拟币。"""
    symbol = (raw or "").strip().upper().replace("/", "").replace("-", "")
    if not symbol:
        return False
    if symbol.endswith(("USDT", "USDC")):
        return True
    base = symbol[:-4] if symbol.endswith(("USDT", "USDC")) else symbol
    return base in CRYPTO_BASES


def parse_symbols(raw: str | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").split(","):
        symbol = normalize_symbol(part)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


def public_quote(symbol: str) -> dict:
    symbol = normalize_symbol(symbol)
    if not symbol:
        raise BinanceError("请填写股票代码，例如 SKHY")
    url = f"{FUTURES_BASE}/fapi/v1/premiumIndex?symbol={symbol}"
    try:
        with http_client(timeout=10.0, headers=HEADERS_UA) as client:
            res = client.get(url)
        data = res.json()
    except httpx.HTTPError as exc:
        raise BinanceError(f"无法连接币安：{exc}") from exc
    except Exception as exc:
        raise BinanceError("币安返回无法解析") from exc
    if res.status_code >= 400:
        msg = data.get("msg") if isinstance(data, dict) else res.text
        raise BinanceError(msg or f"{symbol} 不是有效的币安 U 本位交易对")
    return {
        "symbol": symbol,
        "stock": symbol[:-4] if symbol.endswith("USDT") else symbol,
        "price": round(float(data.get("markPrice") or 0), 4),
        "index_price": round(float(data.get("indexPrice") or 0), 4),
        "market": "futures",
    }


def public_ticker(symbol: str) -> dict:
    symbol = normalize_symbol(symbol)
    if not symbol:
        raise BinanceError("请填写股票代码，例如 SKHY")
    url = f"{FUTURES_BASE}/fapi/v1/ticker/24hr?symbol={symbol}"
    try:
        with http_client(timeout=10.0, headers=HEADERS_UA) as client:
            res = client.get(url)
        data = res.json()
    except httpx.HTTPError as exc:
        raise BinanceError(f"无法连接币安：{exc}") from exc
    except Exception as exc:
        raise BinanceError("币安返回无法解析") from exc
    if res.status_code >= 400:
        msg = data.get("msg") if isinstance(data, dict) else res.text
        raise BinanceError(msg or f"{symbol} 不是有效的币安 U 本位交易对")
    price = float(data.get("lastPrice") or data.get("markPrice") or 0)
    day_pct = float(data.get("priceChangePercent") or 0)
    return {
        "symbol": symbol,
        "stock": symbol[:-4] if symbol.endswith("USDT") else symbol,
        "price": round(price, 4),
        "day_pct": round(day_pct, 2),
        "market": "futures",
    }


def public_kline_bars(symbol: str, interval: str = "1d", limit: int = 500) -> list[dict]:
    symbol = normalize_symbol(symbol)
    if not symbol:
        return []
    allowed = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
    if interval not in allowed:
        interval = "1d"
    url = (
        f"{FUTURES_BASE}/fapi/v1/klines?symbol={symbol}"
        f"&interval={interval}&limit={max(20, min(int(limit), 1500))}"
    )
    try:
        with http_client(timeout=12.0, headers=HEADERS_UA) as client:
            res = client.get(url)
        data = res.json()
    except Exception:
        return []
    if res.status_code >= 400 or not isinstance(data, list):
        return []
    bars = []
    for row in data:
        if not isinstance(row, list) or len(row) < 6 or row[4] is None:
            continue
        bars.append({
            "ts": int(row[0] // 1000),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5] or 0),
        })
    return bars


def public_klines(symbol: str, interval: str = "1d", limit: int = 500) -> list[float]:
    return [bar["close"] for bar in public_kline_bars(symbol, interval, limit)]


def public_daily_closes(symbol: str, limit: int = 60) -> list[float]:
    return public_klines(symbol, "1d", limit)


_INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def public_kline_bars_covering(
    symbol: str,
    interval: str = "5m",
    start: date | None = None,
    end: date | None = None,
) -> list[dict]:
    """分页拉币安 U 本位 K 线，覆盖 start~end（默认到现在）。单次最多约 1500 根，自动翻页。"""
    symbol = normalize_symbol(symbol)
    if not symbol:
        return []
    if interval not in _INTERVAL_MS:
        interval = "5m"
    step = _INTERVAL_MS[interval]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    end_ms = now_ms
    if end is not None:
        end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
        end_ms = min(now_ms, int(end_dt.timestamp() * 1000))
    if start is not None:
        start_ms = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    else:
        start_ms = end_ms - 60 * 86_400_000

    out: list[dict] = []
    cursor = start_ms
    seen: set[int] = set()
    with http_client(timeout=20.0, headers=HEADERS_UA) as client:
        for _ in range(40):  # 上限约 40*1500 根，防止死循环
            if cursor > end_ms:
                break
            url = (
                f"{FUTURES_BASE}/fapi/v1/klines?symbol={symbol}"
                f"&interval={interval}&startTime={cursor}&endTime={end_ms}&limit=1500"
            )
            try:
                res = client.get(url)
                data = res.json()
            except Exception:
                break
            if res.status_code >= 400 or not isinstance(data, list) or not data:
                break
            last_open = None
            for row in data:
                if not isinstance(row, list) or len(row) < 6 or row[4] is None:
                    continue
                ts = int(row[0] // 1000)
                if ts in seen:
                    continue
                seen.add(ts)
                last_open = int(row[0])
                out.append({
                    "ts": ts,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5] or 0),
                })
            if last_open is None:
                break
            nxt = last_open + step
            if nxt <= cursor:
                break
            cursor = nxt
            if len(data) < 1500:
                break
    out.sort(key=lambda b: b["ts"])
    return out


def _display_symbol(symbol: str) -> str:
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def _sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _get(base: str, path: str, key: str, secret: str, params: dict) -> list | dict:
    payload = dict(params)
    payload["timestamp"] = int(time.time() * 1000)
    payload["recvWindow"] = 60000
    query = urlencode(payload, doseq=True)
    url = f"{base}{path}?{query}&signature={_sign(secret, query)}"
    try:
        with http_client(timeout=20.0, headers={**HEADERS_UA, "X-MBX-APIKEY": key}) as client:
            res = client.get(url)
    except httpx.HTTPError as exc:
        raise BinanceError(f"无法连接币安：{exc}") from exc
    try:
        data = res.json()
    except Exception as exc:
        raise BinanceError(f"币安返回无法解析（HTTP {res.status_code}）") from exc
    if res.status_code >= 400:
        msg = data.get("msg") if isinstance(data, dict) else res.text
        raise BinanceError(msg or f"币安接口错误 {res.status_code}")
    return data


def _existing_ids(db: Session) -> set[str]:
    rows = db.scalars(select(TradeLog.external_id).where(TradeLog.external_id.is_not(None)))
    return {x for x in rows if x}


def _save(db: Session, trade: TradeLog, seen: set[str]) -> bool:
    if not trade.external_id or trade.external_id in seen:
        return False
    db.add(trade)
    seen.add(trade.external_id)
    return True


def _ms_to_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def sync_futures_pnl(db: Session, key: str, secret: str, symbols: list[str], start: datetime, seen: set[str]) -> tuple[int, list[str]]:
    if not symbols:
        raise BinanceError("请填写要同步的股票代码，例如 SKHY")
    added = 0
    warnings: list[str] = []
    tags = get_or_create_tags(db, ["币安同步", "U本位合约"])
    for symbol in symbols:
        try:
            added += _sync_symbol_user_trades(db, key, secret, symbol, start, seen, tags)
        except BinanceError as exc:
            warnings.append(f"{symbol}：{exc}")
    return added, warnings


def _sync_symbol_user_trades(
    db: Session,
    key: str,
    secret: str,
    symbol: str,
    start: datetime,
    seen: set[str],
    tags: list,
) -> int:
    added = 0
    cursor = int(start.timestamp() * 1000)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    window_ms = 7 * 24 * 60 * 60 * 1000 - 1
    display = _display_symbol(symbol)
    while cursor <= end_ms:
        window_end = min(cursor + window_ms, end_ms)
        page_from: int | None = None
        while True:
            params: dict = {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": window_end,
                "limit": 1000,
            }
            if page_from is not None:
                params = {"symbol": symbol, "fromId": page_from, "limit": 1000}
            rows = _get(FUTURES_BASE, "/fapi/v1/userTrades", key, secret, params)
            if not isinstance(rows, list) or not rows:
                break
            last_id = int(rows[-1].get("id") or 0)
            for row in rows:
                ts = int(row.get("time") or 0)
                if ts < cursor or ts > window_end:
                    continue
                pnl = float(row.get("realizedPnl") or 0)
                if abs(pnl) < 1e-8:
                    continue
                qty = row.get("qty")
                price = row.get("price")
                side = str(row.get("side") or "").upper()
                ok = _save(
                    db,
                    TradeLog(
                        date=_ms_to_date(ts),
                        symbol=display[:16],
                        side=Side.LONG if side != "SELL" else Side.SHORT,
                        pnl_amount=round(pnl, 4),
                        notes=f"币安 {symbol} {side} {qty}@{price}",
                        source="binance",
                        external_id=f"binance:utrade:{row.get('id')}",
                        tags=list(tags),
                    ),
                    seen,
                )
                if ok:
                    added += 1
            if len(rows) < 1000:
                break
            page_from = last_id + 1
        cursor = window_end + 1
    return added


def _fifo_spot(fills: list[dict]) -> list[dict]:
    lots: list[list[float]] = []
    realized: list[dict] = []
    for fill in sorted(fills, key=lambda x: int(x["time"])):
        qty = float(fill["qty"])
        price = float(fill["price"])
        commission = float(fill.get("commission") or 0)
        commission_asset = fill.get("commissionAsset") or ""
        fee = commission if commission_asset in {"USDT", "USDC", "FDUSD"} else 0.0
        if fill.get("isBuyer"):
            lots.append([qty, price])
            continue
        remain = qty
        pnl = -fee
        while remain > 1e-12 and lots:
            lot_qty, lot_price = lots[0]
            take = min(remain, lot_qty)
            pnl += (price - lot_price) * take
            lot_qty -= take
            remain -= take
            if lot_qty <= 1e-12:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty
        if abs(pnl) < 1e-8:
            continue
        realized.append(
            {
                "id": fill["id"],
                "time": int(fill["time"]),
                "symbol": fill["symbol"],
                "pnl": pnl,
            }
        )
    return realized


def _spot_fills(key: str, secret: str, symbol: str, start_ms: int) -> list[dict]:
    fills: list[dict] = []
    params: dict = {"symbol": symbol, "startTime": start_ms, "limit": 1000}
    while True:
        batch = _get(SPOT_BASE, "/api/v3/myTrades", key, secret, params)
        if not isinstance(batch, list) or not batch:
            break
        fills.extend(batch)
        if len(batch) < 1000:
            break
        params = {"symbol": symbol, "fromId": int(batch[-1]["id"]) + 1, "limit": 1000}
    return fills


def sync_spot_pnl(db: Session, key: str, secret: str, symbols: list[str], start: datetime, seen: set[str]) -> int:
    added = 0
    start_ms = int(start.timestamp() * 1000)
    tags = get_or_create_tags(db, ["币安同步", "现货"])
    for symbol in symbols:
        try:
            fills = _spot_fills(key, secret, symbol, start_ms)
        except BinanceError as exc:
            raise BinanceError(f"现货 {symbol}：{exc}") from exc
        if not fills:
            continue
        for row in _fifo_spot(fills):
            ext = f"binance:spot:{symbol}:{row['id']}"
            ok = _save(
                db,
                TradeLog(
                    date=_ms_to_date(row["time"]),
                    symbol=symbol[:16],
                    side=Side.LONG if row["pnl"] >= 0 else Side.SHORT,
                    pnl_amount=round(row["pnl"], 4),
                    notes="币安现货平仓盈亏（FIFO 估算）",
                    source="binance",
                    external_id=ext,
                    tags=list(tags),
                ),
                seen,
            )
            if ok:
                added += 1
    return added


def sync_binance(
    db: Session,
    key: str,
    secret: str,
    symbols: list[str],
    days: int = 90,
    futures: bool = True,
    spot: bool = True,
) -> dict:
    key = (key or "").strip()
    secret = (secret or "").strip()
    if not key or not secret:
        raise BinanceError("请先填写币安 API Key 和 Secret")
    if not futures and not spot:
        raise BinanceError("请至少选择现货或合约")
    symbols = [normalize_symbol(s) for s in symbols if normalize_symbol(s)]
    if futures and not symbols:
        raise BinanceError("请填写要同步的股票代码，例如 SKHY")
    start = datetime.now(timezone.utc) - timedelta(days=days)
    seen = _existing_ids(db)
    warnings: list[str] = []
    fut = 0
    spo = 0
    if futures:
        fut, fut_warn = sync_futures_pnl(db, key, secret, symbols, start, seen)
        warnings.extend(fut_warn)
        if fut == 0 and fut_warn and not spot:
            raise BinanceError("；".join(warnings))
    if spot:
        try:
            spo = sync_spot_pnl(db, key, secret, symbols, start, seen)
        except BinanceError as exc:
            warnings.append(str(exc))
    db.commit()
    return {"futures": fut, "spot": spo, "days": days, "symbols": symbols, "warnings": warnings}
