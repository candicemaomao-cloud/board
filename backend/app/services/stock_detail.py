"""Per-symbol supplementary data. Failures are surfaced by each section independently."""
from datetime import datetime, timezone
import httpx
from app.services.quotes import HEADERS


def _news_date(value):
    from email.utils import parsedate_to_datetime
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            parsed = parsedate_to_datetime(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _rss_news(content, channel):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(content)
    items = []
    for row in root.findall('./channel/item'):
        title = (row.findtext('title') or '').strip()
        publisher = (row.findtext('source') or '').strip()
        if publisher and title.endswith(' - ' + publisher):
            title = title[:-(len(publisher) + 3)]
        published = _news_date(row.findtext('pubDate'))
        items.append({'title': title, 'url': row.findtext('link') or '',
                      'source': publisher or channel, 'channel': channel,
                      'published_at': published.isoformat() if published else None})
    return items


def _fetch_news_source(symbol, channel):
    with httpx.Client(timeout=15, headers=HEADERS, follow_redirects=True) as client:
        if channel == 'Yahoo Finance':
            response = client.get('https://query1.finance.yahoo.com/v1/finance/search',
                                  params={'q': symbol, 'quotesCount': 0, 'newsCount': 15})
            response.raise_for_status()
            items = []
            for row in response.json().get('news', []):
                if symbol not in [s.upper() for s in row.get('relatedTickers', [])]:
                    continue
                published = _news_date(row.get('providerPublishTime'))
                items.append({'title': row.get('title'), 'url': row.get('link'),
                              'source': row.get('publisher') or channel, 'channel': channel,
                              'published_at': published.isoformat() if published else None})
            return items
        response = client.get('https://news.google.com/rss/search',
                              params={'q': f'"{symbol}" stock when:14d', 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})
        response.raise_for_status()
        return _rss_news(response.content, channel)


def news(symbol: str) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    import re
    from urllib.parse import urlsplit
    symbol = symbol.strip().upper()
    channels = ['Yahoo Finance', 'Google News']
    items, failed, succeeded = [], [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {channel: pool.submit(_fetch_news_source, symbol, channel) for channel in channels}
        for channel, future in futures.items():
            try:
                items.extend(future.result())
                succeeded.append(channel)
            except Exception:
                failed.append(channel)
    if not succeeded:
        raise ValueError('新闻来源暂时均不可用')
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)
    result, seen = [], set()
    for item in sorted(items, key=lambda r: r.get('published_at') or '', reverse=True):
        url = item.get('url') or ''
        title = (item.get('title') or '').strip()
        parsed_url = urlsplit(url)
        if not title or parsed_url.scheme not in ('http', 'https') or not parsed_url.netloc:
            continue
        try:
            published = datetime.fromisoformat(item.get('published_at') or '')
        except ValueError:
            continue
        if not cutoff <= published <= now + timedelta(hours=1):
            continue
        key = re.sub(r'[^\w]', '', title.casefold())
        if key in seen or url in seen:
            continue
        seen.update([key, url])
        result.append(item)
    return {'items': result[:15], 'source': ' / '.join(succeeded), 'unavailable_sources': failed,
            'lookback_days': 30, 'fetched_at': now.isoformat()}


def events(symbol: str) -> dict:
    from yahooquery import Ticker
    raw = Ticker(symbol, timeout=20).calendar_events
    data = raw.get(symbol, {})
    if not isinstance(data, dict):
        raise ValueError('事件数据源暂不可用')
    items = []
    for value in (data.get('earnings') or {}).get('earningsDate', []):
        items.append({'title': '预计财报日期', 'date': str(value)[:10]})
    for key, title in [('exDividendDate', '除息日'), ('dividendDate', '派息日')]:
        value = data.get(key)
        if value:
            if isinstance(value, (int, float)):
                value = datetime.fromtimestamp(value, timezone.utc).isoformat()
            items.append({'title': title, 'date': str(value)[:10]})
    today = datetime.now(timezone.utc).date().isoformat()
    return {'items': sorted([i for i in items if i['date'] >= today], key=lambda i: i['date']),
            'source': 'Yahoo Finance · 日期以公司公告为准'}
