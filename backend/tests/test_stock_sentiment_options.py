import unittest

from app.services.stock_sentiment import _parse_nasdaq_option_chain


class NasdaqOptionFallbackTests(unittest.TestCase):
    def test_parses_nearest_expiry_volume_and_open_interest(self):
        payload = {'data': {
            'lastTrade': 'LAST TRADE: $123.45 (AS OF SEP 30, 2026)',
            'table': {'rows': [
                {'expirygroup': 'October 2, 2026', 'strike': None},
                {'expirygroup': '', 'strike': '120', 'c_Volume': '1,000', 'p_Volume': '750',
                 'c_Openinterest': '2,000', 'p_Openinterest': '2,500'},
                {'expirygroup': '', 'strike': '125', 'c_Volume': '500', 'p_Volume': '--',
                 'c_Openinterest': '1,000', 'p_Openinterest': '500'},
                {'expirygroup': 'October 9, 2026', 'strike': None},
                {'expirygroup': '', 'strike': '120', 'c_Volume': '9999', 'p_Volume': '9999'},
            ]},
        }}

        result = _parse_nasdaq_option_chain(payload, 'XLK')

        self.assertEqual(result['expiration'], 'October 2, 2026')
        self.assertEqual(result['call_vol'], 1500)
        self.assertEqual(result['put_vol'], 750)
        self.assertEqual(result['pcr_vol'], 0.5)
        self.assertEqual(result['pcr_oi'], 1.0)
        self.assertEqual(result['spot'], 123.45)
        self.assertEqual(result['source'], 'Nasdaq')


if __name__ == '__main__':
    unittest.main()
