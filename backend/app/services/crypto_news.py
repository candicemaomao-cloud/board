"""币圈新闻摘要与重大事件（RSS + 中文翻译 + 情绪）。"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx

from app.services.crypto_market import CryptoMarketError, _cached
from app.services.translate import translate_items, translate_source

HEADERS = {"User-Agent": "PnlBoard/1.0", "Accept": "application/rss+xml, application/xml, text/xml, */*"}

FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("GoogleNews", "https://news.google.com/rss/search?q=cryptocurrency+OR+bitcoin+OR+ethereum+ETF&hl=zh-CN&gl=US&ceid=US:zh-Hans"),
]

EVENT_RULES = [
    ("监管政策", ("sec", "cftc", "监管", "regulation", "ban", "lawsuit", "合规", "政策")),
    ("ETF 动态", ("etf", "blackrock", "灰度", "grayscale", "批准", "approval", "流入", "inflow")),
    ("黑客事件", ("hack", "exploit", "breach", "被盗", "攻击", "漏洞", "rug")),
    ("宏观/市场", ("fed", "利率", "cpi", "liquidation", "暴跌", "暴涨", "record")),
]

SENTIMENT_META = {
    "bearish": {"label": "偏空", "score": -1},
    "neutral": {"label": "中性", "score": 0},
    "bullish": {"label": "偏多", "score": 1},
}

BEARISH_KEYS = [
    "crash", "plunge", "hack", "exploit", "breach", "ban", "lawsuit", "selloff", "tumble",
    "bearish", "fear", "liquidation", "outflow", "crackdown", "fraud", "scam", "rug",
    "暴跌", "崩盘", "黑客", "被盗", "攻击", "禁止", "监管打压", "抛售", "清算", "流出", "偏空", "利空",
]
BULLISH_KEYS = [
    "surge", "soar", "rally", "bullish", "approval", "inflow", "record high", "breakthrough",
    "all-time high", "adoption", "etf approved", "上涨", "突破", "批准", "流入", "反弹", "创新高",
    "偏多", "利好",
]


def _strip(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss(xml_text: str, source: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for item in root.findall(".//item"):
        title = _strip(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        desc = _strip(item.findtext("description") or "")
        pub = item.findtext("pubDate") or ""
        ts = None
        try:
            dt = parsedate_to_datetime(pub)
            ts = int(dt.timestamp())
        except Exception:  # noqa: BLE001
            ts = None
        if not title:
            continue
        items.append(
            {
                "title": title[:200],
                "summary": (desc or title)[:280],
                "url": link,
                "source": source,
                "ts": ts,
                "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else None,
            }
        )
    return items


def _classify(title: str, summary: str) -> list[str]:
    blob = f"{title} {summary}".lower()
    tags = []
    for label, keys in EVENT_RULES:
        if any(k.lower() in blob for k in keys):
            tags.append(label)
    return tags or ["一般新闻"]


def _classify_sentiment(item: dict) -> dict:
    blob = f"{item.get('title') or ''} {item.get('summary') or ''} {item.get('title_en') or ''}".lower()
    bear = sum(1 for k in BEARISH_KEYS if k.lower() in blob)
    bull = sum(1 for k in BULLISH_KEYS if k.lower() in blob)
    if bear > bull and bear > 0:
        return {"level": "bearish", **SENTIMENT_META["bearish"]}
    if bull > bear and bull > 0:
        return {"level": "bullish", **SENTIMENT_META["bullish"]}
    return {"level": "neutral", **SENTIMENT_META["neutral"]}


def _summarize_sentiment(items: list[dict]) -> dict:
    counts = {"bearish": 0, "neutral": 0, "bullish": 0}
    total = 0
    for item in items:
        level = (item.get("sentiment") or {}).get("level", "neutral")
        counts[level] = counts.get(level, 0) + 1
        total += (item.get("sentiment") or {}).get("score", 0)
    avg = total / len(items) if items else 0.0
    if avg >= 0.35:
        overall = "偏多"
    elif avg <= -0.35:
        overall = "偏空"
    else:
        overall = "中性"
    return {
        "overall": overall,
        "avg_score": round(avg, 2),
        "counts": counts,
        "sample_size": len(items),
    }


def _digest_row(item: dict) -> dict:
    return {
        "title": item.get("title"),
        "title_en": item.get("title_en"),
        "summary": item.get("summary"),
        "tags": item.get("tags"),
        "sentiment": item.get("sentiment"),
        "url": item.get("url"),
        "source": item.get("source"),
        "date": item.get("date"),
    }


def fetch_news(limit: int = 40) -> dict:
    limit = max(10, min(int(limit or 40), 80))

    def fetch():
        collected: list[dict] = []
        errors = []
        with httpx.Client(timeout=15.0, headers=HEADERS, trust_env=False, follow_redirects=True) as client:
            for name, url in FEEDS:
                try:
                    res = client.get(url)
                    if res.status_code >= 400:
                        errors.append(f"{name}:{res.status_code}")
                        continue
                    collected.extend(_parse_rss(res.text, name))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{name}:{exc}")
        seen = set()
        uniq = []
        for row in collected:
            key = (row["title"][:80], row.get("source"))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(row)
        uniq.sort(key=lambda x: x.get("ts") or 0, reverse=True)
        uniq = uniq[:limit]
        if not uniq:
            raise CryptoMarketError("暂无币圈新闻：" + ("；".join(errors) or "源不可达"))
        translate_items(uniq)
        for row in uniq:
            row["source"] = translate_source(row.get("source") or "")
            if row.get("source") == "CoinDesk":
                row["source"] = "CoinDesk"
            elif row.get("source") == "Cointelegraph":
                row["source"] = "Cointelegraph"
            elif row.get("source") == "GoogleNews":
                row["source"] = "谷歌新闻"
            row["tags"] = _classify(row["title"], row.get("summary") or "")
            row["sentiment"] = _classify_sentiment(row)
        day_cut = time.time() - 86400
        week_cut = time.time() - 7 * 86400
        daily = [x for x in uniq if (x.get("ts") or 0) >= day_cut][:8]
        weekly = [x for x in uniq if (x.get("ts") or 0) >= week_cut][:12]
        events = [x for x in uniq if any(t != "一般新闻" for t in x.get("tags") or [])][:20]
        return {
            "items": uniq,
            "daily_digest": [_digest_row(x) for x in daily],
            "weekly_digest": [_digest_row(x) for x in weekly],
            "events": events,
            "sentiment": _summarize_sentiment(uniq),
            "errors": errors,
        }

    return _cached(f"cnews:{limit}", 180, fetch)
