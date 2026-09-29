"""独立的导数高级策略。收盘确认、下一根开盘成交，监听与回测共用重放引擎。"""
from datetime import date, datetime, timedelta, timezone

from app.services.ohlc import OhlcError
from app.user_stocks import _new_strategy_probe as base

SPECS = [
    {"key": "advanced_pullback", "label": "回踩后重新启动（关闭用于旧入场对照）", "type": "bool", "default": True},
    {"key": "advanced_adx", "label": "ADX14 下限（0关闭）", "type": "float", "default": 20, "min": 0, "max": 50},
    {"key": "advanced_chase", "label": "距EMA20最大ATR（0关闭）", "type": "float", "default": 1.5, "min": 0, "max": 5},
    {"key": "advanced_cooldown", "label": "离场后冷却K线数", "type": "int", "default": 4, "min": 0, "max": 48},
    {"key": "advanced_setup_bars", "label": "回踩信号有效K线数", "type": "int", "default": 8, "min": 2, "max": 30},
    {"key": "advanced_trail_start", "label": "浮盈达到几R启动跟踪", "type": "float", "default": 1, "min": 0, "max": 5},
    {"key": "advanced_risk", "label": "每笔止损风险占权益%（0固定名义本金）", "type": "float", "default": .5, "min": 0, "max": 2},
    {"key": "advanced_window", "label": "平滑导数窗口", "type": "int", "default": 10, "min": 3, "max": 60},
    {"key": "advanced_slope", "label": "最小斜率 / ATR", "type": "float", "default": .05, "min": 0, "max": 1},
    {"key": "advanced_atr", "label": "交易周期 ATR 窗口", "type": "int", "default": 14, "min": 5, "max": 60},
    {"key": "advanced_stop", "label": "初始止损 ATR 倍数", "type": "float", "default": 2, "min": .5, "max": 10},
    {"key": "advanced_trail", "label": "跟踪止损 ATR 倍数", "type": "float", "default": 3, "min": .5, "max": 10},
    {"key": "advanced_fast", "label": "高周期快 EMA", "type": "int", "default": 20, "min": 2, "max": 100},
    {"key": "advanced_slow", "label": "高周期慢 EMA", "type": "int", "default": 50, "min": 3, "max": 200},
    {"key": "advanced_volume", "label": "成交量 / 前20根均量", "type": "float", "default": 1.2, "min": .1, "max": 10},
    {"key": "advanced_fee", "label": "单边手续费（bp）", "type": "float", "default": 5, "min": 0, "max": 100},
    {"key": "advanced_slip", "label": "单边滑点（bp）", "type": "float", "default": 2, "min": 0, "max": 100},
]
COMMON = {"timeframe", "allow_short", "capital_per_trade", "compound", "backtest_trading_days"}


def parameters(raw):
    return {**{s['key']: s['default'] for s in SPECS}, **raw}


def features(bars, seconds, p):
    """仅完整高周期分组参与 EMA；缺根时不生成高周期数据。"""
    higher = max(3600, seconds * 4) if seconds != 1800 else 3600
    ratio = higher // seconds
    closes, trs, volumes = [], [], []
    bucket_rows = []
    bucket = None
    fast = slow = previous_fast = None
    count = 0
    output = []
    adx = base.adx_series(bars, list(range(len(bars))), 14)
    local_ema = None
    for i, bar in enumerate(bars):
        group = int(bar['ts']) // higher
        if group != bucket:
            bucket, bucket_rows = group, []
        bucket_rows.append(bar)
        if (int(bar['ts']) + seconds) % higher == 0 and len(bucket_rows) == ratio:
            c = bar['close']
            previous_fast = fast
            fast = c if fast is None else fast + 2 / (p['advanced_fast'] + 1) * (c - fast)
            slow = c if slow is None else slow + 2 / (p['advanced_slow'] + 1) * (c - slow)
            count += 1
        closes.append(bar['close'])
        local_ema = bar['close'] if local_ema is None else local_ema + 2 / 21 * (bar['close'] - local_ema)
        trs.append(base.true_range(bars, i))
        n = int(p['advanced_atr'])
        atr = sum(trs[-n:]) / n if len(trs) >= n else None
        w = int(p['advanced_window'])
        slope = None
        if len(closes) >= w and atr and atr > 0:
            ys = closes[-w:]
            mid = (w - 1) / 2
            slope = sum((j - mid) * y for j, y in enumerate(ys)) / sum((j - mid) ** 2 for j in range(w)) / atr
        prior = volumes[-20:]
        volume = float(bar.get('volume') or 0)
        rv = volume / (sum(prior) / 20) if len(prior) == 20 and sum(prior) > 0 else None
        volumes.append(volume)
        direction = 0
        if count >= p['advanced_slow'] and previous_fast is not None:
            if fast > slow and fast > previous_fast:
                direction = 1
            elif fast < slow and fast < previous_fast:
                direction = -1
        signal = 0
        if slope is not None and rv is not None and rv >= p['advanced_volume']:
            if direction == 1 and slope > p['advanced_slope']:
                signal = 1
            elif direction == -1 and slope < -p['advanced_slope'] and p.get('allow_short', True):
                signal = -1
        distance = abs(bar['close'] - local_ema) / atr if atr else None
        reasons = []
        if p['advanced_adx'] and (adx[i] is None or adx[i] < p['advanced_adx']):
            reasons.append('ADX不足')
        if p['advanced_chase'] and (distance is None or distance > p['advanced_chase']):
            reasons.append('距离均线过远')
        if reasons:
            signal = 0
        # 先回踩EMA附近，后续另一根K线收盘越过前一根高/低点才确认重新启动。
        pullback = direction if atr and ((direction == 1 and bar['low'] <= local_ema and bar['close'] >= local_ema - atr)
                                        or (direction == -1 and bar['high'] >= local_ema and bar['close'] <= local_ema + atr)) else 0
        reclaim = (1 if i and bar['close'] > bars[i-1]['high'] and bar['close'] > local_ema else
                   -1 if i and bar['close'] < bars[i-1]['low'] and bar['close'] < local_ema else 0)
        output.append({'slope': slope, 'atr': atr, 'signal': signal, 'trend': direction, 'volume_ratio': rv,
                       'pullback': pullback, 'reclaim': reclaim, 'ema': local_ema, 'adx': adx[i], 'blocked': reasons})
    return output


def replay(bars, seconds, p, start):
    fs = features(bars, seconds, p)
    capital = float(p.get('capital_per_trade', 10000))
    equity = capital
    compound = p.get('compound', True)
    fee, slip = p['advanced_fee'] / 10000, p['advanced_slip'] / 10000
    position = pending = None
    trades, events = [], []
    armed = None
    cooldown_until = -1

    def stamp(ts):
        dt = datetime.fromtimestamp(ts, timezone.utc)
        return dt.date().isoformat(), dt.strftime('%H:%M')

    def event(ts, kind, direction, price):
        day, clock = stamp(ts)
        events.append({'day': day, 'time': clock, 'type': kind, 'direction': direction, 'price': price, 'taken': True})

    def close_position(price, ts, reason):
        nonlocal position, equity, armed, cooldown_until
        side = position['sign']
        fill = price * (1 - side * slip)
        pnl = position['shares'] * (side * (fill - position['entry_price']) - fee * (fill + position['entry_price']))
        equity += pnl
        day, clock = stamp(ts)
        trades.append({**position, 'exit_day': day, 'exit_time': clock, 'exit_price': fill,
                       'pnl': pnl, 'equity_after': equity, 'exit_reason': reason})
        event(ts, '多头反转' if side == 1 else '空头反转', '平多' if side == 1 else '平空', fill)
        events[-1]['reason'] = reason
        position = None
        armed = None
        cooldown_until = i + int(p['advanced_cooldown'])

    for i, (bar, f) in enumerate(zip(bars, fs)):
        ts = int(bar['ts'])
        if datetime.fromtimestamp(ts, timezone.utc).date() < start:
            continue
        if pending:
            if pending['exit'] and position:
                close_position(bar['open'], ts, '趋势失效')
            elif not position and not pending['exit']:
                sign = pending['sign']
                fill = bar['open'] * (1 + sign * slip)
                notional = equity if compound else capital
                risk_distance = pending['atr'] * p['advanced_stop']
                if p['advanced_risk'] > 0:
                    # 以止损损失预算定仓，名义仓位不超过权益；不使用杠杆扩大收益。
                    risk_per_unit = risk_distance + fill * (2 * fee + 2 * slip)
                    notional = min(notional, notional * p['advanced_risk'] / 100 / risk_per_unit * fill)
                if p['advanced_chase'] and abs(bar['open'] - pending.get('reference', bar['open'])) > pending['atr']:
                    notional = 0  # 跳价超过1ATR取消，不追入
                if notional > 0:
                    day, clock = stamp(ts)
                    position = {'sign': sign, 'side': '多' if sign == 1 else '空', 'symbol': p.get('symbol'),
                                'entry_day': day, 'entry_time': clock, 'entry_price': fill,
                                'shares': notional / fill, 'stop_price': fill - sign * pending['atr'] * p['advanced_stop'],
                                'extreme': fill, 'initial_risk': risk_distance, 'mfe': 0, 'mae': 0}
                    event(ts, '买入' if sign == 1 else '卖出/做空', '做多' if sign == 1 else '做空', fill)
            pending = None
        if position:
            hit = base.stop_hit_price(position['side'], position['stop_price'], bar)
            if hit is not None:
                close_position(hit, ts, 'ATR止损/跟踪止损')
                continue
            sign = position['sign']
            favorable = (bar['high'] - position['entry_price']) if sign == 1 else (position['entry_price'] - bar['low'])
            adverse = (bar['low'] - position['entry_price']) if sign == 1 else (position['entry_price'] - bar['high'])
            position['mfe'] = max(position['mfe'], favorable / position['initial_risk'])
            position['mae'] = min(position['mae'], adverse / position['initial_risk'])
            # 本根收盘更新的止损仅在下一根生效，避免利用本根高低点顺序。
            if f['atr'] and position['mfe'] >= p['advanced_trail_start']:
                if sign == 1:
                    position['extreme'] = max(position['extreme'], bar['high'])
                    position['stop_price'] = max(position['stop_price'], position['extreme'] - p['advanced_trail'] * f['atr'])
                else:
                    position['extreme'] = min(position['extreme'], bar['low'])
                    position['stop_price'] = min(position['stop_price'], position['extreme'] + p['advanced_trail'] * f['atr'])
            if f['trend'] == -sign:
                pending = {'exit': True}
        elif i > cooldown_until:
            if armed and (f['trend'] != armed['sign'] or i - armed['index'] > p['advanced_setup_bars']):
                armed = None
            ready = not p['advanced_pullback'] or (armed and armed['sign'] == f['signal'] and f.get('reclaim') == f['signal'])
            if f['signal'] and ready:
                pending = {'exit': False, 'sign': f['signal'], 'atr': f['atr'], 'reference': bar['close']}
                armed = None
            elif f.get('pullback') and armed is None:
                armed = {'sign': f['pullback'], 'index': i}
    if position:
        mark = bars[-1]['close']
        floating = position['shares'] * (position['sign'] * (mark - position['entry_price']) - fee * position['entry_price'])
        trades.append({**position, 'exit_day': None, 'exit_time': None, 'exit_price': None,
                       'pnl': 0, 'unrealized_pnl': floating, 'mark_price': mark, 'exit_reason': '区间结束仍持仓（未实现）'})
    return trades, events, position, pending, fs[-1] if fs else {}


def run_backtest(symbol, range_start=None, range_end=None, **kwargs):
    p = parameters(kwargs)
    p['symbol'] = symbol
    tf = p.get('timeframe', '30m')
    seconds = {'5m': 300, '30m': 1800, '1h': 3600, '1d': 86400}[tf]
    end = range_end or datetime.now(timezone.utc).date()
    start = range_start or end - timedelta(days=int(p.get('backtest_trading_days', 21)) - 1)
    if start > end:
        raise OhlcError('开始日期不能晚于结束日期')
    if p['advanced_fast'] >= p['advanced_slow']:
        raise OhlcError('高周期快 EMA 必须小于慢 EMA')
    higher = 3600 if seconds <= 1800 else seconds * 4
    pad = max(15, int(p['advanced_slow'] * higher / 86400) + 5)
    # 虚拟币高级策略只用币安U本位合约K线，不在失败时切换成其他市场数据。
    from app.services.binance import public_kline_bars_covering
    bars = public_kline_bars_covering(symbol, tf, start=start - timedelta(days=pad), end=end)
    cutoff = min(datetime.now(timezone.utc).timestamp(), datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc).timestamp())
    bars = sorted({int(b['ts']): b for b in bars if int(b['ts']) + seconds <= cutoff}.values(), key=lambda b: b['ts'])
    selected = [b for b in bars if datetime.fromtimestamp(b['ts'], timezone.utc).date() >= start]
    if not selected:
        raise OhlcError('所选区间没有已收盘K线')
    if any(int(bars[i]['ts']) - int(bars[i-1]['ts']) != seconds for i in range(1, len(bars))):
        raise OhlcError('币安合约K线存在缺口，请重新拉取后回测；缺失K线不会按连续行情计算')
    trades, events, position, pending, last = replay(bars, seconds, p, start)
    summary = base.summarize(trades, p.get('capital_per_trade', 10000), p.get('compound', True))
    summary['stop_exits'] = sum(t['exit_reason'] == 'ATR止损/跟踪止损' for t in trades)
    summary['reversal_exits'] = sum(t['exit_reason'] == '趋势失效' for t in trades)
    summary['realized_equity'] = summary['final_equity']
    summary['unrealized_pnl'] = trades[-1].get('unrealized_pnl', 0) if position else 0
    summary['final_equity'] += summary['unrealized_pnl']
    summary['total_return_pct'] = (summary['final_equity'] / p.get('capital_per_trade', 10000) - 1) * 100
    summary['by_side'] = {side: base.summarize([t for t in trades if t['side'] == side], p.get('capital_per_trade', 10000), False)
                          for side in ('多', '空')}
    summary['exit_counts'] = {reason: sum(t['exit_reason'] == reason for t in trades if t['exit_day'])
                              for reason in ('ATR止损/跟踪止损', '趋势失效')}
    first = datetime.fromtimestamp(selected[0]['ts'], timezone.utc).date()
    final = datetime.fromtimestamp(selected[-1]['ts'], timezone.utc).date()
    return {'symbol': symbol, 'config': p, 'summary': summary, 'trades': trades, 'signal_log': events,
            'data_range': {'first_day': str(first), 'last_day': str(final)},
            'effective_range': {'start': str(first), 'end': str(final)}, 'n_days': (final - first).days + 1,
            'daily_report': base.daily_report(events, trades), 'daily_signal_counts': base.daily_signal_counts(events),
            'daily_trade_stats': base.daily_trade_stats(trades), 'bar_log': [], 'shape_predictions': [],
            'warnings': ['V2：24小时连续交易，跨UTC日不重置持仓；初始参数未做收益优化。',
                         '币安U本位合约K线；已计手续费和滑点，未计永续资金费；无杠杆、未模拟强平。',
                         '最终权益含按最新收盘价计算的浮盈亏和入场费，未平仓部分不预扣未来离场成本。']
                        + ([f'数据实际从{first}开始，未覆盖请求起点{start}。'] if first > start else []),
            '_position': position, '_pending': pending, '_last': last, '_bar': selected[-1]}


def current_signal(symbol, **kwargs):
    # 固定起点确保连续轮询不会因每天滑动回放区间而重置跨日持仓。
    start = date.fromisoformat(kwargs.pop('advanced_start', '2026-01-01'))
    result = run_backtest(symbol, start, **kwargs)
    bar, last, position, pending = (result[k] for k in ('_bar', '_last', '_position', '_pending'))
    events = result['signal_log']
    dt = datetime.fromtimestamp(bar['ts'], timezone.utc)
    recent = [e for e in events if e['day'] == dt.date().isoformat()]
    action = '等待趋势与放量确认'
    direction = '观望'
    if position:
        direction, action = ('持多' if position['sign'] == 1 else '持空'), '趋势持仓，ATR止损只向盈利方向移动'
    if pending:
        action = '趋势失效，下一根开盘离场' if pending['exit'] else '入场条件满足，等待下一根开盘确认成交'
        # 收盘确认即通知，不必等下一根完整K线结束；事件键与下一根成交事件一致，避免重复推送。
        seconds = {'5m': 300, '30m': 1800, '1h': 3600, '1d': 86400}[kwargs.get('timeframe', '30m')]
        confirmed = datetime.fromtimestamp(bar['ts'] + seconds, timezone.utc)
        sign = position['sign'] if pending['exit'] else pending['sign']
        kind = ('多头反转' if sign == 1 else '空头反转') if pending['exit'] else ('买入' if sign == 1 else '卖出/做空')
        target = ('平多' if sign == 1 else '平空') if pending['exit'] else ('做多' if sign == 1 else '做空')
        recent.append({'day': confirmed.date().isoformat(), 'time': confirmed.strftime('%H:%M'),
                       'type': kind, 'direction': target, 'price': bar['close'], 'reference_price': True})
        direction = target
        action += '（价格为信号收盘参考价）'
    return {'price': bar['close'], 'asof': dt.strftime('%H:%M'), 'direction': direction, 'action': action,
            'events': recent, 'hit': bool(recent), 'warnings': result['warnings'],
            'snap': {'stop': position['stop_price'] if position and position['sign'] == 1 else None,
                     'stop_short': position['stop_price'] if position and position['sign'] == -1 else None,
                     'dtrend': '涨' if (last.get('slope') or 0) > 0 else '跌',
                     'vol_high': (last.get('volume_ratio') or 0) >= kwargs.get('advanced_volume', 1.2)}}
