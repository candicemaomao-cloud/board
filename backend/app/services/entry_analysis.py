"""Fixed-rule entry zones with chronological, non-overlapping OHLC simulations.
No learned probabilities or claimed optimal prices. All prices are per share.
"""
import math
from datetime import datetime, timezone
from statistics import median
from app.services.screener import _theil_sen

WINDOWS = {3: 40, 10: 60, 30: 90, 60: 120}


def clean_bars(raw):
    result = {}
    today = datetime.now(timezone.utc).date()
    for row in raw:
        try:
            bar = {k: float(row[k]) for k in ('ts', 'open', 'high', 'low', 'close')}
            if not all(math.isfinite(v) for v in bar.values()):
                continue
            if min(bar[k] for k in ('open', 'high', 'low', 'close')) <= 0:
                continue
            if bar['low'] > min(bar['open'], bar['close']) or bar['high'] < max(bar['open'], bar['close']):
                continue
            # Conservative: exclude today's potentially incomplete daily candle.
            day = datetime.fromtimestamp(bar['ts'], timezone.utc).date()
            if day >= today:
                continue
            result[day] = bar
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    return [result[d] for d in sorted(result)]


def levels(bars, horizon):
    sample = bars[-WINDOWS[horizon]:]
    closes = [b['close'] for b in sample]
    slope, intercept = _theil_sen(list(range(len(closes))), closes)
    center = intercept + slope * (len(closes) - 1)
    residuals = [v - (intercept + slope * i) for i, v in enumerate(closes)]
    residual_mid = median(residuals)
    sigma = median([abs(v - residual_mid) for v in residuals]) * 1.4826
    trs = [max(b['high'] - b['low'], abs(b['high'] - sample[i-1]['close']), abs(b['low'] - sample[i-1]['close'])) for i, b in enumerate(sample) if i > 0]
    atr = sum(trs[-14:]) / len(trs[-14:])
    if atr <= 0:
        raise ValueError('价格波动不足，无法生成有效止损区间')
    band = max(sigma * 1.5, atr)
    trend_strength = slope * 20 / atr
    trend = '上升' if trend_strength > 1 else '下降' if trend_strength < -1 else '震荡'
    last = closes[-1]
    support = min(b['low'] for b in sample[-20:])
    resistance = max(b['high'] for b in sample[-20:])
    long_entry = min(center - band, last - .25 * atr)
    short_entry = max(center + band, last + .25 * atr)
    sides = []
    for side, entry, allowed in [('long', long_entry, trend != '下降'), ('short', short_entry, trend != '上升')]:
        direction = 1 if side == 'long' else -1
        stop = entry - direction * atr * 1.5
        target = entry + direction * atr * 3
        zone = [entry - .2 * atr, entry + .2 * atr]
        valid = min(stop, target, *zone) > 0
        sides.append({'side': side, 'entry': entry if valid else None, 'zone': zone if valid else None,
                      'stop': stop if valid else None, 'target': target if valid else None,
                      'reward_risk': 2 if valid else None, 'eligible': allowed and valid,
                      'reason': '顺势回调候选' if allowed and trend != '震荡' else '震荡回归候选' if allowed else '与当前趋势相反，观望'})
    return {'trend': trend, 'trend_strength': trend_strength, 'center': center, 'atr': atr,
            'support': support, 'resistance': resistance, 'price': last, 'sides': sides}


def simulate(plan, future, horizon, cost_bps, borrow_pct):
    """One next-session limit entry; gap beyond stop skips entry. Same bar: stop first."""
    entry, stop, target = plan['entry'], plan['stop'], plan['target']
    direction = 1 if plan['side'] == 'long' else -1
    first = future[0]
    if direction == 1:
        if first['open'] <= stop or first['low'] > entry:
            return None
    elif first['open'] >= stop or first['high'] < entry:
        return None
    # No favorable price improvement assumed. Entry-day target ignored without intraday ordering.
    exit_price, holding, outcome = future[-1]['close'], len(future), '到期退出'
    for i, bar in enumerate(future):
        hit_stop = bar['low'] <= stop if direction == 1 else bar['high'] >= stop
        hit_target = bar['high'] >= target if direction == 1 else bar['low'] <= target
        if hit_stop:
            exit_price = min(stop, bar['open']) if direction == 1 else max(stop, bar['open'])
            holding, outcome = i + 1, '止损'; break
        if i > 0 and hit_target:
            exit_price, holding, outcome = target, i + 1, '止盈'; break
    costs = entry * cost_bps / 10000
    if direction == -1:
        costs += entry * borrow_pct / 100 * holding / 252
    pnl = direction * (exit_price - entry) - costs
    return {'return_pct': pnl / entry * 100, 'r': pnl / abs(entry - stop), 'outcome': outcome}


def calculate(raw, horizon=10, cost_bps=20, borrow_pct=5):
    if horizon not in WINDOWS:
        raise ValueError('不支持的交易周期')
    bars = clean_bars(raw.get('ohlc_bars') or [])
    window = WINDOWS[horizon]
    if len(bars) < window:
        raise ValueError(f'需要至少 {window} 根完整日线，目前仅 {len(bars)} 根')
    last_day = datetime.fromtimestamp(bars[-1]['ts'], timezone.utc).date()
    if (datetime.now(timezone.utc).date() - last_day).days > 7:
        raise ValueError('最近完整日线超过 7 天，行情可能停牌或过期，请更新数据后重试')
    current = levels(bars, horizon)
    stats = {}
    for side in ('long', 'short'):
        trades, opportunities = [], 0
        for end in range(window, len(bars) - horizon + 1, horizon + 1):
            try:
                historical = levels(bars[:end], horizon)
            except ValueError:
                continue
            plan = next(p for p in historical['sides'] if p['side'] == side)
            if not plan['eligible']:
                continue
            opportunities += 1
            trade = simulate(plan, bars[end:end+horizon], horizon, cost_bps, borrow_pct)
            if trade:
                trades.append(trade)
        returns = [t['return_pct'] for t in trades]
        stats[side] = {'opportunities': opportunities, 'trades': len(trades),
            'fill_rate': len(trades) / opportunities * 100 if opportunities else None,
            'win_rate': sum(v > 0 for v in returns) / len(returns) * 100 if returns else None,
            'mean_return_pct': sum(returns) / len(returns) if returns else None,
            'mean_r': sum(t['r'] for t in trades) / len(trades) if trades else None,
            'worst_return_pct': min(returns) if returns else None}
    for plan in current['sides']:
        stat = stats[plan['side']]
        plan['history'] = stat
        plan['status'] = '观望' if not plan['eligible'] else '候选价 · 待积累验证' if stat['trades'] < 20 else '历史净收益非正，观望' if stat['mean_return_pct'] <= 0 else '候选，需确认'
    return {**current, 'horizon': horizon, 'window': window, 'bars': len(bars),
            'asof': datetime.fromtimestamp(bars[-1]['ts'], timezone.utc).date().isoformat(),
            'source': raw.get('source'), 'cost_bps': cost_bps, 'borrow_pct': borrow_pct,
            'method': '稳健回归 + 14 日 ATR · 固定规则 v1',
            'limits': ['候选价格不是最优价格保证；回归线不代表内在价值。',
                       '按完整日线计算；候选挂单仅供下一交易日参考，未成交则重新计算。',
                       '历史模拟逐时向前，样本持有区间不重叠；同根日线止盈止损均触及时按止损，入场当日不计止盈。',
                       '模拟为触价限价单，未模拟排队、流动性、停牌或无法借券；实际成交可能不同。',
                       '期权、财务、新闻与事件没有历史快照，未纳入模型或历史统计；财报与突发事件前需重新评估。',
                       '所有参数固定且未经优化；历史胜率不是未来获利概率。']}


def analyze(symbol, horizon, cost_bps, borrow_pct):
    from app.services.ohlc import fetch_daily_history
    return calculate(fetch_daily_history(symbol, '10y'), horizon, cost_bps, borrow_pct)
