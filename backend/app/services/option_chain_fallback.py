"""Cboe delayed chain fallback, restricted to the exact requested expiry."""
from datetime import datetime, timedelta
import math
import re
from time import monotonic
from urllib.parse import quote

import httpx

_CACHE = {}


def fetch_cboe_chain(symbol):
    hit = _CACHE.get(symbol)
    if hit and monotonic() - hit[0] < 300:
        return hit[1]
    response = httpx.get(
        f'https://cdn.cboe.com/api/global/delayed_quotes/options/{quote(symbol, safe="")}.json',
        timeout=20, follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    if len(_CACHE) >= 128:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[symbol] = (monotonic(), payload)
    return payload


def cboe_expirations(payload, symbol):
    """Return sorted ISO expiry dates present in a Cboe delayed snapshot."""
    data = (payload or {}).get('data') or {}
    if str(data.get('symbol') or '').upper() != str(symbol or '').upper():
        raise ValueError('备用期权链股票代码不匹配')
    expirations = set()
    for row in data.get('options', []):
        match = re.fullmatch(r'(.+?)(\d{6})[CP](\d{8})', row.get('option', ''))
        if match and match[1].upper() == str(symbol).upper():
            expirations.add(datetime.strptime(match[2], '%y%m%d').date().isoformat())
    if not expirations:
        raise ValueError('备用期权链没有可用到期日')
    return sorted(expirations)


def cboe_expiry(payload, symbol, expiration):
    """Keep one source for spot, quotes, IV and OI; never mix expiry dates."""
    import pandas as pd
    data = payload.get('data') or {}
    if data.get('symbol') != symbol:
        raise ValueError('备用期权链股票代码不匹配')
    timestamp = str(payload.get('timestamp') or '')
    try:
        snapshot_day = datetime.fromisoformat(timestamp).date()
    except ValueError as exc:
        raise ValueError('备用期权链缺少有效时间') from exc
    today = datetime.now().date()
    if not today - timedelta(days=4) <= snapshot_day <= today + timedelta(days=1):
        raise ValueError('备用期权链快照已过期')
    spot = data.get('current_price')
    if spot is None or not math.isfinite(float(spot)) or float(spot) <= 0:
        raise ValueError('备用期权链缺少现价')
    sides = {'C': [], 'P': []}
    for row in data.get('options', []):
        match = re.fullmatch(r'(.+?)(\d{6})([CP])(\d{8})', row.get('option', ''))
        if not match or match[1] != symbol:
            continue
        expiry = datetime.strptime(match[2], '%y%m%d').date().isoformat()
        if expiry != expiration:
            continue
        oi = row.get('open_interest')
        if oi is None or not math.isfinite(float(oi)) or float(oi) < 0:
            continue
        def numeric(key):
            value = row.get(key)
            return float(value) if value is not None and math.isfinite(float(value)) else 0.0
        sides[match[3]].append({
            'contractSymbol': row['option'], 'strike': int(match[4]) / 1000,
            'openInterest': float(oi), 'bid': numeric('bid'), 'ask': numeric('ask'),
            'lastPrice': numeric('last_trade_price'), 'impliedVolatility': numeric('iv'),
        })
    if not sides['C'] or not sides['P']:
        raise ValueError('备用源缺少该到期日完整 Call/Put 数据')
    if sum(r['openInterest'] for rows in sides.values() for r in rows) < 10:
        raise ValueError('备用源同一到期日也没有足够持仓')
    return (pd.DataFrame(sides['C']).sort_values('strike').reset_index(drop=True),
            pd.DataFrame(sides['P']).sort_values('strike').reset_index(drop=True),
            float(spot), timestamp)
