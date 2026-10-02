"""未来 N 天将发财报的美股列表（Nasdaq 财报日历 + 报价/板块）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from time import time
from urllib.parse import quote

import httpx

from app.http_outbound import http_client
from app.services.daily_watch import SECTORS
from app.services.fundamentals import NASDAQ_HEADERS, _get, _num

CAL_TTL = 30 * 60
QUOTE_TTL = 60 * 60
_cal_cache: dict[int, tuple[float, dict]] = {}
_quote_cache: dict[str, tuple[float, dict]] = {}

SECTOR_ZH = {
    "technology": "科技",
    "information technology": "科技",
    "semiconductors": "半导体",
    "semiconductor": "半导体",
    "healthcare": "医疗",
    "health care": "医疗",
    "financials": "金融",
    "financial services": "金融",
    "financial": "金融",
    "finance": "金融",
    "banks": "银行",
    "consumer cyclical": "可选消费",
    "consumer discretionary": "可选消费",
    "consumer defensive": "必需消费品",
    "consumer staples": "必需消费品",
    "energy": "能源",
    "industrials": "工业",
    "industrial": "工业",
    "utilities": "公用事业",
    "real estate": "房地产",
    "basic materials": "原材料",
    "materials": "原材料",
    "communication services": "通讯服务",
    "telecommunications": "通讯服务",
    "telecom": "通讯服务",
}


class EarningsStocksError(Exception):
    pass


def _map_sector(en: str | None) -> str:
    if not en:
        return "其他"
    key = str(en).strip().lower()
    zh = SECTOR_ZH.get(key)
    if zh and zh in SECTORS:
        return zh
    for k, v in SECTOR_ZH.items():
        if k in key or key in k:
            return v if v in SECTORS else "其他"
    return "其他"


def _parse_time_label(raw: str | None) -> str:
    t = (raw or "").strip().lower()
    if "before" in t or "pre-market" in t or t == "bmo":
        return "盘前"
    if "after" in t or "after-hours" in t or t == "amc":
        return "盘后"
    if "time-not-supplied" in t or not t:
        return "—"
    return raw or "—"


def _calendar_day(day: date) -> list[dict]:
    try:
        with http_client(timeout=14.0, headers=NASDAQ_HEADERS, follow_redirects=True) as client:
            raw = _get(client, f"/api/calendar/earnings?date={day.isoformat()}")
    except Exception:
        return []
    rows = raw.get("rows") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        if not sym or len(sym) > 8 or any(ch in sym for ch in (".", "/", "^")):
            continue
        out.append(
            {
                "symbol": sym,
                "name": str(row.get("name") or "").strip() or sym,
                "earnings_date": day.isoformat(),
                "time": _parse_time_label(row.get("time")),
                "market_cap_raw": row.get("marketCap"),
                "market_cap": _num(str(row.get("marketCap") or "").replace("$", "")),
                "eps_forecast": row.get("epsForecast"),
                "fiscal_quarter": row.get("fiscalQuarterEnding"),
            }
        )
    return out


def _trading_days(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _fetch_calendar(days: int) -> dict:
    """并行拉未来 N 天财报日历（含今天，跳过周末）。"""
    cache_key = f"future:{days}"
    hit = _cal_cache.get(cache_key)
    now = time()
    if hit and now - hit[0] < CAL_TTL:
        return hit[1]

    today = date.today()
    start = today
    end = today + timedelta(days=days - 1)
    day_list = _trading_days(start, end)
    by_sym: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_calendar_day, d): d for d in day_list}
        for fut in as_completed(futs):
            try:
                rows = fut.result()
            except Exception:
                continue
            for row in rows:
                prev = by_sym.get(row["symbol"])
                # 同一股票多次排期：保留最近将到来的财报日
                if prev is None or row["earnings_date"] <= prev["earnings_date"]:
                    by_sym[row["symbol"]] = row

    items = list(by_sym.values())
    for row in items:
        row.setdefault("price", None)
        row.setdefault("change_pct", None)
        row.setdefault("sector", None)
        row.setdefault("sector_zh", "其他")
        row.setdefault("industry", None)
        try:
            ed = date.fromisoformat(str(row.get("earnings_date") or ""))
            row["days_until"] = (ed - today).days
        except ValueError:
            row["days_until"] = None

    # 按财报日由近到远，同日内按市值
    items.sort(
        key=lambda r: (
            r.get("earnings_date") or "9999-99-99",
            -(float(r.get("market_cap") or 0)),
        ),
    )
    out = {
        "days": days,
        "scope": "future",
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(items),
        "items": items,
        "note": f"Nasdaq 未来财报 {start.isoformat()}～{end.isoformat()}，去重 {len(items)} 只",
        "source": "nasdaq calendar",
    }
    _cal_cache[cache_key] = (now, out)
    return out


def _quote_one(symbol: str) -> dict:
    now = time()
    hit = _quote_cache.get(symbol)
    if hit and now - hit[0] < QUOTE_TTL:
        return hit[1]
    info = {
        "price": None,
        "change_pct": None,
        "sector": None,
        "sector_zh": "其他",
        "industry": None,
    }
    try:
        with http_client(timeout=10.0, headers=NASDAQ_HEADERS, follow_redirects=True) as client:
            # summary 一份同时有 Sector + PreviousClose，少打一轮
            summary = _get(client, f"/api/quote/{quote(symbol, safe='')}/summary?assetclass=stocks")
            sd = summary.get("summaryData") if isinstance(summary, dict) else {}
            if isinstance(sd, dict):
                sector = ((sd.get("Sector") or {}).get("value")) if isinstance(sd.get("Sector"), dict) else None
                industry = ((sd.get("Industry") or {}).get("value")) if isinstance(sd.get("Industry"), dict) else None
                prev = ((sd.get("PreviousClose") or {}).get("value")) if isinstance(sd.get("PreviousClose"), dict) else None
                last = ((sd.get("LastSale") or {}).get("value")) if isinstance(sd.get("LastSale"), dict) else None
                info["sector"] = sector
                info["industry"] = industry
                info["sector_zh"] = _map_sector(sector)
                info["price"] = _num(last) or _num(prev)
    except Exception:
        pass
    _quote_cache[symbol] = (now, info)
    return info


def upcoming_earnings(days: int = 30, enrich: bool = True, max_enrich: int = 60) -> dict:
    days = max(1, min(int(days or 30), 45))
    max_enrich = max(0, min(int(max_enrich or 60), 120))

    base = _fetch_calendar(days)
    # 深拷贝行，避免污染日历缓存
    items = [dict(row) for row in base.get("items") or []]

    enriched_n = 0
    if enrich and items and max_enrich > 0:
        ranked = sorted(items, key=lambda r: float(r.get("market_cap") or 0), reverse=True)[:max_enrich]
        with ThreadPoolExecutor(max_workers=10) as pool:
            futs = {pool.submit(_quote_one, row["symbol"]): row["symbol"] for row in ranked}
            quotes: dict[str, dict] = {}
            for fut in as_completed(futs):
                try:
                    sym = futs[fut]
                    quotes[sym] = fut.result()
                except Exception:
                    continue
        for row in items:
            q = quotes.get(row["symbol"])
            if not q:
                continue
            row["price"] = q.get("price")
            row["change_pct"] = q.get("change_pct")
            row["sector"] = q.get("sector")
            row["sector_zh"] = q.get("sector_zh") or "其他"
            row["industry"] = q.get("industry")
            enriched_n += 1

    note = base.get("note") or ""
    if enrich:
        note = f"{note}；已补行情/板块 {enriched_n} 只（市值优先）"

    return {
        "days": days,
        "scope": "future",
        "from": base.get("from"),
        "to": base.get("to"),
        "count": len(items),
        "enriched": bool(enrich),
        "max_enrich": max_enrich,
        "enriched_count": enriched_n,
        "items": items,
        "note": note,
        "source": "nasdaq calendar",
    }


# 兼容旧名
def recent_earnings(days: int = 30, enrich: bool = True, max_enrich: int = 60) -> dict:
    return upcoming_earnings(days=days, enrich=enrich, max_enrich=max_enrich)
