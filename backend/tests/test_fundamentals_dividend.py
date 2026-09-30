import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import fundamentals


class FakeTicker:
    def __init__(self, _symbol, timeout=None):
        pass

    def get_modules(self, _modules):
        return {'AAPL': {
            'summaryDetail': {
                'dividendRate': {'raw': 1.04},
                'dividendYield': {'raw': 0.0042},
                'payoutRatio': {'raw': 0.137},
                'exDividendDate': {'raw': 1786147200},
                'fiveYearAvgDividendYield': {'raw': 0.58},
            },
            'financialData': {},
            'defaultKeyStatistics': {},
        }}


class FundamentalsDividendTests(unittest.TestCase):
    def test_yahoo_ttm_includes_dividend_metrics(self):
        fake_module = SimpleNamespace(Ticker=FakeTicker)
        with patch.dict(sys.modules, {'yahooquery': fake_module}):
            result = fundamentals._yahoo_ttm('AAPL')

        self.assertEqual(result['annual_dividend_rate'], 1.04)
        self.assertAlmostEqual(result['dividend_yield'], 0.42)
        self.assertAlmostEqual(result['payout_ratio'], 13.7)
        self.assertEqual(result['ex_dividend_date'], '2026-08-08')
        self.assertEqual(result['five_year_avg_dividend_yield'], 0.58)


if __name__ == '__main__':
    unittest.main()
