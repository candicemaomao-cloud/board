import unittest
from datetime import datetime, timedelta
from app.services.option_chain_fallback import cboe_expiry


class CboeFallbackTest(unittest.TestCase):
    def payload(self):
        today = datetime.now()
        expiry = today.strftime('%y%m%d')
        def row(side, strike, oi):
            return {'option': f'AAPL{expiry}{side}{strike:08d}', 'open_interest': oi, 'bid': 1, 'ask': 2, 'iv': .3}
        return {'timestamp': today.isoformat(), 'data': {'symbol': 'AAPL', 'current_price': 100,
            'options': [row('C', 100000, 30), row('P', 100000, 40), row('P', 105000, 20)]}}

    def test_exact_expiry_and_put_only_strike(self):
        calls, puts, spot, _ = cboe_expiry(self.payload(), 'AAPL', datetime.now().date().isoformat())
        self.assertEqual(calls.openInterest.sum(), 30)
        self.assertEqual(puts.openInterest.sum(), 60)
        self.assertIn(105, puts.strike.values)
        self.assertEqual(spot, 100)
        with self.assertRaises(ValueError):
            cboe_expiry(self.payload(), 'AAPL', '2030-01-01')

    def test_reject_stale_wrong_symbol_and_zero_oi(self):
        day = datetime.now().date().isoformat()
        for change in ['stale', 'symbol', 'zero']:
            payload = self.payload()
            if change == 'stale': payload['timestamp'] = (datetime.now() - timedelta(days=10)).isoformat()
            if change == 'symbol': payload['data']['symbol'] = 'MSFT'
            if change == 'zero':
                for row in payload['data']['options']: row['open_interest'] = 0
            with self.assertRaises(ValueError): cboe_expiry(payload, 'AAPL', day)
