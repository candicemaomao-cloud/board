"""美股情绪：CNN Fear & Greed Index。"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import httpx

CNN_FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.cnn.com",
    "Referer": "https://www.cnn.com/markets/fear-and-greed",
}

_cache: dict[str, tuple[float, object]] = {}

RATING_ZH = {
    "extreme fear": "极度恐惧",
    "fear": "恐惧",
    "neutral": "中性",
    "greed": "贪婪",
    "extreme greed": "极度贪婪",
}


class StockSentimentError(ValueError):
    pass


def _cached(key: str, ttl: float, factory):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = factory()
    _cache[key] = (now, value)
    return value


def _label(value: float | None, rating: str | None = None) -> str:
    if rating:
        zh = RATING_ZH.get(str(rating).strip().lower())
        if zh:
            return zh
    if value is None:
        return "—"
    v = float(value)
    if v <= 24:
        return "极度恐惧"
    if v <= 44:
        return "恐惧"
    if v <= 55:
        return "中性"
    if v <= 74:
        return "贪婪"
    return "极度贪婪"


def _tone(value: float | None) -> str:
    if value is None:
        return "neutral"
    v = float(value)
    if v <= 44:
        return "fear"
    if v >= 56:
        return "greed"
    return "neutral"


def fear_greed(*, history_days: int = 30) -> dict:
    """拉取 CNN 美股恐惧贪婪指数（全市场，非单票）。"""
    history_days = max(1, min(int(history_days or 30), 500))
    start = (date.today() - timedelta(days=max(history_days + 40, 90))).isoformat()

    def fetch():
        url = f"{CNN_FNG_URL}/{start}"
        try:
            with httpx.Client(timeout=20.0, headers=HEADERS, trust_env=False) as client:
                res = client.get(url)
            data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
        except Exception as exc:  # noqa: BLE001
            raise StockSentimentError(f"无法获取恐惧贪婪指数：{exc}") from exc
        if res.status_code >= 400:
            raise StockSentimentError(f"恐惧贪婪指数错误 {res.status_code}")
        if not isinstance(data, dict):
            raise StockSentimentError("恐惧贪婪指数暂无数据")

        cur = data.get("fear_and_greed") or {}
        try:
            score = float(cur.get("score"))
        except (TypeError, ValueError) as exc:
            raise StockSentimentError("恐惧贪婪指数暂无数据") from exc
        rating = str(cur.get("rating") or "")
        ts = str(cur.get("timestamp") or "")[:10] or None

        hist_block = data.get("fear_and_greed_historical") or {}
        hist_rows = hist_block.get("data") if isinstance(hist_block, dict) else None
        history: list[dict] = []
        if isinstance(hist_rows, list):
            for row in hist_rows[-history_days:]:
                try:
                    y = float(row.get("y"))
                    x = int(float(row.get("x") or 0))
                except (TypeError, ValueError):
                    continue
                history.append(
                    {
                        "value": round(y, 2),
                        "label": _label(y, row.get("rating")),
                        "rating": row.get("rating"),
                        "ts": x // 1000 if x else None,
                        "date": time.strftime("%Y-%m-%d", time.gmtime(x / 1000)) if x else None,
                    }
                )

        def _prev(key: str):
            raw = cur.get(key)
            try:
                return round(float(raw), 2)
            except (TypeError, ValueError):
                return None

        return {
            "value": round(score, 2),
            "label": _label(score, rating),
            "rating": rating,
            "tone": _tone(score),
            "date": ts,
            "previous_close": _prev("previous_close"),
            "previous_1_week": _prev("previous_1_week"),
            "previous_1_month": _prev("previous_1_month"),
            "previous_1_year": _prev("previous_1_year"),
            "history": history,
            "source": "CNN Fear & Greed",
            "note": "美股全市场恐惧贪婪（CNN），不是单只股票",
        }

    return _cached(f"cnn-fng:{history_days}:{start}", 300, fetch)


def _bars_to_series(ohlc: dict) -> list[dict]:
    from datetime import datetime, timezone

    out: list[dict] = []
    for bar in ohlc.get("ohlc_bars") or []:
        try:
            ts = int(bar.get("ts") or 0)
            close = float(bar.get("close"))
        except (TypeError, ValueError):
            continue
        if not ts:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        out.append({"date": day, "close": round(close, 4)})
    return out


def vix_history(*, days: int = 365) -> dict:
    """VIX 日线收盘序列。"""
    from app.services.ohlc import OhlcError, fetch_closes

    days = max(30, min(int(days or 365), 800))

    def fetch():
        try:
            ohlc = fetch_closes("VIX", "1d", apply_live=False)
        except OhlcError as exc:
            raise StockSentimentError(str(exc)) from exc
        series = _bars_to_series(ohlc)
        if days and len(series) > days:
            series = series[-days:]
        if not series:
            raise StockSentimentError("VIX 暂无数据")
        latest = series[-1]
        return {
            "symbol": "VIX",
            "value": latest["close"],
            "date": latest["date"],
            "series": series,
            "source": ohlc.get("source") or "yahoo",
            "note": "CBOE VIX 日线收盘",
        }

    return _cached(f"vix-hist:{days}", 300, fetch)


def fear_stock_path(
    symbol: str,
    *,
    event_date: str,
    before: int = 30,
    after: int = 60,
) -> dict:
    """CNN 事件日前后，单只股票日线走势（一次只算一只，省算力）。"""
    from datetime import datetime

    from app.services.ohlc import OhlcError, fetch_closes

    sym = (symbol or "").strip().upper()
    if not sym:
        raise StockSentimentError("请选择股票代码")
    try:
        event = datetime.strptime(event_date[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise StockSentimentError("事件日期格式应为 YYYY-MM-DD") from exc
    before = max(0, min(int(before or 30), 120))
    after = max(5, min(int(after or 60), 180))

    try:
        ohlc = fetch_closes(sym, "1d", apply_live=False)
    except OhlcError as exc:
        raise StockSentimentError(str(exc)) from exc

    series = _bars_to_series(ohlc)
    if not series:
        raise StockSentimentError(f"{sym} 没有价格数据")

    # 取事件日当天或之后第一根交易日作为锚点
    idx = next((i for i, row in enumerate(series) if row["date"] >= event.isoformat()), None)
    if idx is None:
        raise StockSentimentError(f"{sym} 在 {event.isoformat()} 之后没有交易日数据")

    start_i = max(0, idx - before)
    end_i = min(len(series) - 1, idx + after)
    window = series[start_i : end_i + 1]
    anchor = series[idx]
    base = float(anchor["close"])

    def _ret(offset: int) -> float | None:
        j = idx + offset
        if j < 0 or j >= len(series) or base == 0:
            return None
        return round((float(series[j]["close"]) / base) - 1.0, 6)

    path = []
    for i, row in enumerate(window):
        abs_i = start_i + i
        path.append(
            {
                "date": row["date"],
                "close": row["close"],
                "offset": abs_i - idx,
                "ret_from_event": round((float(row["close"]) / base) - 1.0, 6) if base else None,
            }
        )

    # 顺带给事件日当天的 CNN / VIX 水平（若有缓存/可算）
    cnn_at = None
    try:
        fng = fear_greed(history_days=400)
        for row in fng.get("history") or []:
            if row.get("date") == event.isoformat() or row.get("date") == anchor["date"]:
                cnn_at = row
                break
    except StockSentimentError:
        cnn_at = None

    vix_at = None
    try:
        vix = vix_history(days=800)
        for row in vix.get("series") or []:
            if row.get("date") == anchor["date"]:
                vix_at = row
                break
            if row.get("date") and row["date"] < anchor["date"]:
                vix_at = row
    except StockSentimentError:
        vix_at = None

    return {
        "symbol": sym,
        "event_date": event.isoformat(),
        "anchor_date": anchor["date"],
        "anchor_close": base,
        "before": before,
        "after": after,
        "source": ohlc.get("source"),
        "path": path,
        "returns": {
            "d1": _ret(1),
            "d5": _ret(5),
            "d20": _ret(20),
            "d60": _ret(60),
            "end": path[-1].get("ret_from_event") if path else None,
        },
        "cnn_at_event": cnn_at,
        "vix_at_event": vix_at,
        "note": "以事件日后第一个交易日收盘为基准，看后面走势",
    }


def _parse_day(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _ms_to_day(ms) -> str | None:
    try:
        ts = int(float(ms)) / 1000.0
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _cnn_raw(*, history_days: int = 400) -> dict:
    history_days = max(60, min(int(history_days or 400), 500))
    start = (date.today() - timedelta(days=max(history_days + 40, 90))).isoformat()

    def fetch():
        url = f"{CNN_FNG_URL}/{start}"
        try:
            with httpx.Client(timeout=25.0, headers=HEADERS, trust_env=False) as client:
                res = client.get(url)
            data = res.json() if "json" in (res.headers.get("content-type") or "") else {}
        except Exception as exc:  # noqa: BLE001
            raise StockSentimentError(f"无法获取恐惧贪婪指数：{exc}") from exc
        if res.status_code >= 400 or not isinstance(data, dict):
            raise StockSentimentError("恐惧贪婪指数暂无数据")
        return data

    return _cached(f"cnn-raw:{start}", 300, fetch)


def _series_lookup(series: list[dict], asof: date) -> tuple[dict | None, dict | None]:
    """返回 asof 当天或之前最近一根，以及再前一根（算涨跌）。"""
    target = asof.isoformat()
    cur = prev = None
    for row in series:
        d = row.get("date")
        if not d or d > target:
            break
        prev = cur
        cur = row
    return cur, prev


def _ohlc_series(symbol: str, *, days: int = 400) -> list[dict]:
    from app.services.ohlc import OhlcError, fetch_closes

    try:
        ohlc = fetch_closes(symbol, "1d", apply_live=False)
    except OhlcError:
        return []
    series = _bars_to_series(ohlc)
    if days and len(series) > days:
        series = series[-days:]
    return series


def _cnn_component_series(raw: dict, key: str) -> list[dict]:
    block = raw.get(key) or {}
    rows = block.get("data") if isinstance(block, dict) else None
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        day = _ms_to_day(row.get("x"))
        if not day:
            continue
        try:
            val = float(row.get("y"))
        except (TypeError, ValueError):
            continue
        rating = str(row.get("rating") or "")
        score = row.get("score")
        try:
            score_f = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_f = None
        out.append(
            {
                "date": day,
                "value": round(val, 4),
                "rating": rating,
                "label": _label(score_f, rating) if (score_f is not None or rating) else _label(None, rating),
                "score": round(score_f, 2) if score_f is not None else None,
            }
        )
    return out


def _clip01(x: float) -> float:
    return max(0.0, min(100.0, float(x)))


def _fear_from_vix(v: float | None) -> float | None:
    if v is None:
        return None
    # 12→0, 20→40, 30→70, 45→100
    if v <= 12:
        return 0.0
    if v <= 20:
        return (v - 12) / 8 * 40
    if v <= 30:
        return 40 + (v - 20) / 10 * 30
    if v <= 45:
        return 70 + (v - 30) / 15 * 30
    return 100.0


def _fear_from_move(v: float | None) -> float | None:
    if v is None:
        return None
    if v <= 60:
        return 0.0
    if v <= 100:
        return (v - 60) / 40 * 50
    if v <= 150:
        return 50 + (v - 100) / 50 * 50
    return 100.0


def _fear_from_pcr(v: float | None) -> float | None:
    if v is None:
        return None
    if v <= 0.6:
        return 0.0
    if v <= 1.0:
        return (v - 0.6) / 0.4 * 50
    if v <= 1.5:
        return 50 + (v - 1.0) / 0.5 * 50
    return 100.0


def _fear_from_hy_bp(v: float | None) -> float | None:
    if v is None:
        return None
    if v <= 300:
        return 0.0
    if v <= 450:
        return (v - 300) / 150 * 50
    if v <= 700:
        return 50 + (v - 450) / 250 * 50
    return 100.0


def _fear_from_cnn(v: float | None) -> float | None:
    if v is None:
        return None
    return _clip01(100.0 - float(v))


def _fear_from_aaii(spread_pct: float | None) -> float | None:
    if spread_pct is None:
        return None
    # +30 → 0, 0 → 50, -30 → 100
    return _clip01(50.0 - float(spread_pct) * (50.0 / 30.0))


def _tone_from_fear(fear: float | None) -> str:
    if fear is None:
        return "neutral"
    if fear >= 70:
        return "extreme_fear"
    if fear >= 55:
        return "fear"
    if fear <= 30:
        return "greed"
    if fear <= 45:
        return "neutral"
    return "fear"


def _label_from_fear(fear: float | None) -> str:
    tone = _tone_from_fear(fear)
    return {
        "extreme_fear": "极度恐惧",
        "fear": "恐惧",
        "neutral": "中性",
        "greed": "贪婪",
    }.get(tone, "—")


def _pack_band(tone: str, label: str, bar: float) -> dict:
    return {"tone": tone, "label": label, "bar": round(_clip01(bar), 1)}


def _band_vix(v: float | None) -> dict:
    """标签用语跟说明一致：平静 / 正常 / 紧张 / 恐慌"""
    if v is None:
        return _pack_band("neutral", "—", 0)
    if v < 15:
        return _pack_band("greed", "平静", 10 + v / 15 * 15)
    if v < 20:
        return _pack_band("neutral", "正常", 25 + (v - 15) / 5 * 20)
    if v < 30:
        return _pack_band("fear", "紧张", 50 + (v - 20) / 10 * 25)
    return _pack_band("extreme_fear", "恐慌", min(100, 75 + (v - 30) / 20 * 25))


def _band_move(v: float | None) -> dict:
    """标签用语跟说明一致：平静 / 正常 / 紧张 / 恐慌"""
    if v is None:
        return _pack_band("neutral", "—", 0)
    if v < 80:
        return _pack_band("greed", "平静", max(5, v / 80 * 25))
    if v < 100:
        return _pack_band("neutral", "正常", 25 + (v - 80) / 20 * 25)
    if v < 130:
        return _pack_band("fear", "紧张", 50 + (v - 100) / 30 * 25)
    return _pack_band("extreme_fear", "恐慌", min(100, 75 + (v - 130) / 40 * 25))


def _band_pcr(v: float | None) -> dict:
    """标签用语跟说明一致：贪婪 / 中性 / 恐惧 / 极度恐惧"""
    if v is None:
        return _pack_band("neutral", "—", 0)
    if v < 0.7:
        return _pack_band("greed", "贪婪", max(5, v / 0.7 * 25))
    if v <= 1.0:
        return _pack_band("neutral", "中性", 25 + (v - 0.7) / 0.3 * 25)
    if v <= 1.2:
        return _pack_band("fear", "恐惧", 50 + (v - 1.0) / 0.2 * 25)
    return _pack_band("extreme_fear", "极度恐惧", min(100, 75 + (v - 1.2) / 0.5 * 25))


def _band_hy_bp(v: float | None) -> dict:
    """标签用语跟说明一致：正常 / 紧张 / 危机"""
    if v is None:
        return _pack_band("neutral", "—", 0)
    if v < 350:
        return _pack_band("greed", "正常", max(5, v / 350 * 35))
    if v < 500:
        return _pack_band("fear", "紧张", 40 + (v - 350) / 150 * 35)
    return _pack_band("extreme_fear", "危机", min(100, 75 + (v - 500) / 300 * 25))


def _band_cnn(v: float | None) -> dict:
    """标签用语跟说明一致：极度恐惧 / 恐惧 / 中性 / 贪婪 / 极度贪婪"""
    if v is None:
        return _pack_band("neutral", "—", 0)
    return _pack_band(_tone(v), _label(v), _fear_from_cnn(v) or 0)


def _band_aaii(v: float | None) -> dict:
    """标签用语跟说明一致：正常 / 乐观 / 悲观 / 极度悲观"""
    if v is None:
        return _pack_band("neutral", "—", 0)
    if v > 10:
        return _pack_band("greed", "乐观", max(5, 35 - min(v, 40) * 0.5))
    if v >= -10:
        return _pack_band("neutral", "正常", 40 + abs(v))
    if v >= -25:
        return _pack_band("fear", "悲观", 55 + (-v - 10) / 15 * 20)
    return _pack_band("extreme_fear", "极度悲观", min(100, 80 + (-v - 25) / 20 * 20))


def _band_basis_pct(pct: float | None) -> dict:
    """ES−SPX 基差占现货比例。正且走扩偏多；收窄/转负偏抛压。"""
    if pct is None:
        return _pack_band("neutral", "—", 0)
    if pct >= 0.25:
        return _pack_band("greed", "加多", max(5, 35 - min(pct, 1.0) * 10))
    if pct >= 0.05:
        return _pack_band("neutral", "正常", 40 + (0.25 - pct) / 0.2 * 10)
    if pct >= -0.05:
        return _pack_band("fear", "收窄", 55 + (-pct) / 0.05 * 10)
    return _pack_band("extreme_fear", "贴水", min(100, 75 + (-pct - 0.05) / 0.3 * 25))


def _fear_from_basis_pct(pct: float | None) -> float | None:
    if pct is None:
        return None
    # +0.4% → 0, 0 → 50, −0.3% → 100
    return _clip01(50.0 - float(pct) * (50.0 / 0.35))


def _band_skew_pct(v: float | None) -> dict:
    """虚值 put IV 相对虚值 call IV 溢价。越高=越愿为下跌保护付费。"""
    if v is None:
        return _pack_band("neutral", "—", 0)
    # v 为小数，如 0.08 = 8%
    if v < 0.02:
        return _pack_band("greed", "偏贪婪", max(5, 25 + v / 0.02 * 10))
    if v < 0.08:
        return _pack_band("neutral", "正常", 35 + (v - 0.02) / 0.06 * 20)
    if v < 0.15:
        return _pack_band("fear", "偏恐惧", 55 + (v - 0.08) / 0.07 * 20)
    return _pack_band("extreme_fear", "尾部保护", min(100, 80 + (v - 0.15) / 0.15 * 20))


def _fear_from_skew_pct(v: float | None) -> float | None:
    if v is None:
        return None
    return _clip01(float(v) / 0.20 * 100.0)


def _band_gex_sign(gex: float | None) -> dict:
    if gex is None:
        return _pack_band("neutral", "—", 0)
    if gex > 0:
        return _pack_band("greed", "正 Gamma", 25)
    if gex < 0:
        return _pack_band("fear", "负 Gamma", 75)
    return _pack_band("neutral", "中性", 50)


def _aligned_basis_series(fut: list[dict], spot: list[dict]) -> list[dict]:
    spot_map = {r["date"]: float(r["close"]) for r in spot if r.get("close") is not None}
    out = []
    for row in fut:
        d = row.get("date")
        f = row.get("close")
        s = spot_map.get(d)
        if d is None or f is None or s is None or s == 0:
            continue
        pts = float(f) - float(s)
        pct = pts / float(s) * 100.0
        out.append({"date": d, "value": round(pct, 4), "pts": round(pts, 2), "fut": float(f), "spot": float(s)})
    return out


def _option_pulses(symbols: list[str]) -> dict[str, dict]:
    """近月期权链快照：成交量 Put/Call、OTM skew、简化 GEX。缓存 10 分钟。"""
    from app.user_stocks.options import BSInputs, bs_greeks

    syms = [s.strip().upper() for s in symbols if s and str(s).strip()]
    key = "opt-pulse-v2:" + ",".join(syms)

    def fetch():
        from yahooquery import Ticker
        import pandas as pd

        if not syms:
            return {}
        yq = Ticker(" ".join(syms))
        chain = yq.option_chain
        if not isinstance(chain, pd.DataFrame) or chain.empty:
            return {s: {"error": "无期权链"} for s in syms}

        df = chain.reset_index()
        exp_col = "expiration" if "expiration" in df.columns else "expiration_date"
        type_col = "optionType" if "optionType" in df.columns else "option_type"
        if "symbol" not in df.columns:
            df["symbol"] = syms[0]

        price_info = yq.price if isinstance(yq.price, dict) else {}
        out: dict[str, dict] = {}
        for sym in syms:
            try:
                sub_all = df[df["symbol"] == sym]
                if sub_all.empty:
                    out[sym] = {"error": "无期权链"}
                    continue
                spot_info = price_info.get(sym) or {}
                spot = spot_info.get("regularMarketPrice") or spot_info.get("postMarketPrice")
                if spot is None:
                    out[sym] = {"error": "无现价"}
                    continue
                spot = float(spot)
                exp = sorted(sub_all[exp_col].unique())[0]
                sub = sub_all[sub_all[exp_col] == exp]
                calls = sub[sub[type_col] == "calls"]
                puts = sub[sub[type_col] == "puts"]
                cv = float(calls["volume"].fillna(0).sum())
                pv = float(puts["volume"].fillna(0).sum())
                co = float(calls["openInterest"].fillna(0).sum())
                po = float(puts["openInterest"].fillna(0).sum())
                pcr_vol = (pv / cv) if cv > 0 else None
                pcr_oi = (po / co) if co > 0 else None

                # OTM skew：约 95% put IV vs 105% call IV（Yahoo 自带 IV）
                put_row = puts.iloc[(puts["strike"] - spot * 0.95).abs().argsort()[:1]]
                call_row = calls.iloc[(calls["strike"] - spot * 1.05).abs().argsort()[:1]]
                put_iv = float(put_row.iloc[0]["impliedVolatility"]) if not put_row.empty else None
                call_iv = float(call_row.iloc[0]["impliedVolatility"]) if not call_row.empty else None
                if put_iv and put_iv > 5:  # yahoo 偶发给小数或百分数；>5 当百分数
                    put_iv /= 100.0
                if call_iv and call_iv > 5:
                    call_iv /= 100.0
                skew_pct = None
                if put_iv and call_iv and call_iv > 0:
                    skew_pct = (put_iv - call_iv) / call_iv

                # 简化 GEX：现价 ±8% 行权价，用 Yahoo IV + BS gamma
                lo, hi = spot * 0.92, spot * 1.08
                near_c = calls[(calls["strike"] >= lo) & (calls["strike"] <= hi)]
                near_p = puts[(puts["strike"] >= lo) & (puts["strike"] <= hi)]
                exp_ts = pd.Timestamp(exp)
                days = max((exp_ts - pd.Timestamp.now()).days, 1)
                T = days / 365.0
                gex = 0.0
                gex_n = 0
                for _, row in near_c.iterrows():
                    iv = float(row.get("impliedVolatility") or 0)
                    if iv > 5:
                        iv /= 100.0
                    oi = float(row.get("openInterest") or 0)
                    if iv <= 0 or oi <= 0:
                        continue
                    try:
                        g = bs_greeks(
                            BSInputs(S=spot, K=float(row["strike"]), T=T, r=0.045, sigma=iv, option_type="call")
                        )["gamma"]
                    except Exception:
                        continue
                    gex += g * oi * 100 * spot
                    gex_n += 1
                for _, row in near_p.iterrows():
                    iv = float(row.get("impliedVolatility") or 0)
                    if iv > 5:
                        iv /= 100.0
                    oi = float(row.get("openInterest") or 0)
                    if iv <= 0 or oi <= 0:
                        continue
                    try:
                        g = bs_greeks(
                            BSInputs(S=spot, K=float(row["strike"]), T=T, r=0.045, sigma=iv, option_type="put")
                        )["gamma"]
                    except Exception:
                        continue
                    gex -= g * oi * 100 * spot
                    gex_n += 1

                out[sym] = {
                    "symbol": sym,
                    "spot": round(spot, 2),
                    "expiration": exp_ts.strftime("%Y-%m-%d"),
                    "days_to_exp": days,
                    "call_vol": round(cv, 0),
                    "put_vol": round(pv, 0),
                    "call_oi": round(co, 0),
                    "put_oi": round(po, 0),
                    "pcr_vol": None if pcr_vol is None else round(pcr_vol, 3),
                    "pcr_oi": None if pcr_oi is None else round(pcr_oi, 3),
                    "put_iv_otm": None if put_iv is None else round(put_iv, 4),
                    "call_iv_otm": None if call_iv is None else round(call_iv, 4),
                    "skew_pct": None if skew_pct is None else round(skew_pct, 4),
                    "gex": round(gex, 0) if gex_n else None,
                    "gex_strikes": gex_n,
                }
            except Exception as exc:  # noqa: BLE001
                out[sym] = {"error": str(exc)[:120]}
        return out

    return _cached(key, 600, fetch)


def _derivative_cards(*, asof_d: date) -> list[dict]:
    """衍生品三层：股指期货基差 / 股指期权 / 板块个股期权。不计入六项等权。"""
    today = date.today()
    live_only = asof_d < today - timedelta(days=3)  # 期权链无历史，偏旧日期仍给实时并标注

    # 1) ES 基差
    es_s = _ohlc_series("ES=F", days=80)
    spx_s = _ohlc_series("SPX", days=80)
    basis_hist = _aligned_basis_series(es_s, spx_s)
    basis_cur, basis_prev = _series_lookup(basis_hist, asof_d)
    basis_pct = None
    basis_pts = None
    if basis_cur:
        try:
            basis_pct = float(basis_cur.get("value"))
            basis_pts = float(basis_cur.get("pts")) if basis_cur.get("pts") is not None else None
            # series_lookup 只保留 value；从 hist 回填 pts
        except (TypeError, ValueError):
            basis_pct = None
    # 回填 pts
    if basis_cur and basis_pts is None:
        for row in reversed(basis_hist):
            if row["date"] == basis_cur.get("date"):
                basis_pts = row.get("pts")
                break
    basis_prev_pct = None
    if basis_prev:
        try:
            basis_prev_pct = float(basis_prev.get("value"))
        except (TypeError, ValueError):
            basis_prev_pct = None

    # ES 日波动（夜盘/消息面冲击的粗代理）
    es_chg = None
    if es_s:
        es_cur, es_prev = _series_lookup([{"date": r["date"], "value": r["close"]} for r in es_s], asof_d)
        try:
            if es_cur and es_prev and float(es_prev["value"]):
                es_chg = (float(es_cur["value"]) / float(es_prev["value"]) - 1.0) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            es_chg = None

    def _delta(cur, prev):
        if cur is None or prev is None:
            return None
        return round(cur - prev, 4)

    def _delta_txt(d, digits=2, suffix=""):
        if d is None:
            return None
        sign = "+" if d > 0 else ""
        return f"{sign}{d:.{digits}f}{suffix}"

    cards = []
    b_delta = _delta(basis_pct, basis_prev_pct)
    cards.append(
        _card(
            key="es_basis",
            title="股指期货 · ES 基差",
            value=None if basis_pct is None else round(basis_pct, 3),
            display="—" if basis_pct is None else f"{basis_pct:+.2f}%",
            delta=b_delta,
            delta_display=_delta_txt(b_delta, 2, "pp"),
            fear=_fear_from_basis_pct(basis_pct),
            band=_band_basis_pct(basis_pct),
            hint=(
                f"ES 连续合约 − SPX；"
                + (f"绝对基差 {basis_pts:+.1f} 点；" if basis_pts is not None else "")
                + (f"ES 日变 {es_chg:+.2f}%；" if es_chg is not None else "")
                + "正基差走扩≈机构加多；收窄/转负≈抛压或资金紧张。定位：大盘方向仓位，不含个股噪音。"
            ),
            unit="%",
        )
    )

    # 2+3) 期权链：SPY + 行业 ETF（对齐股票列表里的板块，不含个股）
    sectors = [
        "SOXX",  # 半导体
        "XLB",  # 原材料
        "XLC",  # 通讯服务
        "XLE",  # 能源
        "XLF",  # 金融
        "XLI",  # 工业
        "XLK",  # 科技
        "XLP",  # 必需消费品
        "XLRE",  # 房地产
        "XLU",  # 公用事业
        "XLV",  # 医疗
        "XLY",  # 可选消费
    ]
    pulses = {}
    try:
        pulses = _option_pulses(["SPY", *sectors])
    except Exception:  # noqa: BLE001
        pulses = {}

    spy = pulses.get("SPY") or {}
    skew = spy.get("skew_pct")
    gex = spy.get("gex")
    spy_pcr = spy.get("pcr_vol")
    live_tag = ""
    if live_only:
        live_tag = "（期权为实时快照，非历史日）"

    # 股指期权卡：主显示 skew，GEX 进标签旁 hint
    if spy.get("error") and skew is None:
        cards.append(
            _card(
                key="index_opt",
                title="股指期权 · SPY",
                value=None,
                display="—",
                delta=None,
                delta_display=None,
                fear=None,
                band=_pack_band("neutral", "—", 0),
                hint=f"期权链暂不可用：{spy.get('error')}{live_tag}",
            )
        )
    else:
        gex_band = _band_gex_sign(gex)
        # 综合：skew 主恐惧分，GEX 符号微调标签
        fear = _fear_from_skew_pct(skew)
        band = _band_skew_pct(skew)
        if gex is not None and gex < 0 and band.get("tone") in {"neutral", "greed"}:
            band = _pack_band("fear", "负Gamma放大", max(band.get("bar") or 50, 60))
        elif gex is not None and gex > 0 and band.get("tone") == "fear":
            band = _pack_band("fear", band.get("label") or "偏恐惧", band.get("bar") or 55)
        cards.append(
            _card(
                key="index_opt",
                title="股指期权 · Skew / GEX",
                value=None if skew is None else round(skew * 100, 2),
                display="—" if skew is None else f"{skew * 100:+.1f}%",
                delta=None,
                delta_display=None,
                fear=fear,
                band=band,
                hint=(
                    f"近月 {spy.get('expiration') or '—'}；"
                    f"成交 Put/Call {spy_pcr if spy_pcr is not None else '—'}；"
                    f"GEX {('正' if gex and gex > 0 else '负' if gex and gex < 0 else '—')}"
                    + (f"（{gex:,.0f}）" if gex is not None else "")
                    + f"。{gex_band.get('label') or ''}："
                    "正Gamma压制波动，负Gamma放大波动。Skew=虚值put相对call的IV溢价，越高越愿买下跌保护。"
                    f"{live_tag}"
                ),
                unit="%",
            )
        )

    # 板块期权：各行业 Put/Call 成交量拆分，前端用双色进度条
    SECTOR_NAMES = {
        "SOXX": "半导体",
        "XLB": "原材料",
        "XLC": "通讯服务",
        "XLE": "能源",
        "XLF": "金融",
        "XLI": "工业",
        "XLK": "科技",
        "XLP": "必需消费品",
        "XLRE": "房地产",
        "XLU": "公用事业",
        "XLV": "医疗",
        "XLY": "可选消费",
    }
    sector_rows = []
    for s in sectors:
        p = pulses.get(s) or {}
        if p.get("pcr_vol") is None:
            continue
        cv = float(p.get("call_vol") or 0)
        pv = float(p.get("put_vol") or 0)
        total = cv + pv
        call_share = round(cv / total * 100, 1) if total else None
        put_share = round(pv / total * 100, 1) if total else None
        pcr = float(p["pcr_vol"])
        band = _band_pcr(pcr)
        sector_rows.append(
            {
                "symbol": s,
                "name": SECTOR_NAMES.get(s, s),
                "pcr": round(pcr, 3),
                "call_vol": cv,
                "put_vol": pv,
                "call_share": call_share,
                "put_share": put_share,
                "skew": p.get("skew_pct"),
                "tone": band.get("tone"),
                "label": band.get("label"),
            }
        )
    sector_rows.sort(key=lambda r: r["pcr"])
    if not sector_rows:
        cards.append(
            _card(
                key="sector_opt",
                title="板块期权 · Put/Call",
                value=None,
                display="—",
                delta=None,
                delta_display=None,
                fear=None,
                band=_pack_band("neutral", "—", 0),
                hint=f"行业 ETF 期权链暂不可用{live_tag}",
            )
        )
    else:
        hot = sector_rows[0]  # 最低 PCR = call 相对更热
        cold = sector_rows[-1]
        avg_pcr = sum(r["pcr"] for r in sector_rows) / len(sector_rows)
        card = _card(
            key="sector_opt",
            title="板块期权 · Put/Call",
            value=round(avg_pcr, 3),
            display=f"均值 {avg_pcr:.2f}",
            delta=None,
            delta_display=None,
            fear=_fear_from_pcr(avg_pcr),
            band=_band_pcr(avg_pcr),
            hint=(
                f"近月成交量拆分：绿=Call、红=Put；条越偏绿越偏多头押注。"
                f"Call 最热 {hot['symbol']}（{hot['pcr']:.2f}），对冲最重 {cold['symbol']}（{cold['pcr']:.2f}）。"
                f"{live_tag}"
            ),
        )
        card["rows"] = sector_rows
        card["layout"] = "sector_bars"
        cards.append(card)

    # 给卡片打 group 标记，方便前端分区
    for c in cards:
        c["group"] = "derivatives"
    return cards


def _card(
    *,
    key: str,
    title: str,
    value,
    display: str,
    delta,
    delta_display: str | None,
    fear: float | None,
    hint: str,
    band: dict | None = None,
    unit: str = "",
) -> dict:
    b = band or {}
    tone = b.get("tone") or _tone_from_fear(fear)
    label = b.get("label") or _label_from_fear(fear)
    bar = b.get("bar")
    if bar is None:
        bar = None if fear is None else round(_clip01(fear), 1)
    return {
        "key": key,
        "title": title,
        "value": value,
        "display": display,
        "delta": delta,
        "delta_display": delta_display,
        "fear": None if fear is None else round(fear, 1),
        "bar": bar,
        "label": label,
        "tone": tone,
        "hint": hint,
        "unit": unit,
    }


def fear_panel(*, asof: str | None = None) -> dict:
    """六项恐慌指标面板：可指定历史日期。"""
    asof_d = _parse_day(asof) or date.today()
    raw = _cnn_raw(history_days=400)

    cnn_hist = []
    for row in (raw.get("fear_and_greed_historical") or {}).get("data") or []:
        day = _ms_to_day(row.get("x"))
        if not day:
            continue
        try:
            val = float(row.get("y"))
        except (TypeError, ValueError):
            continue
        rating = str(row.get("rating") or "")
        cnn_hist.append(
            {
                "date": day,
                "value": round(val, 2),
                "rating": rating,
                "label": _label(val, rating),
                "fear": round(_fear_from_cnn(val) or 0, 1),
                "tone": _tone(val),
            }
        )

    pcr_hist = _cnn_component_series(raw, "put_call_options")
    # VIX / MOVE / HY / AAII from market data
    vix_s = _ohlc_series("VIX", days=400)
    move_s = _ohlc_series("MOVE", days=400)
    hy_s = _ohlc_series("HYOAS", days=400)
    bull_s = _ohlc_series("AAII_BULL", days=400)
    bear_s = _ohlc_series("AAII_BEAR", days=400)

    # AAII spread series
    bull_map = {r["date"]: r["close"] for r in bull_s}
    bear_map = {r["date"]: r["close"] for r in bear_s}
    aaii_s = []
    for d in sorted(set(bull_map) | set(bear_map)):
        b, e = bull_map.get(d), bear_map.get(d)
        if b is None or e is None:
            continue
        aaii_s.append({"date": d, "close": round(float(b) - float(e), 2)})

    def _val_series(series: list[dict], field: str = "close"):
        return [{"date": r["date"], "value": r.get(field)} for r in series]

    vix_cur, vix_prev = _series_lookup(_val_series(vix_s), asof_d)
    # Prefer CNN component VIX raw if market VIX missing for that day
    if vix_cur is None:
        cnn_vix = _cnn_component_series(raw, "market_volatility_vix")
        vix_cur, vix_prev = _series_lookup(cnn_vix, asof_d)

    move_cur, move_prev = _series_lookup(_val_series(move_s), asof_d)
    pcr_cur, pcr_prev = _series_lookup(pcr_hist, asof_d)
    hy_cur, hy_prev = _series_lookup(_val_series(hy_s), asof_d)
    cnn_cur, cnn_prev = _series_lookup(cnn_hist, asof_d)
    aaii_cur, aaii_prev = _series_lookup(_val_series(aaii_s), asof_d)

    def _num(row):
        if not row:
            return None
        try:
            return float(row.get("value"))
        except (TypeError, ValueError):
            return None

    vix_v, vix_p = _num(vix_cur), _num(vix_prev)
    move_v, move_p = _num(move_cur), _num(move_prev)
    pcr_v, pcr_p = _num(pcr_cur), _num(pcr_prev)
    hy_v = _num(hy_cur)
    hy_bp = None if hy_v is None else round(hy_v * 100, 1)  # 2.7 → 270bp
    hy_p = _num(hy_prev)
    hy_bp_p = None if hy_p is None else round(hy_p * 100, 1)
    cnn_v, cnn_p = _num(cnn_cur), _num(cnn_prev)
    aaii_v, aaii_p = _num(aaii_cur), _num(aaii_prev)

    def _delta(cur, prev):
        if cur is None or prev is None:
            return None
        return round(cur - prev, 4)

    def _delta_txt(d, digits=1, suffix=""):
        if d is None:
            return None
        sign = "+" if d > 0 else ""
        return f"{sign}{d:.{digits}f}{suffix}"

    cards = [
        _card(
            key="vix",
            title="VIX 恐慌指数",
            value=None if vix_v is None else round(vix_v, 2),
            display="—" if vix_v is None else f"{vix_v:.1f}",
            delta=_delta(vix_v, vix_p),
            delta_display=_delta_txt(_delta(vix_v, vix_p), 1),
            fear=_fear_from_vix(vix_v),
            band=_band_vix(vix_v),
            hint="数值越高=预期未来30天波动越剧烈。<15 平静 / 15–20 正常 / 20–30 紧张 / >30 恐慌。股市「体温计」，常滞后于实际下跌，偏反应性。",
        ),
        _card(
            key="move",
            title="MOVE 债市波动率",
            value=None if move_v is None else round(move_v, 1),
            display="—" if move_v is None else f"{move_v:.0f}",
            delta=_delta(move_v, move_p),
            delta_display=_delta_txt(_delta(move_v, move_p), 0),
            fear=_fear_from_move(move_v),
            band=_band_move(move_v),
            hint="债市版 VIX，衡量美债隐含波动率。<80 平静 / 80–100 正常 / 100–130 紧张 / >130 恐慌。常先于 VIX 抬升，是更早的系统性压力预警。",
        ),
        _card(
            key="put_call",
            title="Put/Call 比率",
            value=None if pcr_v is None else round(pcr_v, 2),
            display="—" if pcr_v is None else f"{pcr_v:.2f}",
            delta=_delta(pcr_v, pcr_p),
            delta_display=_delta_txt(_delta(pcr_v, pcr_p), 2),
            fear=_fear_from_pcr(pcr_v),
            band=_band_pcr(pcr_v),
            hint="看跌成交量/看涨成交量，越高=对冲或做空需求越强。<0.7 贪婪 / 0.7–1.0 中性 / >1.0 恐惧 / >1.2 极度恐惧。真金白银仓位，相对诚实。",
        ),
        _card(
            key="hy_spread",
            title="高收益债利差",
            value=hy_bp,
            display="—" if hy_bp is None else f"{hy_bp:.0f}bp",
            delta=None if hy_bp is None or hy_bp_p is None else round(hy_bp - hy_bp_p, 1),
            delta_display=_delta_txt(
                None if hy_bp is None or hy_bp_p is None else hy_bp - hy_bp_p, 0
            ),
            fear=_fear_from_hy_bp(hy_bp),
            band=_band_hy_bp(hy_bp),
            hint="垃圾债收益率减国债，利差越宽=信用风险溢价越高。<350bp 正常 / 350–500bp 紧张 / >500bp 危机。信用市场维度，与 VIX 互补。",
            unit="bp",
        ),
        _card(
            key="cnn",
            title="CNN 恐惧贪婪指数",
            value=None if cnn_v is None else round(cnn_v, 1),
            display="—" if cnn_v is None else f"{cnn_v:.1f}",
            delta=_delta(cnn_v, cnn_p),
            delta_display=_delta_txt(_delta(cnn_v, cnn_p), 1),
            fear=_fear_from_cnn(cnn_v),
            band=_band_cnn(cnn_v),
            hint="7 个子指标加权合成。0–24 极度恐惧 / 25–44 恐惧 / 45–55 中性 / 56–75 贪婪 / 76–100 极度贪婪。偏总览，建议对照分项定位问题出在哪。",
        ),
        _card(
            key="aaii",
            title="AAII 看多-看空差",
            value=None if aaii_v is None else round(aaii_v, 1),
            display="—" if aaii_v is None else f"{aaii_v:.0f}%",
            delta=_delta(aaii_v, aaii_p),
            delta_display=_delta_txt(_delta(aaii_v, aaii_p), 0, "%"),
            fear=_fear_from_aaii(aaii_v),
            band=_band_aaii(aaii_v),
            hint="散户问卷：看多比例减看空比例。±10% 内算正常；转负且扩大=悲观加深。唯一主观问卷类，常被当反向指标——极度悲观有时接近底部。",
            unit="%",
        ),
    ]

    bars = [c["bar"] for c in cards if c.get("bar") is not None]
    panic = round(sum(bars) / len(bars), 1) if bars else None
    composite = None if panic is None else round(100.0 - panic, 1)

    derivative_cards = []
    try:
        derivative_cards = _derivative_cards(asof_d=asof_d)
    except Exception:  # noqa: BLE001
        derivative_cards = []

    calendar = [
        {
            "date": row["date"],
            "value": row["value"],
            "label": row["label"],
            "tone": row["tone"],
            "fear": row["fear"],
        }
        for row in cnn_hist
    ]

    resolved = asof_d.isoformat()
    if cnn_cur and cnn_cur.get("date"):
        resolved = cnn_cur["date"]

    cnn_delta = _delta(cnn_v, cnn_p)
    return {
        "asof": asof_d.isoformat(),
        "resolved_date": resolved,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "cnn": {
            "value": None if cnn_v is None else round(cnn_v, 1),
            "label": _label(cnn_v) if cnn_v is not None else "—",
            "tone": _tone(cnn_v) if cnn_v is not None else "neutral",
            "delta": cnn_delta,
            "delta_display": _delta_txt(cnn_delta, 1),
        },
        "composite": composite,
        "composite_label": _label(composite) if composite is not None else "—",
        "composite_tone": _tone(composite) if composite is not None else "neutral",
        "panic": panic,
        "panic_label": _label_from_fear(panic),
        "cards": cards,
        "derivative_cards": derivative_cards,
        "calendar": calendar,
        "note": "右上主数字=六项等权情绪分（低=恐惧，高=贪婪）；下方衍生品三层独立展示、不计入等权",
        "sources": {
            "cnn": "CNN Fear & Greed",
            "vix": "TVC:VIX",
            "move": "TVC:MOVE",
            "put_call": "CNN put/call 组件",
            "hy": "FRED BAMLH0A0HYM2",
            "aaii": "AAII Bullish − Bearish",
            "es_basis": "CME ES1! − SPX",
            "index_opt": "SPY 近月期权链（Yahoo）",
            "sector_opt": "行业 ETF 近月期权链（Yahoo）",
        },
    }

