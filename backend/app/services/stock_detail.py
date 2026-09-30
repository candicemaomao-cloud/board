"""Per-symbol supplementary data. Failures are surfaced by each section independently."""
from datetime import datetime, timezone
import math
import time
import httpx
from app.services.quotes import HEADERS


_ratings_cache = {}
_RATINGS_TTL_SECONDS = 60 * 60


def _clean_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _rating_label(value):
    original = str(value or '').strip()
    key = original.casefold().replace('-', ' ').replace('_', ' ')
    buy = ('strong buy', 'buy', 'outperform', 'overweight', 'positive', 'accumulate', 'add')
    sell = ('strong sell', 'sell', 'underperform', 'underweight', 'negative', 'reduce')
    hold = ('hold', 'neutral', 'equal weight', 'market perform', 'sector perform',
            'peer perform', 'in line', 'mixed')
    if any(term in key for term in buy):
        return '买入 / 看多', 'buy'
    if any(term in key for term in sell):
        return '卖出 / 看空', 'sell'
    if any(term in key for term in hold):
        return '观望 / 中性', 'hold'
    return original or '未分类', 'other'


def _rating_action(value, target_action=None):
    key = str(value or '').casefold()
    target = str(target_action or '').casefold()
    if key in ('up', 'upgrade') or 'raise' in target:
        return '上调'
    if key in ('down', 'downgrade') or 'lower' in target:
        return '下调'
    if key in ('init', 'initiated') or 'initiat' in target:
        return '首次覆盖'
    if key in ('main', 'reit', 'reiterate') or 'maintain' in target:
        return '维持'
    return str(value or target_action or '').strip()


def _is_major_firm(firm):
    key = str(firm or '').casefold()
    names = ('jpmorgan', 'jp morgan', 'morgan stanley', 'goldman sachs', 'b of a',
             'bank of america', 'citigroup', 'citi', 'ubs', 'barclays', 'wells fargo',
             'deutsche bank', 'hsbc', 'jefferies', 'evercore', 'bernstein', 'mizuho', 'td cowen')
    return any(name in key for name in names)


def _frame_records(frame):
    if frame is None or getattr(frame, 'empty', True):
        return []
    data = frame.reset_index()
    return data.to_dict(orient='records')


def _recommendation_summary(frame):
    records = _frame_records(frame)
    if not records:
        return None
    row = next((item for item in records if str(item.get('period', '')).casefold() == '0m'), records[0])
    counts = {key: int(_clean_number(row.get(key)) or 0) for key in
              ('strongBuy', 'buy', 'hold', 'sell', 'strongSell')}
    total = sum(counts.values())
    if not total:
        return None
    average = (counts['strongBuy'] + counts['buy'] * 2 + counts['hold'] * 3 +
               counts['sell'] * 4 + counts['strongSell'] * 5) / total
    if average <= 1.5:
        label, tone = '强力买入', 'buy'
    elif average <= 2.5:
        label, tone = '买入', 'buy'
    elif average <= 3.5:
        label, tone = '观望', 'hold'
    elif average <= 4.5:
        label, tone = '卖出', 'sell'
    else:
        label, tone = '强力卖出', 'sell'
    return {'period': row.get('period'), 'counts': counts, 'total': total,
            'label': label, 'tone': tone, 'average_score': round(average, 2)}


def ratings(symbol: str) -> dict:
    """Return recent institution ratings. Empty/failed sources intentionally produce available=false."""
    symbol = symbol.strip().upper()
    cached = _ratings_cache.get(symbol)
    if cached and time.monotonic() - cached[0] < _RATINGS_TTL_SECONDS:
        return cached[1]

    import yfinance as yf
    ticker = yf.Ticker(symbol)
    upgrades = summary_frame = None
    targets = {}
    try:
        upgrades = ticker.get_upgrades_downgrades()
    except Exception:
        pass
    try:
        summary_frame = ticker.get_recommendations_summary()
    except Exception:
        pass
    try:
        targets = ticker.get_analyst_price_targets() or {}
    except Exception:
        pass

    rows = _frame_records(upgrades)
    rows.sort(key=lambda item: str(item.get('GradeDate') or item.get('index') or ''), reverse=True)
    items, seen = [], set()
    for row in rows:
        firm = str(row.get('Firm') or '').strip()
        grade = str(row.get('ToGrade') or '').strip()
        if not firm or not grade or firm.casefold() in seen:
            continue
        seen.add(firm.casefold())
        date_value = row.get('GradeDate') or row.get('index')
        if hasattr(date_value, 'isoformat'):
            date_value = date_value.isoformat()
        label, tone = _rating_label(grade)
        firm_key = firm.casefold().replace('.', '').replace(' ', '')
        items.append({
            'date': str(date_value or '')[:10], 'firm': firm, 'rating': grade,
            'rating_label': label, 'tone': tone, 'from_rating': str(row.get('FromGrade') or '').strip(),
            'action': _rating_action(row.get('Action'), row.get('priceTargetAction')),
            'current_target': _clean_number(row.get('currentPriceTarget')),
            'prior_target': _clean_number(row.get('priorPriceTarget')),
            'is_major': _is_major_firm(firm),
            'is_featured': firm_key in ('jpmorgan', 'jpmorgansecurities'),
        })
        if len(items) >= 30:
            break

    target_data = {key: _clean_number(targets.get(key)) for key in
                   ('current', 'low', 'high', 'mean', 'median')}
    summary = _recommendation_summary(summary_frame)
    action_summary = {'upgrade': 0, 'downgrade': 0, 'maintain': 0, 'initiated': 0, 'other': 0}
    action_keys = {'上调': 'upgrade', '下调': 'downgrade', '维持': 'maintain', '首次覆盖': 'initiated'}
    for item in items:
        action_summary[action_keys.get(item['action'], 'other')] += 1
    action_summary['total'] = len(items)
    available = bool(items or summary or any(value is not None for value in target_data.values()))
    result = {
        'available': available, 'items': items,
        'major_items': [item for item in items if item['is_major']],
        'summary': summary, 'action_summary': action_summary, 'targets': target_data,
        'source': 'Yahoo Finance · 机构评级可能延迟，请以机构最新报告为准',
        'fetched_at': datetime.now(timezone.utc).isoformat(),
    }
    _ratings_cache[symbol] = (time.monotonic(), result)
    return result


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
