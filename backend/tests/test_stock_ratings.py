import unittest
from unittest.mock import patch

import pandas as pd

from app.services import stock_detail


class FakeTicker:
    def __init__(self, _symbol):
        pass

    def get_upgrades_downgrades(self):
        return pd.DataFrame([
            {'Firm': 'JP Morgan', 'ToGrade': 'Overweight', 'FromGrade': 'Neutral', 'Action': 'up',
             'currentPriceTarget': 240, 'priorPriceTarget': 210},
            {'Firm': 'JP Morgan', 'ToGrade': 'Neutral', 'FromGrade': '', 'Action': 'main'},
            {'Firm': 'Small Research', 'ToGrade': 'Hold', 'FromGrade': 'Buy', 'Action': 'down'},
        ], index=pd.to_datetime(['2026-09-29', '2025-01-01', '2026-09-28'])).rename_axis('GradeDate')

    def get_recommendations_summary(self):
        return pd.DataFrame([{
            'period': '0m', 'strongBuy': 4, 'buy': 6, 'hold': 2, 'sell': 0, 'strongSell': 0,
        }])

    def get_analyst_price_targets(self):
        return {'current': 200, 'low': 180, 'high': 260, 'mean': 225, 'median': 220}


class StockRatingsTests(unittest.TestCase):
    def setUp(self):
        stock_detail._ratings_cache.clear()

    @patch('yfinance.Ticker', FakeTicker)
    def test_combines_latest_institution_rating_summary_and_targets(self):
        result = stock_detail.ratings('aapl')

        self.assertTrue(result['available'])
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(result['items'][0]['firm'], 'JP Morgan')
        self.assertEqual(result['items'][0]['rating_label'], '买入 / 看多')
        self.assertEqual(result['items'][0]['action'], '上调')
        self.assertTrue(result['items'][0]['is_featured'])
        self.assertEqual(result['summary']['label'], '买入')
        self.assertEqual(result['targets']['mean'], 225)
        self.assertEqual(result['action_summary']['upgrade'], 1)
        self.assertEqual(result['action_summary']['downgrade'], 1)
        self.assertEqual(result['action_summary']['total'], 2)

    @patch('yfinance.Ticker')
    def test_empty_sources_hide_section(self, ticker):
        instance = ticker.return_value
        instance.get_upgrades_downgrades.return_value = pd.DataFrame()
        instance.get_recommendations_summary.return_value = pd.DataFrame()
        instance.get_analyst_price_targets.return_value = {}

        result = stock_detail.ratings('NONE')

        self.assertFalse(result['available'])
        self.assertEqual(result['items'], [])


if __name__ == '__main__':
    unittest.main()
