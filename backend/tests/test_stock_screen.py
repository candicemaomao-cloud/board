import unittest
from unittest.mock import patch

from app.services import stock_screen


class StockScreenTest(unittest.TestCase):
    def test_number_and_missing_values(self):
        self.assertEqual(stock_screen._number('$1.5B'), 1_500_000_000)
        self.assertEqual(stock_screen._number({'raw': 12.5}), 12.5)
        self.assertIsNone(stock_screen._number(None))
        self.assertFalse(stock_screen._in_range(None, {'min': 0}))

    def test_conditions_do_not_treat_missing_as_zero(self):
        filters = {'ranges': {'pe': {'min': 0, 'max': 20}}, 'free_cash_flow_positive': True}
        self.assertEqual(stock_screen._conditions({'pe': None, 'free_cash_flow_positive': None}, filters), [False, False])
        self.assertEqual(stock_screen._conditions({'pe': 15, 'free_cash_flow_positive': True}, filters), [True, True])

    @patch('app.services.stock_screen._enrich')
    @patch('app.services.stock_screen._base_universe')
    def test_and_prefilters_but_any_keeps_financial_matches(self, base_universe, enrich):
        rows = [
            {'symbol': 'AAA', 'market_cap': 100, 'price': 10, 'sector_zh': '科技'},
            {'symbol': 'BBB', 'market_cap': 10, 'price': 10, 'sector_zh': '金融'},
        ]
        base_universe.return_value = rows
        enrich.side_effect = lambda candidates, advanced=False: [
            {**row, 'pe': 10 if row['symbol'] == 'BBB' else 40} for row in candidates
        ]
        filters = {'ranges': {'market_cap': {'min': 50}, 'pe': {'max': 20}}}
        both = stock_screen.run({'logic': 'all', 'filters': filters, 'max_candidates': 20})
        either = stock_screen.run({'logic': 'any', 'filters': filters, 'max_candidates': 20})
        self.assertEqual(both['items'], [])
        self.assertEqual({row['symbol'] for row in either['items']}, {'AAA', 'BBB'})

    def test_advanced_financial_continuity_and_turnaround(self):
        income = []
        revenues = [80, 90, 100, 110, 120, 140]
        for i, revenue in enumerate(revenues):
            income.append({
                'periodType': '3M', 'TotalRevenue': revenue, 'DilutedEPS': 1 + i * .2,
                'GrossProfit': revenue * (.3 + i * .01), 'OperatingIncome': revenue * (.1 + i * .01),
                'NetIncome': -2 if i == 1 else 5 + i, 'EBIT': 10, 'InterestExpense': 2,
            })
        metrics = stock_screen._advanced_financial_metrics(
            income,
            [{'periodType': '3M', 'FreeCashFlow': 10} for _ in range(6)],
            [{'periodType': '12M', 'FreeCashFlow': 30} for _ in range(3)],
            [{'TotalAssets': 200, 'TotalDebt': 50}],
        )
        self.assertGreaterEqual(metrics['revenue_growth_streak'], 2)
        self.assertTrue(metrics['turned_profitable'])
        self.assertEqual(metrics['fcf_positive_years'], 3)
        self.assertAlmostEqual(metrics['debt_to_assets'], 25)

    def test_excluding_upcoming_earnings(self):
        filters = {
            'advanced_enabled': True, 'earnings_mode': 'exclude',
            'ranges': {'days_to_earnings': {'min': 0, 'max': 14}},
        }
        self.assertEqual(stock_screen._conditions({'days_to_earnings': 7}, filters), [False])
        self.assertEqual(stock_screen._conditions({'days_to_earnings': 30}, filters), [True])
        self.assertEqual(stock_screen._conditions({'days_to_earnings': None}, filters), [False])


if __name__ == '__main__':
    unittest.main()
