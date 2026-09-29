from __future__ import annotations

import httpx

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PnlBoard/1.0)"}

GLOSSARY = {
    "Federal Reserve issues FOMC statement": "宏观因素发布 FOMC 利率决议",
    "Federal Reserve Board and Federal Open Market Committee release economic projections from the June 16-17 FOMC meeting": "宏观因素与 FOMC 发布 6 月 16–17 日会议经济预测（点阵图）",
    "Federal Reserve Board and Federal Open Market Committee release economic projections from the March 17-18 FOMC meeting": "宏观因素与 FOMC 发布 3 月 17–18 日会议经济预测（点阵图）",
    "Federal Open Market Committee reaffirms its \"Statement on Longer-Run Goals and Monetary Policy Strategy\"": "FOMC 重申《长期目标与货币政策策略》声明",
    "Statement on Longer-Run Goals and Monetary Policy Strategy": "《长期目标与货币政策策略》声明",
}

PHRASES = [
    ("Federal Open Market Committee", "联邦公开市场委员会（FOMC）"),
    ("Federal Reserve Board", "宏观因素理事会"),
    ("Federal Reserve", "宏观因素"),
    ("FOMC statement", "FOMC 利率决议"),
    ("economic projections", "经济预测"),
    ("Press Conference", "新闻发布会"),
    ("Implementation Note", "实施说明"),
    ("discount rate meetings", "贴现率会议"),
    ("discount rate meeting", "贴现率会议"),
    ("Minutes of the Board's", "宏观因素理事会会议纪要："),
    ("Minutes of the Board’s", "宏观因素理事会会议纪要："),
    ("Minutes of the Federal Open Market Committee", "FOMC 会议纪要"),
    ("Speech At the", "讲话，于"),
    ("Speech at the", "讲话，于"),
]

SOURCE_NAMES = {
    "Yahoo Finance": "雅虎财经",
    "TheStreet": "TheStreet",
    "Reuters": "路透社",
    "CNBC": "CNBC",
    "Bloomberg": "彭博",
    "AP": "美联社",
    "Oilprice.com": "OilPrice",
    "MarketWatch": "市场观察",
    "Investing.com": "英为财情",
    "Barron's": "巴伦周刊",
    "Barrons": "巴伦周刊",
}

_memory: dict[str, str] = {}


def _looks_english(text: str) -> bool:
    letters = sum(ch.isascii() and ch.isalpha() for ch in text)
    return letters >= 8


def _apply_phrases(text: str) -> str:
    out = text
    for en, zh in PHRASES:
        out = out.replace(en, zh)
    return out


def _gtx(client: httpx.Client, text: str) -> str | None:
    url = "https://translate.googleapis.com/translate_a/single"
    res = client.get(
        url,
        params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text},
        timeout=8.0,
    )
    res.raise_for_status()
    data = res.json()
    chunks = data[0] if data else []
    translated = "".join(part[0] for part in chunks if part and part[0])
    return translated.strip() or None


def _mymemory(client: httpx.Client, text: str) -> str | None:
    url = "https://api.mymemory.translated.net/get"
    res = client.get(url, params={"q": text[:500], "langpair": "en|zh-CN"}, timeout=8.0)
    res.raise_for_status()
    data = res.json()
    translated = (data.get("responseData") or {}).get("translatedText") or ""
    if not translated or translated.lower() == text.lower():
        return None
    return translated.strip()


def translate_en_zh(text: str, client: httpx.Client | None = None) -> str:
    raw = (text or "").strip()
    if not raw:
        return raw
    if raw in GLOSSARY:
        return GLOSSARY[raw]
    if raw in _memory:
        return _memory[raw]
    if not _looks_english(raw):
        return raw

    phrased = _apply_phrases(raw)
    if not _looks_english(phrased):
        _memory[raw] = phrased
        return phrased

    own = client is None
    http = client or httpx.Client(headers=HEADERS, follow_redirects=True)
    translated = None
    try:
        try:
            translated = _gtx(http, raw)
        except Exception:
            translated = None
        if not translated:
            try:
                translated = _mymemory(http, raw)
            except Exception:
                translated = None
    finally:
        if own:
            http.close()

    result = translated or phrased
    _memory[raw] = result
    return result


def translate_source(name: str) -> str:
    if not name:
        return name
    return SOURCE_NAMES.get(name, name)


def translate_items(items: list[dict]) -> list[dict]:
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        for item in items:
            original = item.get("title") or ""
            item["title_en"] = original
            item["title"] = translate_en_zh(original, client)
            if item.get("summary") and _looks_english(item["summary"]):
                item["summary"] = translate_en_zh(item["summary"], client)
            item["source"] = translate_source(item.get("source") or "")
    return items
