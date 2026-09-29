import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from app.services.entry_analysis import analyze, calculate, levels, simulate, clean_bars


def bars(count=400):
    start = datetime.now(timezone.utc) - timedelta(days=count + 2)
    return [{'ts': (start + timedelta(days=i)).timestamp(), 'open': 100 + 3 * math.sin(i / 5),
             'close': 100 + 3 * math.sin(i / 5), 'high': 102 + 3 * math.sin(i / 5),
             'low': 98 + 3 * math.sin(i / 5)} for i in range(count)]


class EntryAnalysisTest(unittest.TestCase):
    def test_horizons_and_price_order(self):
        for horizon in (3, 10, 30, 60):
            data = calculate({'ohlc_bars': bars()}, horizon)
            long, short = data['sides']
            self.assertLess(long['stop'], long['entry'])
            self.assertLess(long['entry'], long['target'])
            self.assertGreater(short['stop'], short['entry'])
            self.assertGreater(short['entry'], short['target'])
            self.assertEqual(data['horizon'], horizon)
            self.assertGreaterEqual(long['history']['opportunities'], long['history']['trades'])

    def test_insufficient_and_invalid_bars(self):
        with self.assertRaises(ValueError): calculate({'ohlc_bars': bars(10)})
        with self.assertRaises(ValueError): calculate({'ohlc_bars': bars()}, 1)
        rows = bars(2)
        self.assertEqual(len(clean_bars(rows + [rows[0], {'ts': 0}])), 2)
        today = dict(rows[0], ts=datetime.now(timezone.utc).timestamp())
        self.assertEqual(clean_bars([today]), [])

    def test_simulator_costs_and_conservative_stops(self):
        plan = {'side': 'long', 'entry': 100, 'stop': 95, 'target': 110}
        both = [{'open': 101, 'low': 94, 'high': 111, 'close': 105}]
        trade = simulate(plan, both, 3, 20, 5)
        self.assertEqual(trade['outcome'], '止损')
        self.assertAlmostEqual(trade['return_pct'], -5.2)
        self.assertIsNone(simulate(plan, [dict(both[0], open=94)], 3, 20, 5))
        self.assertIsNone(simulate(plan, [dict(both[0], low=101)], 3, 20, 5))
        short = {'side': 'short', 'entry': 100, 'stop': 105, 'target': 90}
        future = [{'open': 100, 'high': 102, 'low': 98, 'close': 99}]
        self.assertLess(simulate(short, future, 3, 20, 5)['return_pct'], simulate(short, future, 3, 0, 0)['return_pct'])

    def test_walk_forward_uses_only_pre_entry_bars(self):
        rows = bars(100)
        original = levels
        samples = []
        def capture(sample, horizon):
            samples.append(sample[-1]['ts'])
            return original(sample, horizon)
        with patch('app.services.entry_analysis.levels', side_effect=capture):
            calculate({'ohlc_bars': rows}, 3)
        expected = [rows[end - 1]['ts'] for end in range(40, len(rows) - 3 + 1, 4)]
        self.assertEqual(samples[1:1+len(expected)], expected)

    def test_trending_market_blocks_countertrend(self):
        rows = bars(60)
        for i, row in enumerate(rows):
            row.update(open=100+i, close=100+i, high=102+i, low=98+i)
        data = levels(rows, 10)
        self.assertEqual(data['trend'], '上升')
        self.assertFalse(data['sides'][1]['eligible'])

    def test_analyze_requests_ten_year_history(self):
        with patch('app.services.ohlc.fetch_daily_history', return_value={'ohlc_bars': bars(), 'source': 'test'}) as fetch:
            result = analyze('AAPL', 10, 20, 5)
        fetch.assert_called_once_with('AAPL', '10y')
        self.assertEqual(result['source'], 'test')
