from __future__ import annotations

import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from time import time
from xml.etree import ElementTree as ET

import httpx

from app.services.econ_calendar import build_econ_calendar
from app.services.translate import translate_items

HEADERS = {"User-Agent": "PnlBoard/1.0 (personal trading dashboard)"}
CACHE_TTL = 10 * 60

FEEDS = {
    "fed_monetary": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "fed_speeches": "https://www.federalreserve.gov/feeds/speeches.xml",
    "yahoo": "https://finance.yahoo.com/news/rssindex",
}

# Official FOMC dates published by the Fed (star = SEP / press conference projections).
FOMC_MEETINGS = [
    ("2026-01-27", "2026-01-28", False),
    ("2026-03-17", "2026-03-18", True),
    ("2026-04-28", "2026-04-29", False),
    ("2026-06-16", "2026-06-17", True),
    ("2026-07-28", "2026-07-29", False),
    ("2026-09-15", "2026-09-16", True),
    ("2026-10-27", "2026-10-28", False),
    ("2026-12-08", "2026-12-09", True),
    ("2027-01-26", "2027-01-27", False),
    ("2027-03-16", "2027-03-17", True),
    ("2027-04-27", "2027-04-28", False),
    ("2027-06-08", "2027-06-09", True),
    ("2027-07-27", "2027-07-28", False),
    ("2027-09-14", "2027-09-15", True),
    ("2027-10-26", "2027-10-27", False),
    ("2027-12-07", "2027-12-08", True),
]

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, builder):
    hit = _cache.get(key)
    if hit and time() - hit[0] < CACHE_TTL:
        return hit[1]
    value = builder()
    _cache[key] = (time(), value)
    return value


def _parse_when(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    seconds = int((now - dt).total_seconds())
    if seconds < 0:
        days = max(1, -seconds // 86400)
        return f"{days} 天后"
    if seconds < 3600:
        return f"{max(1, seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


# 新闻分类：雅虎那个 rssindex 源本身是一条不分类的大杂烩标题流（"市场新闻"），
# 没有任何自带的主题标签，所以只能凭标题/摘要里的关键词猜——不是精确分类，
# 就是个"大致分个类方便你扫一眼"的粗筛，不追求完美。每条只归一类（按下面
# TOPIC_ORDER 的顺序，先匹配到哪个算哪个），避免同一条在好几个分类里重复出现。
# 中英文关键词都配了：title_en 是原始英文标题，title/summary 翻译成中文之后
# 关键词经常也就没了原文里的样子，所以两边都要查。
TOPIC_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "科技": {
        "en": [
            "tech", "technology", "artificial intelligence", " ai ", "chip", "semiconductor",
            "software", "cloud computing", "cybersecurity", "robot", "nvidia", "apple",
            "microsoft", "google", "alphabet", "meta platforms", "openai", "quantum",
            "satellite", "space x", "spacex",
        ],
        "zh": [
            "科技", "人工智能", "芯片", "半导体", "软件", "云计算", "网络安全", "机器人",
            "英伟达", "苹果公司", "微软", "谷歌", "量子", "卫星", "太空",
        ],
    },
    "医疗": {
        "en": [
            "health", "medical", "medicine", " drug", "vaccine", "biotech", "pharma",
            "fda ", "hospital", "disease", "clinical trial", "cancer",
        ],
        "zh": [
            "医疗", "医药", "疫苗", "药物", "制药", "生物科技", "医院", "疾病", "临床", "癌症", "健康",
        ],
    },
    "能源": {
        "en": ["crude oil", " oil ", "opec", "natural gas", "pipeline", "solar power", "renewable energy", "nuclear power"],
        "zh": ["石油", "原油", "天然气", "欧佩克", "太阳能", "可再生能源", "核能", "能源"],
    },
    "金融": {
        "en": [
            "federal reserve", " fed ", "interest rate", "rate cut", "rate hike", "treasury yield",
            "wall street", " ipo", "merger", "acquisition", "earnings", "dividend",
            "stock market", "crypto", "bitcoin", " bank",
        ],
        "zh": [
            "美联储", "利率", "降息", "加息", "国债收益率", "华尔街", "首次公开募股", "并购",
            "财报", "股息", "股市", "加密货币", "比特币", "银行",
        ],
    },
    "消费": {
        "en": ["retail sales", "consumer spending", "restaurant", "airline", "housing market", "home sales", "electric vehicle"],
        "zh": ["零售", "消费者", "餐厅", "航空", "房地产", "房屋销售", "电动车", "汽车"],
    },
    "宏观政策": {
        "en": ["inflation", " cpi", " ppi", " gdp", "unemployment", "jobs report", "tariff", "trade war", "government shutdown", "debt ceiling"],
        "zh": ["通胀", "失业", "就业报告", "关税", "贸易战", "政府停摆", "债务上限", "大选"],
    },
}
TOPIC_ORDER = ["科技", "医疗", "能源", "金融", "消费", "宏观政策"]
NEWS_TOPICS_LIMIT = 5  # 每个分类最多展示几条


def _classify_topic(item: dict) -> str:
    title_en = (item.get("title_en") or "").lower()
    zh_blob = f"{item.get('title') or ''} {item.get('summary') or ''}"
    for topic in TOPIC_ORDER:
        kw = TOPIC_KEYWORDS[topic]
        if any(k in title_en for k in kw["en"]) or any(k in zh_blob for k in kw["zh"]):
            return topic
    return "其他"


def _group_news_by_topic(items: list[dict], limit_per_topic: int = NEWS_TOPICS_LIMIT) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for item in items:
        topic = _classify_topic(item)
        buckets.setdefault(topic, []).append(item)
    order = TOPIC_ORDER + ["其他"]
    return [
        {"topic": topic, "items": buckets[topic][:limit_per_topic]}
        for topic in order
        if buckets.get(topic)
    ]


# 情绪：跟分类一样，纯关键词命中，不是真正的 NLP 情感分析——反讽、否定句式
# （"否认财务造假传闻"这种辟谣新闻会被"造假"命中判成利空，实际是利好）、
# 混合信号（"下调目标价但维持买入评级"）都处理不了，只能当一个粗略的方向参考，
# 别当真正的情感分析结果用。五档打分，跟 unified_strategy.py 里给单只股票用的
# classify_news_headline() 是同一个五档体系（severe_negative ~ very_positive），
# 但关键词表是给"大盘/宏观新闻"重新配的，不是那个给"单只股票财报/丑闻新闻"用的表
# ——两边领域不一样，硬用同一套关键词会经常判错。
SENTIMENT_LEVELS = {
    "severe_negative": {"label": "强烈利空", "score": -2},
    "mild_negative": {"label": "偏空", "score": -1},
    "neutral": {"label": "中性", "score": 0},
    "mild_positive": {"label": "偏多", "score": 1},
    "very_positive": {"label": "强烈利好", "score": 2},
}
SENTIMENT_KEYWORDS = {
    "severe_negative": {
        "en": ["crash", "plunge", "recession", "collapse", "crisis", "bankruptcy", "selloff", "tumble", "meltdown", "panic"],
        "zh": ["暴跌", "崩盘", "衰退", "崩溃", "危机", "破产", "抛售", "恐慌", "暴雷"],
    },
    "very_positive": {
        "en": ["surge", "soar", "record high", "breakthrough", "blowout", "all-time high", "rally"],
        "zh": ["飙升", "暴涨", "创历史新高", "创新高", "突破", "大幅反弹"],
    },
    "mild_negative": {
        "en": ["fall", "drop", "decline", "downgrade", "concern", "warning", " miss", "slowdown", "tariff", "layoff", "cut jobs"],
        "zh": ["下跌", "下滑", "下调", "担忧", "警告", "不及预期", "放缓", "关税", "裁员"],
    },
    "mild_positive": {
        "en": ["rise", "gain", "growth", "beat", "upgrade", "recovery", "expansion", "approval", "optimis"],
        "zh": ["上涨", "上升", "增长", "超预期", "上调", "复苏", "扩张", "获批", "乐观"],
    },
}
SENTIMENT_PRIORITY = ["severe_negative", "very_positive", "mild_negative", "mild_positive"]


def _classify_sentiment(item: dict) -> dict:
    title_en = (item.get("title_en") or "").lower()
    zh_blob = f"{item.get('title') or ''} {item.get('summary') or ''}"
    for level in SENTIMENT_PRIORITY:
        kw = SENTIMENT_KEYWORDS[level]
        if any(k in title_en for k in kw["en"]) or any(k in zh_blob for k in kw["zh"]):
            return {"level": level, **SENTIMENT_LEVELS[level]}
    return {"level": "neutral", **SENTIMENT_LEVELS["neutral"]}


def _summarize_sentiment(items: list[dict]) -> dict:
    counts = {lvl: 0 for lvl in SENTIMENT_LEVELS}
    total_score = 0
    for item in items:
        level = (item.get("sentiment") or {}).get("level", "neutral")
        counts[level] = counts.get(level, 0) + 1
        total_score += (item.get("sentiment") or {}).get("score", 0)
    avg = total_score / len(items) if items else 0.0
    if avg >= 0.8:
        overall = "偏乐观"
    elif avg <= -0.8:
        overall = "偏悲观"
    else:
        overall = "中性/分歧"
    return {"overall": overall, "avg_score": round(avg, 2), "counts": counts, "sample_size": len(items)}


def _classify_fed(title: str) -> str:
    lower = title.lower()
    if "fomc statement" in lower or "issues fomc" in lower:
        return "FOMC 决议"
    if "minutes of the federal open market committee" in lower or "fomc minutes" in lower:
        return "FOMC 纪要"
    if "economic projections" in lower or "projection" in lower:
        return "点阵图 / 经济预测"
    if "discount rate" in lower:
        return "贴现率会议纪要"
    if "speech" in lower or "," in title:
        return "官员讲话"
    return "货币政策"


def _items_from_rss(xml_text: str, source: str, limit: int) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for node in root.findall("./channel/item")[:limit]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", "", node.findtext("description") or "").strip()
        published = _parse_when(node.findtext("pubDate"))
        extra_source = node.findtext("source") or source
        kind = _classify_fed(title) if "Fed" in source or source.startswith("宏观因素") else "市场"
        items.append(
            {
                "title": title,
                "url": link,
                "summary": desc[:220],
                "source": extra_source,
                "kind": kind,
                "published": published.isoformat() if published else None,
                "ago": _ago(published),
            }
        )
    return items


def _fetch_rss(url: str, source: str, limit: int) -> list[dict]:
    try:
        with httpx.Client(timeout=10.0, headers=HEADERS, follow_redirects=True) as client:
            res = client.get(url)
            res.raise_for_status()
        return _items_from_rss(res.text, source, limit)
    except Exception:
        return []


def _fomc_calendar(today: date | None = None) -> dict:
    today = today or date.today()
    meetings = []
    for start_s, end_s, sep in FOMC_MEETINGS:
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)
        if end < today:
            status = "已结束"
        elif start <= today <= end:
            status = "进行中"
        else:
            status = "即将召开"
        meetings.append(
            {
                "start": start_s,
                "end": end_s,
                "sep": sep,
                "label": f"{start.month}/{start.day}–{end.month}/{end.day}",
                "year": end.year,
                "status": status,
                "days_until": (start - today).days,
            }
        )
    upcoming = [m for m in meetings if m["status"] != "已结束"]
    next_meeting = upcoming[0] if upcoming else None
    return {"next": next_meeting, "upcoming": upcoming[:4], "all": meetings}


def build_market() -> dict:
    def _load():
        calendar = _fomc_calendar()
        releases = translate_items(_fetch_rss(FEEDS["fed_monetary"], "宏观因素", 12))
        speeches = translate_items(_fetch_rss(FEEDS["fed_speeches"], "宏观因素讲话", 8))
        # 分类是从这一批里挑的，池子越大、每个分类能凑到的条数才越稳——16条太少，
        # 经常某个分类一条都凑不到，加到 40 条基本能保证科技/医疗这些常见分类
        # 每次都有东西可看（雅虎这个 rssindex 源本身不分类，只能靠关键词硬筛）。
        #
        # 雅虎这个 feed 返回的顺序不是严格按时间倒序的（实测里几小时前的条目跟
        # 几分钟前的混在一起），只取前 N 条不排序的话，"最新"其实是随缘的——
        # 这就是之前"新闻老是显示一天前"的真正原因，不是数据源本身不更新。
        # 这里显式按 published 倒序排一遍，没有时间戳的排最后，保证真正最新的
        # 排在前面。
        news = translate_items(_fetch_rss(FEEDS["yahoo"], "雅虎财经", 40))
        news.sort(key=lambda x: x.get("published") or "", reverse=True)
        for item in news:
            item["sentiment"] = _classify_sentiment(item)
        return {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "fomc": calendar,
            "econ_calendar": build_econ_calendar(),
            "fed_releases": releases,
            "fed_speeches": speeches,
            "news": news,
            "news_by_topic": _group_news_by_topic(news),
            "news_sentiment": _summarize_sentiment(news),
        }

    return _cached("market_zh", _load)
