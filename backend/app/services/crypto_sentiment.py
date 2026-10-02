"""情绪指标：当前币种在币安 / 欧意的做多做空统计。"""

from __future__ import annotations

import time

import httpx

from app.http_outbound import http_client
from app.services.binance import normalize_symbol
from app.services.crypto_market import CryptoMarketError, _cached, _http_get

BN_F = "https://fapi.binance.com"
OKX = "https://www.okx.com"
FNG_URL = "https://api.alternative.me/fng/"
HEADERS = {"User-Agent": "PnlBoard/1.0", "Accept": "application/json"}

OKX_PERIOD = {
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "12h": "12H",
    "1d": "1D",
}


def _label_fng(value: int | None, classification: str | None = None) -> str:
    if value is None:
        return classification or "—"
    if value <= 24:
        return "极度恐惧"
    if value <= 44:
        return "恐惧"
    if value <= 55:
        return "中性"
    if value <= 74:
        return "贪婪"
    return "极度贪婪"


def fear_greed(limit: int = 30) -> dict:
    limit = max(1, min(int(limit or 30), 365))

    def fetch():
        try:
            with http_client(timeout=15.0, headers=HEADERS) as client:
                res = client.get(FNG_URL, params={"limit": limit, "format": "json"})
            data = res.json()
        except Exception as exc:  # noqa: BLE001
            raise CryptoMarketError(f"无法获取恐惧贪婪指数：{exc}") from exc
        if res.status_code >= 400:
            raise CryptoMarketError(f"恐惧贪婪指数错误 {res.status_code}")
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            raise CryptoMarketError("恐惧贪婪指数暂无数据")
        history = []
        for row in rows:
            try:
                val = int(row.get("value"))
            except (TypeError, ValueError):
                continue
            try:
                ts = int(row.get("timestamp") or 0)
            except (TypeError, ValueError):
                ts = 0
            cls = str(row.get("value_classification") or "")
            history.append(
                {
                    "value": val,
                    "label": _label_fng(val, cls),
                    "classification": cls,
                    "ts": ts,
                    "date": time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else None,
                }
            )
        if not history:
            raise CryptoMarketError("恐惧贪婪指数暂无数据")
        latest = history[0]
        return {
            "value": latest["value"],
            "label": latest["label"],
            "classification": latest["classification"],
            "ts": latest["ts"],
            "date": latest["date"],
            "history": list(reversed(history)),
            "source": "alternative.me",
            "note": "Crypto Fear & Greed（全市场，偏 BTC）",
        }

    return _cached(f"fng:{limit}", 300, fetch)


def _base_ccy(symbol: str) -> str:
    sym = normalize_symbol(symbol or "BTC")
    return sym.replace("USDT", "").replace("USDC", "") or sym


def _okx_inst(symbol: str) -> str:
    return f"{_base_ccy(symbol)}-USDT-SWAP"


def _ratio_to_pcts(ratio: float) -> tuple[float, float]:
    """多空比 R=long/short → 做多%、做空%。"""
    r = max(0.0, float(ratio))
    long_pct = r / (1.0 + r) * 100.0
    short_pct = 100.0 - long_pct
    return round(long_pct, 2), round(short_pct, 2)


def _pct(x: float | None) -> float | None:
    if x is None:
        return None
    return round(float(x) * 100, 2)


def _bias_from_long_pct(long_pct: float | None) -> tuple[str, str]:
    if long_pct is None:
        return "—", "neutral"
    if long_pct >= 58:
        return "偏多", "bullish"
    if long_pct <= 42:
        return "偏空", "bearish"
    return "中性", "neutral"


def _overall_bias(rows: list[dict], funding_pct: float | None, prefix: str = "") -> tuple[str, str, list[str]]:
    longs = [r["long_pct"] for r in rows if r.get("long_pct") is not None]
    reasons: list[str] = []
    score = 0.0
    tag = f"{prefix}" if prefix else ""

    if longs:
        avg_long = sum(longs) / len(longs)
        if avg_long >= 55:
            score += (avg_long - 50) / 5
            reasons.append(f"{tag}多头占比均值 {avg_long:.1f}%")
        elif avg_long <= 45:
            score -= (50 - avg_long) / 5
            reasons.append(f"{tag}空头占优，多头仅 {avg_long:.1f}%")
        else:
            reasons.append(f"{tag}多空接近，多头 {avg_long:.1f}%")

    if funding_pct is not None:
        if funding_pct > 0.01:
            score += min(2.0, funding_pct / 0.02)
            reasons.append(f"{tag}资金费率 +{funding_pct:.4f}%")
        elif funding_pct < -0.01:
            score -= min(2.0, abs(funding_pct) / 0.02)
            reasons.append(f"{tag}资金费率 {funding_pct:.4f}%")

    if score >= 0.8:
        return "偏多", "bullish", reasons
    if score <= -0.8:
        return "偏空", "bearish", reasons
    return "中性", "neutral", reasons


def _metric_from_hist(key: str, title: str, hist: list[dict]) -> dict:
    cur = hist[-1] if hist else None
    if not cur:
        return {"key": key, "title": title, "error": "暂无数据"}
    label, tone = _bias_from_long_pct(cur.get("long_pct"))
    prev = hist[-2] if len(hist) >= 2 else None
    long_chg = None
    if prev and cur.get("long_pct") is not None and prev.get("long_pct") is not None:
        long_chg = round(cur["long_pct"] - prev["long_pct"], 2)
    return {
        "key": key,
        "title": title,
        "long_pct": cur.get("long_pct"),
        "short_pct": cur.get("short_pct"),
        "long_short_ratio": cur.get("long_short_ratio"),
        "long_chg_pct": long_chg,
        "label": label,
        "tone": tone,
        "ts": cur.get("ts"),
    }


def _parse_bn_row(row: dict | None) -> dict | None:
    if not isinstance(row, dict):
        return None
    try:
        long_a = float(row.get("longAccount") or row.get("longPosition") or 0)
        short_a = float(row.get("shortAccount") or row.get("shortPosition") or 0)
        ratio = float(row.get("longShortRatio") or 0)
        ts = int(row.get("timestamp") or 0) // 1000
    except (TypeError, ValueError):
        return None
    if not ratio and long_a and short_a:
        ratio = long_a / short_a
    if long_a == 0 and short_a == 0 and ratio > 0:
        long_pct, short_pct = _ratio_to_pcts(ratio)
    else:
        long_pct, short_pct = _pct(long_a), _pct(short_a)
    return {
        "long_pct": long_pct,
        "short_pct": short_pct,
        "long_short_ratio": round(ratio, 4) if ratio else None,
        "ts": ts or None,
    }


def _binance_ls(symbol: str, period: str) -> dict:
    sym = normalize_symbol(symbol or "BTC")
    specs = [
        ("global_account", "/futures/data/globalLongShortAccountRatio", "全市场账户"),
        ("top_account", "/futures/data/topLongShortAccountRatio", "大户账户"),
        ("top_position", "/futures/data/topLongShortPositionRatio", "大户持仓"),
    ]
    metrics = []
    latest_rows: list[dict] = []
    for key, path, title in specs:
        try:
            data = _http_get(
                f"{BN_F}{path}",
                {"symbol": sym, "period": period, "limit": 24},
                timeout=12.0,
            )
            hist = []
            if isinstance(data, list):
                for row in data:
                    parsed = _parse_bn_row(row)
                    if parsed:
                        hist.append(parsed)
            m = _metric_from_hist(key, title, hist)
        except (CryptoMarketError, Exception) as exc:
            m = {"key": key, "title": title, "error": str(exc)}
        metrics.append(m)
        if m.get("long_pct") is not None:
            latest_rows.append(m)

    funding_pct = None
    try:
        from app.services.crypto_onchain import funding_rates

        funding_pct = funding_rates(sym).get("latest_pct")
    except Exception:  # noqa: BLE001
        pass

    overall, tone, reasons = _overall_bias(latest_rows, funding_pct, prefix="")
    return {
        "exchange": "binance",
        "name": "币安",
        "symbol": _base_ccy(sym),
        "pair": sym,
        "period": period,
        "overall": overall,
        "tone": tone,
        "reasons": reasons,
        "funding_pct": funding_pct,
        "metrics": metrics,
    }


def _okx_get(path: str, params: dict) -> list:
    data = _http_get(f"{OKX}{path}", params, timeout=12.0)
    if not isinstance(data, dict):
        raise CryptoMarketError("欧意返回异常")
    if str(data.get("code")) not in ("0", "0.0", ""):
        raise CryptoMarketError(f"欧意错误 {data.get('code')}: {data.get('msg') or ''}".strip())
    rows = data.get("data")
    return rows if isinstance(rows, list) else []


def _parse_okx_ratio_series(rows: list) -> list[dict]:
    """OKX rubik：[[ts_ms, ratio], ...]，通常新→旧。"""
    hist = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            ts = int(float(row[0])) // 1000
            ratio = float(row[1])
        except (TypeError, ValueError):
            continue
        if ratio <= 0:
            continue
        long_pct, short_pct = _ratio_to_pcts(ratio)
        hist.append(
            {
                "long_pct": long_pct,
                "short_pct": short_pct,
                "long_short_ratio": round(ratio, 4),
                "ts": ts,
            }
        )
    # 统一成旧→新，便于取末条为最新
    hist.sort(key=lambda x: x.get("ts") or 0)
    return hist[-24:]


def _okx_funding_pct(inst_id: str) -> float | None:
    try:
        rows = _okx_get("/api/v5/public/funding-rate", {"instId": inst_id})
        if not rows or not isinstance(rows[0], dict):
            return None
        rate = float(rows[0].get("fundingRate") or 0)
        return round(rate * 100, 4)
    except Exception:  # noqa: BLE001
        return None


def _okx_ls(symbol: str, period: str) -> dict:
    base = _base_ccy(symbol)
    inst = _okx_inst(symbol)
    okx_period = OKX_PERIOD.get(period, "1H")
    specs = [
        (
            "global_account",
            "全市场账户",
            "/api/v5/rubik/stat/contracts/long-short-account-ratio",
            {"ccy": base, "period": okx_period},
        ),
        (
            "top_account",
            "大户账户",
            "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader",
            {"instId": inst, "period": okx_period},
        ),
        (
            "top_position",
            "大户持仓",
            "/api/v5/rubik/stat/contracts/long-short-position-ratio-contract-top-trader",
            {"instId": inst, "period": okx_period},
        ),
    ]
    metrics = []
    latest_rows: list[dict] = []
    for key, title, path, params in specs:
        try:
            raw = _okx_get(path, params)
            hist = _parse_okx_ratio_series(raw)
            m = _metric_from_hist(key, title, hist)
        except (CryptoMarketError, Exception) as exc:
            m = {"key": key, "title": title, "error": str(exc)}
        metrics.append(m)
        if m.get("long_pct") is not None:
            latest_rows.append(m)

    funding_pct = _okx_funding_pct(inst)
    overall, tone, reasons = _overall_bias(latest_rows, funding_pct, prefix="")
    return {
        "exchange": "okx",
        "name": "欧意",
        "symbol": base,
        "pair": inst,
        "period": period,
        "overall": overall,
        "tone": tone,
        "reasons": reasons,
        "funding_pct": funding_pct,
        "metrics": metrics,
    }


def _combine_exchanges(exchanges: list[dict]) -> tuple[str, str, list[str]]:
    """跨交易所综合：两边同向更强，分歧则中性偏提示。"""
    tones = [e.get("tone") for e in exchanges if e.get("tone") and not e.get("error")]
    reasons: list[str] = []
    for e in exchanges:
        if e.get("error"):
            reasons.append(f"{e.get('name')}: {e['error']}")
            continue
        reasons.append(f"{e.get('name')} {e.get('overall')}")
        if e.get("funding_pct") is not None:
            reasons.append(f"{e.get('name')}费率 {e['funding_pct']:+.4f}%")

    bull = tones.count("bullish")
    bear = tones.count("bearish")
    if bull and bear:
        return "分歧", "neutral", reasons + ["交易所多空方向不一致"]
    if bull >= 1 and bear == 0:
        return "偏多", "bullish", reasons
    if bear >= 1 and bull == 0:
        return "偏空", "bearish", reasons
    return "中性", "neutral", reasons


def long_short_stats(symbol: str = "BTCUSDT", period: str = "1h") -> dict:
    """当前币：币安 + 欧意 做多/做空统计。"""
    sym = normalize_symbol(symbol or "BTC")
    base = _base_ccy(sym)
    period = period if period in OKX_PERIOD else "1h"

    def fetch():
        exchanges = []
        try:
            exchanges.append(_binance_ls(sym, period))
        except Exception as exc:  # noqa: BLE001
            exchanges.append({"exchange": "binance", "name": "币安", "error": str(exc), "metrics": []})
        try:
            exchanges.append(_okx_ls(sym, period))
        except Exception as exc:  # noqa: BLE001
            exchanges.append({"exchange": "okx", "name": "欧意", "error": str(exc), "metrics": []})

        overall, tone, reasons = _combine_exchanges(exchanges)
        return {
            "symbol": base,
            "binance_symbol": sym,
            "period": period,
            "overall": overall,
            "tone": tone,
            "reasons": reasons,
            "exchanges": exchanges,
            "metrics": (exchanges[0].get("metrics") if exchanges else []) or [],
            "funding_pct": next((e.get("funding_pct") for e in exchanges if e.get("funding_pct") is not None), None),
            "note": "各交易所多空比独立统计，方向常不一致；币安/欧意均为合约账户或持仓占比",
            "source": "binance + okx",
        }

    return _cached(f"ls2:{sym}:{period}", 90, fetch)


def sentiment_bundle(current_symbol: str | None = None) -> dict:
    """弹窗用：当前币在币安 / 欧意的做多做空情绪。"""
    sym = normalize_symbol(current_symbol or "BTC")
    try:
        coin = long_short_stats(sym)
    except CryptoMarketError as exc:
        return {"error": str(exc), "symbol": sym}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "symbol": sym}

    market = None
    if sym.startswith("BTC"):
        try:
            market = fear_greed(limit=7)
        except CryptoMarketError as exc:
            market = {"error": str(exc)}

    return {
        "coin": coin,
        "market": market,
        "note": coin.get("note"),
    }
