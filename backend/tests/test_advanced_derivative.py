import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from app.user_stocks import _advanced_derivative as advanced


class AdvancedTests(unittest.TestCase):
    def bars(self, n=180):
        ts = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
        return [dict(ts=ts + i * 1800, open=100+i, high=102+i, low=99+i, close=101+i, volume=100+i) for i in range(n)]

    def test_features_are_prefix_invariant(self):
        p = advanced.parameters({})
        bars = self.bars()
        full = advanced.features(bars, 1800, p)
        self.assertEqual(full[:130], advanced.features(bars[:130], 1800, p))
        self.assertEqual(full[98]['trend'], 0)  # only 49 completed hourly candles
        self.assertEqual(full[99]['trend'], 1)

    def test_entry_next_open_and_stop_not_retroactive(self):
        bars = self.bars(3)
        bars[1].update(open=110, high=150, low=109, close=145)
        bars[2].update(open=145, high=146, low=130, close=135)
        fs = [dict(atr=10, signal=1, trend=1, slope=1, volume_ratio=2)] * 3
        p = advanced.parameters({'advanced_fee': 0, 'advanced_slip': 0, 'advanced_pullback': False, 'advanced_risk': 0, 'advanced_chase': 0})
        with patch.object(advanced, 'features', return_value=fs):
            trades, events, *_ = advanced.replay(bars, 1800, p, date(2026, 1, 1))
        self.assertEqual(trades[0]['entry_price'], 110)
        self.assertIsNone(trades[0]['exit_day'])  # raised stop is 120, not applied to prior bar low 109
        self.assertEqual(len(events), 1)

    def test_fee_and_stop_gap(self):
        bars = self.bars(3)
        bars[1].update(open=110, high=111, low=109, close=110)
        bars[2].update(open=80, high=81, low=79, close=80)
        fs = [dict(atr=10, signal=1, trend=1, slope=1, volume_ratio=2)] * 3
        p = advanced.parameters({'advanced_fee': 10, 'advanced_slip': 0, 'capital_per_trade': 1100,
                                 'advanced_pullback': False, 'advanced_risk': 0, 'advanced_chase': 0})
        with patch.object(advanced, 'features', return_value=fs):
            trades, *_ = advanced.replay(bars, 1800, p, date(2026, 1, 1))
        self.assertEqual(trades[0]['exit_price'], 80)
        self.assertAlmostEqual(trades[0]['pnl'], -301.9)

    def test_dispatch_preserves_original(self):
        from types import SimpleNamespace
        from app.services.crypto_custom_strategies import _check_symbol
        row = SimpleNamespace(strategy_key='deriv_advanced', params='{}', created_at=datetime(2026, 1, 1))
        with patch.object(advanced, 'current_signal', return_value={'direction': '观望'}) as call:
            self.assertEqual(_check_symbol(row, 'BTCUSDT')['direction'], '观望')
            self.assertEqual(call.call_args.kwargs['advanced_start'], '2026-01-01')

    def test_requires_prior_pullback_then_later_reclaim(self):
        bars = self.bars(5)
        fs = [dict(atr=10, signal=1, trend=1, slope=1, volume_ratio=2, pullback=0, reclaim=1) for _ in bars]
        fs[1]['pullback'] = 1
        p = advanced.parameters({'advanced_fee': 0, 'advanced_slip': 0})
        with patch.object(advanced, 'features', return_value=fs):
            trades, events, *_ = advanced.replay(bars, 1800, p, date(2026, 1, 1))
        self.assertEqual(events[0]['time'], '01:30')  # pullback 00:30, confirmation 01:00, next open 01:30
        self.assertEqual(trades[0]['entry_price'], bars[3]['open'])

    def test_no_reentry_without_fresh_pullback_after_stop(self):
        bars = self.bars(12)
        fs = [dict(atr=2, signal=1, trend=1, slope=1, volume_ratio=2, pullback=0, reclaim=1) for _ in bars]
        fs[0]['pullback'] = 1
        bars[3].update(open=80, high=82, low=79, close=81)
        with patch.object(advanced, 'features', return_value=fs):
            trades, events, *_ = advanced.replay(bars, 1800, advanced.parameters({}), date(2026, 1, 1))
        self.assertEqual(sum(e['type'] == '买入' for e in events), 1)
        self.assertEqual(len(trades), 1)

    def test_short_pullback_and_cross_midnight(self):
        bars = self.bars(5)
        for b in bars:
            b['ts'] += 23 * 3600
        fs = [dict(atr=10, signal=-1, trend=-1, slope=-1, volume_ratio=2, pullback=0, reclaim=-1) for _ in bars]
        fs[0]['pullback'] = -1
        with patch.object(advanced, 'features', return_value=fs):
            trades, *_ = advanced.replay(bars, 1800, advanced.parameters({}), date(2026, 1, 1))
        self.assertEqual(trades[0]['side'], '空')
        self.assertIsNone(trades[0]['exit_day'])
        self.assertIn('unrealized_pnl', trades[0])

    def test_open_equity_is_included(self):
        bars = self.bars(180)
        with patch('app.services.binance.public_kline_bars_covering', return_value=bars):
            result = advanced.run_backtest('BTCUSDT', date(2026, 1, 2), date(2026, 1, 3),
                                          timeframe='30m', advanced_pullback=False, advanced_volume=.1,
                                          advanced_adx=0, advanced_chase=0)
        s = result['summary']
        self.assertGreater(s['unrealized_pnl'], 0)
        self.assertAlmostEqual(s['final_equity'], s['realized_equity'] + s['unrealized_pnl'])
