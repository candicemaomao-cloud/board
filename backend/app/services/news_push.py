"""重大新闻推送（虚拟币 / 美股）：轮询 X（Nitter 镜像）/ Google News / 快讯站，打分判定大新闻后推给所选推送人。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.http_outbound import http_client
from app.models import CryptoNewsPush, DailyWatch, StockNewsPush
from app.services.crypto_news import _parse_rss
from app.services.notify import _pack_results, send_all
from app.services.translate import translate_en_zh

log = logging.getLogger("news_push")
CN = ZoneInfo("Asia/Shanghai")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

NITTER_HOSTS = [
    "https://nitter.net",
    "https://nitter.tiekoetter.com",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://xcancel.com",
]

WINDOW_SEC = 90 * 60
MAX_PUSH_PER_SCAN = 5
SIMILAR = 0.45
SCORE_LEVELS = {4: "灵敏", 6: "标准", 8: "只推特大"}

BREAKING = re.compile(r"breaking|just in|快讯|突发", re.I)
AMOUNT = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s?(trillion|billion|bn|b|million|m)\b", re.I)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
STOP = {
    "the", "and", "for", "with", "that", "this", "from", "after", "into", "over", "amid", "its", "has", "have",
    "will", "are", "was", "says", "said", "new", "just", "breaking", "crypto", "news", "stock", "stocks",
}


def _google(query: str, zh: bool = False) -> str:
    if zh:
        return f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    return f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"


@dataclass
class Profile:
    key: str
    label: str
    model: type
    x_defaults: list[str]
    feeds: list[tuple[str, str, str]]
    critical: list[str]
    major: list[str]
    test_text: str
    extra_feeds: Callable[[], list[tuple[str, str, str]]] | None = None
    bonus: Callable[[str], tuple[int, list[str]]] | None = None
    crit_re: list[re.Pattern] = field(default_factory=list)
    major_re: list[re.Pattern] = field(default_factory=list)

    def __post_init__(self):
        self.crit_re = [re.compile(p, re.I) for p in self.critical]
        self.major_re = [re.compile(p, re.I) for p in self.major]


CRYPTO = Profile(
    key="crypto",
    label="币圈重大新闻",
    model=CryptoNewsPush,
    x_defaults=["WatcherGuru", "whale_alert", "tier10k", "WuBlockchain", "BitcoinMagazine", "CoinDesk"],
    feeds=[
        ("google", "谷歌新闻", _google("(bitcoin OR crypto OR ethereum OR stablecoin OR solana) when:1h")),
        ("google", "谷歌新闻·中文", _google("(比特币 OR 加密货币 OR 以太坊 OR 稳定币) when:1h", zh=True)),
        ("flash", "Watcher.Guru", "https://watcher.guru/news/feed"),
        ("flash", "The Block", "https://www.theblock.co/rss.xml"),
        ("flash", "Decrypt", "https://decrypt.co/feed"),
        ("flash", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("flash", "Cointelegraph", "https://cointelegraph.com/rss"),
    ],
    critical=[
        r"hack(ed|er|s)?", r"exploit(ed|s)?", r"drain(ed|s)?", r"stolen", r"bankrupt(cy)?", r"insolven(t|cy)",
        r"(halts?|suspends?|pauses?) withdrawals?", r"strategic (bitcoin |crypto )?reserve", r"emergency",
        r"rate (cut|hike)s?", r"all[- ]time high", r"\bath\b", r"crash(es|ed)?", r"plunge[sd]?", r"delist(s|ed|ing)?",
        r"arrest(ed|s)?", r"indict(ed|ment)", r"\bban(s|ned)?\b", r"collapse[sd]?", r"depeg(ged|s)?",
        r"approv(es|ed|al)", r"executive order",
        r"被盗", r"黑客", r"破产", r"暂停提[现币]", r"历史新高", r"暴跌", r"降息", r"加息", r"批准", r"脱锚", r"逮捕", r"战略储备",
    ],
    major=[
        r"\bsec\b", r"\betfs?\b", r"blackrock", r"\bfed\b", r"fomc", r"powell", r"trump", r"liquidat(ed|ion|ions)",
        r"lawsuit", r"\bsues?\b", r"binance", r"coinbase", r"tether", r"\busdc\b", r"stablecoin (bill|act|law)",
        r"microstrategy", r"saylor", r"record", r"surge[sd]?", r"soar(s|ed)?", r"tumble[sd]?", r"sell-?off",
        r"cftc", r"treasury", r"genius act", r"clarity act",
        r"监管", r"清算", r"美联储", r"特朗普", r"诉讼", r"起诉", r"大涨", r"暴涨", r"抛售",
    ],
    test_text="币圈重大新闻推送：测试\n收到这条说明推送已接通，开启轮询后会定时扫描 X / Google 等源。",
)

_SECTOR_ETFS = {"SOXX", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "SPY", "QQQ"}
_watch_cache: dict[str, tuple[float, list[str]]] = {}


def _watch_symbols() -> list[str]:
    hit = _watch_cache.get("w")
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    from app.database import SessionLocal

    syms: list[str] = []
    try:
        with SessionLocal() as db:
            for s in db.scalars(select(DailyWatch.symbol)):
                s = (s or "").strip().upper()
                if s and re.fullmatch(r"[A-Z.]{1,6}", s) and s not in syms:
                    syms.append(s)
    except Exception:  # noqa: BLE001
        syms = []
    _watch_cache["w"] = (time.time(), syms)
    return syms


def _stock_extra_feeds() -> list[tuple[str, str, str]]:
    syms = [s for s in _watch_symbols() if s not in _SECTOR_ETFS][:15]
    if not syms:
        return []
    return [("google", "谷歌新闻·自选", _google(f"({' OR '.join(syms)}) stock when:1h"))]


def _stock_bonus(text: str) -> tuple[int, list[str]]:
    syms = [s for s in _watch_symbols() if s not in _SECTOR_ETFS]
    hits = [s for s in syms if re.search(rf"(?<![A-Za-z])\$?{re.escape(s)}(?![A-Za-z])", text)]
    if not hits:
        return 0, []
    return 2, [f"自选 {'/'.join(hits[:3])}"]


STOCK = Profile(
    key="stock",
    label="美股重大新闻",
    model=StockNewsPush,
    x_defaults=["DeItaone", "FirstSquawk", "LiveSquawk", "unusual_whales", "KobeissiLetter", "zerohedge"],
    feeds=[
        ("google", "谷歌新闻", _google("(stock market OR \"S&P 500\" OR Nasdaq OR \"Dow Jones\" OR Fed OR earnings) when:1h")),
        ("google", "谷歌新闻·中文", _google("(美股 OR 纳斯达克 OR 标普 OR 美联储 OR 财报) when:1h", zh=True)),
        ("flash", "CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("flash", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        ("flash", "WSJ 市场", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
        ("flash", "Yahoo 财经", "https://finance.yahoo.com/news/rssindex"),
        ("flash", "Benzinga", "https://www.benzinga.com/feed"),
        ("flash", "Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
        ("flash", "美联储", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ],
    critical=[
        r"rate (cut|hike)s?", r"(cuts?|raises?|hikes?) (interest )?rates?", r"emergency", r"circuit breaker",
        r"trading (halt|halted|suspended)", r"halts? trading", r"bankrupt(cy)?", r"chapter 11", r"default(s|ed)?",
        r"tariffs?", r"sanctions?", r"\bwar\b", r"invasion", r"missile", r"airstrikes?", r"crash(es|ed)?",
        r"plunge[sd]?", r"plummet(s|ed)?", r"(cuts?|slashes|lowers?) (its )?(guidance|forecast|outlook)",
        r"(raises?|hikes?) (its )?(guidance|forecast|outlook)", r"export (controls?|curbs?|ban)",
        r"antitrust", r"\bdoj\b", r"(steps? down|resigns?|ousted)", r"government shutdown", r"downgrade[sd]? .*(credit|rating)",
        r"(acquire|acquisition|merger|to buy|buyout)", r"all[- ]time high", r"record high",
        r"降息", r"加息", r"熔断", r"停牌", r"破产", r"关税", r"制裁", r"战争", r"并购", r"收购", r"下调.*指引", r"上调.*指引", r"历史新高", r"暴跌",
    ],
    major=[
        r"\bfed\b", r"fomc", r"powell", r"\bcpi\b", r"\bpce\b", r"payrolls?", r"jobs report", r"jobless claims",
        r"\bgdp\b", r"inflation", r"treasury yields?", r"trump", r"white house", r"\bsec\b", r"probe", r"investigation",
        r"earnings", r"(beats?|miss(es)?) (estimates|expectations)", r"(upgrade|downgrade)[sd]?", r"layoffs?",
        r"recall", r"buyback", r"split", r"surge[sd]?", r"soar(s|ed)?", r"tumble[sd]?", r"sell-?off", r"rall(y|ies)",
        r"nvidia", r"apple", r"tesla", r"microsoft", r"\bopec\b", r"\bchina\b",
        r"美联储", r"鲍威尔", r"通胀", r"非农", r"财报", r"特朗普", r"裁员", r"回购", r"大涨", r"暴涨", r"抛售", r"调查",
    ],
    test_text="美股重大新闻推送：测试\n收到这条说明推送已接通，开启轮询后会定时扫描 X / Google / CNBC 等源。",
    extra_feeds=_stock_extra_feeds,
    bonus=_stock_bonus,
)

PROFILES = {"crypto": CRYPTO, "stock": STOCK}

_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()
_tick_lock = threading.Lock()
_host_state = {"host": None, "dead_until": 0.0}


class NewsPushError(Exception):
    pass


def _cached(key: str, ttl: int, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _ids(raw) -> list[int]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    return [int(x) for x in data if isinstance(x, int) or str(x).isdigit()]


def _json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except Exception:
        return default


def _accounts(profile: Profile, row) -> list[str]:
    raw = (row.x_accounts or "").strip()
    if not raw:
        return list(profile.x_defaults)
    out = []
    for part in re.split(r"[,\s，]+", raw):
        part = part.strip().lstrip("@")
        if part and part not in out:
            out.append(part)
    return out[:20]


def _clean_title(title: str) -> str:
    text = re.sub(r"\s+-\s+[^-]{2,40}$", "", title or "")
    text = re.sub(r"^(RT by @\w+:|R to @\w+:)\s*", "", text)
    return text.strip()


def _tokens(text: str) -> set[str]:
    text = (text or "").lower()
    words = {w for w in re.findall(r"[a-z0-9$]{3,}", text) if w not in STOP}
    han = re.sub(r"[^\u4e00-\u9fff]", "", text)
    words |= {han[i : i + 2] for i in range(len(han) - 1)}
    return words


def _similar(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= SIMILAR


def _key(tokens: set[str]) -> str:
    return hashlib.sha1(" ".join(sorted(tokens)[:16]).encode()).hexdigest()[:16]


def _fetch_rss(client: httpx.Client, url: str, source: str) -> list[dict]:
    res = client.get(url)
    if res.status_code >= 400:
        raise NewsPushError(f"HTTP {res.status_code}")
    items = _parse_rss(res.text, source)
    if not items and "<rss" not in res.text[:500] and "<feed" not in res.text[:500]:
        raise NewsPushError("非 RSS 响应")
    return items


def _fetch_web(client: httpx.Client, feeds: list[tuple[str, str, str]]) -> tuple[list[dict], dict]:
    items: list[dict] = []
    status: dict[str, dict] = {}
    for kind, name, url in feeds:
        try:
            got = _fetch_rss(client, url, name)
            for it in got:
                it["kind"] = kind
            items.extend(got)
            status[name] = {"ok": True, "count": len(got)}
        except Exception as exc:  # noqa: BLE001
            status[name] = {"ok": False, "error": str(exc)[:80]}
    return items, status


def _x_link(url: str, account: str) -> str:
    try:
        path = urlparse(url).path.split("#")[0]
        return f"https://x.com{path}" if path else f"https://x.com/{account}"
    except Exception:
        return f"https://x.com/{account}"


def _x_url(host: str, account: str) -> str:
    return f"{host.rstrip('/')}/{account}/rss"


def _fetch_x(client: httpx.Client, accounts: list[str]) -> tuple[list[dict], dict]:
    items: list[dict] = []
    status: dict[str, dict] = {}
    direct = [a for a in accounts if a.startswith("http")]
    handles = [a for a in accounts if not a.startswith("http")]

    for url in direct:
        label = f"X·{urlparse(url).path.rstrip('/').split('/')[-1] or url}"
        try:
            got = _fetch_rss(client, url, label)
            for it in got:
                it["kind"] = "x"
            items.extend(got)
            status[label] = {"ok": True, "count": len(got)}
        except Exception as exc:  # noqa: BLE001
            status[label] = {"ok": False, "error": str(exc)[:80]}

    if not handles:
        return items, status
    if time.time() < _host_state["dead_until"]:
        status["X（Nitter 镜像）"] = {"ok": False, "error": "镜像均不可达，10 分钟后重试"}
        return items, status

    hosts = [_host_state["host"]] if _host_state["host"] else []
    hosts += [h for h in NITTER_HOSTS if h not in hosts]
    host = None
    first = handles[0]
    for candidate in hosts:
        try:
            got = _fetch_rss(client, _x_url(candidate, first), f"X@{first}")
            if got:
                host = candidate
                for it in got:
                    it["kind"] = "x"
                    it["url"] = _x_link(it.get("url") or "", first)
                items.extend(got)
                status[f"X@{first}"] = {"ok": True, "count": len(got)}
                break
        except Exception:  # noqa: BLE001
            continue
    if not host:
        _host_state["host"] = None
        _host_state["dead_until"] = time.time() + 600
        status["X（Nitter 镜像）"] = {"ok": False, "error": "镜像均不可达，可在账号里填 RSSHub 等完整 RSS 地址"}
        return items, status

    _host_state["host"] = host
    for account in handles[1:]:
        try:
            got = _fetch_rss(client, _x_url(host, account), f"X@{account}")
            for it in got:
                it["kind"] = "x"
                it["url"] = _x_link(it.get("url") or "", account)
            items.extend(got)
            status[f"X@{account}"] = {"ok": True, "count": len(got)}
        except Exception as exc:  # noqa: BLE001
            status[f"X@{account}"] = {"ok": False, "error": str(exc)[:80]}
    return items, status


def collect(profile: Profile, accounts: list[str]) -> dict:
    feeds = list(profile.feeds) + (profile.extra_feeds() if profile.extra_feeds else [])

    def fetch():
        with http_client(timeout=8.0, headers=HEADERS, follow_redirects=True) as client:
            web, web_status = _fetch_web(client, feeds)
            x_items, x_status = _fetch_x(client, accounts)
        return {"items": x_items + web, "sources": {**x_status, **web_status}, "at": time.time()}

    key = f"npush:{profile.key}:" + ",".join(accounts) + "|" + ",".join(f[1] for f in feeds)
    return _cached(key, 120, fetch)


def score_text(profile: Profile, text: str) -> tuple[int, list[str]]:
    reasons: list[str] = []
    kw = 0
    for rx in profile.crit_re:
        m = rx.search(text)
        if m:
            kw += 3
            reasons.append(m.group(0))
    for rx in profile.major_re:
        m = rx.search(text)
        if m:
            kw += 2
            reasons.append(m.group(0))
    score = min(kw, 8)
    for m in AMOUNT.finditer(text):
        num = float(m.group(1))
        unit = m.group(2).lower()
        if unit in {"trillion", "billion", "bn", "b"}:
            score += 3
            reasons.append(m.group(0))
            break
        if num >= 100:
            score += 2
            reasons.append(m.group(0))
            break
        if num >= 10:
            score += 1
    for m in PERCENT.finditer(text):
        val = float(m.group(1))
        if val >= 20:
            score += 3
            reasons.append(m.group(0))
            break
        if val >= 10:
            score += 2
            reasons.append(m.group(0))
            break
    if BREAKING.search(text):
        score += 2
        reasons.append("BREAKING")
    if profile.bonus:
        extra, why = profile.bonus(text)
        score += extra
        reasons.extend(why)
    return score, reasons


def _clusters(profile: Profile, items: list[dict]) -> list[dict]:
    cut = time.time() - WINDOW_SEC
    fresh = [it for it in items if (it.get("ts") or 0) >= cut]
    fresh.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    clusters: list[dict] = []
    for it in fresh:
        title = _clean_title(it.get("title") or "")
        if not title:
            continue
        toks = _tokens(title)
        target = next((c for c in clusters if _similar(c["tokens"], toks)), None)
        if target:
            target["sources"].add(it.get("source") or "")
            target["tokens"] |= toks
            if it.get("kind") == "x" and target["lead"].get("kind") != "x":
                target["lead"] = {**it, "title": title}
            continue
        clusters.append({"lead": {**it, "title": title}, "tokens": toks, "sources": {it.get("source") or ""}})
    out = []
    for c in clusters:
        lead = c["lead"]
        score, reasons = score_text(profile, f"{lead.get('title')} {lead.get('summary') or ''}")
        extra = min(len(c["sources"]) - 1, 3)
        if extra > 0:
            score += extra
            reasons.append(f"{len(c['sources'])} 个源同报")
        out.append(
            {
                "key": _key(_tokens(lead.get("title") or "")),
                "title_en": lead.get("title"),
                "url": lead.get("url"),
                "ts": lead.get("ts"),
                "date": lead.get("date"),
                "kind": lead.get("kind"),
                "sources": sorted(s for s in c["sources"] if s),
                "score": score,
                "reasons": reasons[:6],
                "tokens": c["tokens"],
            }
        )
    out.sort(key=lambda x: (x["score"], x.get("ts") or 0), reverse=True)
    return out


def _format(profile: Profile, item: dict) -> str:
    when = datetime.fromtimestamp(item["ts"], CN).strftime("%m-%d %H:%M") if item.get("ts") else "—"
    lines = [f"{profile.label}（评分 {item['score']}）", item.get("title") or item.get("title_en") or ""]
    if item.get("title_en") and item.get("title_en") != item.get("title"):
        lines.append(item["title_en"])
    lines.append(f"来源：{'、'.join(item.get('sources') or []) or '—'}")
    if item.get("reasons"):
        lines.append(f"命中：{'、'.join(item['reasons'])}")
    lines.append(f"时间：{when} 北京")
    if item.get("url"):
        lines.append(item["url"])
    return "\n".join(lines)


def get_or_create(db: Session, profile: Profile, user_id: int):
    model = profile.model
    row = db.scalar(select(model).where(model.user_id == user_id))
    if row:
        return row
    row = model(user_id=user_id, x_accounts=",".join(profile.x_defaults))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _aware(dt: datetime | None) -> datetime | None:
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def to_out(profile: Profile, row) -> dict:
    checked = _aware(row.last_checked_at)
    next_ts = int(checked.timestamp()) + int(row.interval_sec or 300) if checked and row.enabled else None
    return {
        "enabled": bool(row.enabled),
        "recipient_ids": _ids(row.recipient_ids),
        "interval_sec": int(row.interval_sec or 300),
        "min_score": int(row.min_score or 6),
        "score_levels": [{"value": k, "label": v} for k, v in SCORE_LEVELS.items()],
        "x_accounts": _accounts(profile, row),
        "feeds": [name for _, name, _ in profile.feeds],
        "history": _json(row.history, [])[:30],
        "sources": _json(row.last_sources, {}),
        "last_error": row.last_error,
        "last_checked_at": checked.isoformat() if checked else None,
        "next_check_ts": next_ts,
    }


def save(db: Session, profile: Profile, user_id: int, data: dict):
    row = get_or_create(db, profile, user_id)
    if data.get("recipient_ids") is not None:
        row.recipient_ids = json.dumps([int(x) for x in data["recipient_ids"]])
    if data.get("interval_sec") is not None:
        row.interval_sec = max(60, min(int(data["interval_sec"]), 3600))
    if data.get("min_score") is not None:
        row.min_score = max(2, min(int(data["min_score"]), 15))
    if data.get("x_accounts") is not None:
        raw = data["x_accounts"]
        if isinstance(raw, list):
            raw = ",".join(str(x) for x in raw)
        row.x_accounts = str(raw)[:2000]
    if data.get("enabled") is not None:
        on = bool(data["enabled"])
        if on and not _ids(row.recipient_ids):
            raise NewsPushError("请先选推送人再开启轮询")
        if on and not row.enabled:
            row.last_checked_at = None
        row.enabled = 1 if on else 0
    db.commit()
    db.refresh(row)
    return row


def scan(db: Session, profile: Profile, row, *, push: bool) -> dict:
    data = collect(profile, _accounts(profile, row))
    clusters = _clusters(profile, data["items"])
    seen = _json(row.seen_keys, [])
    history = _json(row.history, [])
    recent_tokens = [_tokens(h.get("title_en") or "") for h in history[:40]]
    threshold = int(row.min_score or 6)
    rids = _ids(row.recipient_ids)

    fresh_big = []
    for c in clusters:
        if c["score"] < threshold:
            continue
        if c["key"] in seen or any(_similar(c["tokens"], t) for t in recent_tokens):
            c["pushed_before"] = True
            continue
        fresh_big.append(c)

    pushed = []
    errors = []
    if push and rids:
        for c in fresh_big[:MAX_PUSH_PER_SCAN]:
            try:
                c["title"] = translate_en_zh(c["title_en"] or "")
            except Exception:  # noqa: BLE001
                c["title"] = c["title_en"]
            packed = _pack_results(send_all(db, _format(profile, c), recipient_ids=rids))
            entry = {
                "title": c.get("title"),
                "title_en": c.get("title_en"),
                "url": c.get("url"),
                "sources": c.get("sources"),
                "score": c.get("score"),
                "reasons": c.get("reasons"),
                "news_ts": c.get("ts"),
                "at": datetime.now(timezone.utc).isoformat(),
                "ok": packed["ok"],
                "error": packed.get("error"),
            }
            history.insert(0, entry)
            recent_tokens.insert(0, c["tokens"])
            seen.insert(0, c["key"])
            pushed.append(entry)
            if not packed["ok"]:
                errors.append(packed.get("error") or "推送失败")

    row.seen_keys = json.dumps(seen[:400])
    row.history = json.dumps(history[:60], ensure_ascii=False)
    row.last_sources = json.dumps(data["sources"], ensure_ascii=False)
    row.last_checked_at = datetime.now(timezone.utc)
    bad = [k for k, v in data["sources"].items() if not v.get("ok")]
    if errors:
        row.last_error = "；".join(errors)[:255]
    elif len(bad) == len(data["sources"]):
        row.last_error = "所有新闻源都不可达"
    else:
        row.last_error = None
    db.commit()
    db.refresh(row)

    return {
        "config": to_out(profile, row),
        "candidates": [{k: v for k, v in c.items() if k != "tokens"} for c in clusters[:20]],
        "pushed": pushed,
        "threshold": threshold,
    }


def send_test(db: Session, profile: Profile, row, recipient_ids: list[int] | None = None) -> dict:
    rids = [int(x) for x in recipient_ids] if recipient_ids else _ids(row.recipient_ids)
    if not rids:
        raise NewsPushError("请先选推送人")
    return _pack_results(send_all(db, profile.test_text, recipient_ids=rids))


def tick_due() -> dict:
    from app.database import SessionLocal

    if not _tick_lock.acquire(blocking=False):
        return {"ran": 0}
    ran = 0
    try:
        with SessionLocal() as db:
            now = time.time()
            for profile in PROFILES.values():
                model = profile.model
                for row in list(db.scalars(select(model).where(model.enabled == 1))):
                    checked = _aware(row.last_checked_at)
                    if checked and now - checked.timestamp() < int(row.interval_sec or 300):
                        continue
                    try:
                        scan(db, profile, row, push=True)
                        ran += 1
                    except Exception as exc:  # noqa: BLE001
                        log.exception("news push scan failed profile=%s user=%s", profile.key, row.user_id)
                        row.last_error = str(exc)[:255]
                        row.last_checked_at = datetime.now(timezone.utc)
                        db.commit()
    finally:
        _tick_lock.release()
    return {"ran": ran}
