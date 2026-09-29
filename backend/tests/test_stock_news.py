import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from app.services.stock_detail import news, _rss_news


class StockNewsTest(unittest.TestCase):
    def item(self, title='Company earnings', url='https://example.com/story'):
        return dict(title=title, url=url, source='Publisher', published_at=datetime.now(timezone.utc).isoformat())

    def test_survives_yahoo_failure(self):
        def fetch(symbol, channel):
            if channel == 'Yahoo Finance': raise ValueError('down')
            return [self.item()]
        with patch('app.services.stock_detail._fetch_news_source', side_effect=fetch):
            result = news('avgo')
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['unavailable_sources'], ['Yahoo Finance'])

    def test_dedupe_dates_and_safe_links(self):
        old = self.item('Old'); old['published_at'] = '2000-01-01T00:00:00+00:00'
        with patch('app.services.stock_detail._fetch_news_source', return_value=[self.item(), self.item(url='https://other.com/story'), old, self.item('Unsafe', 'javascript:alert(1)')]):
            self.assertEqual(len(news('AVGO')['items']), 1)

    def test_all_failed_is_error(self):
        with patch('app.services.stock_detail._fetch_news_source', side_effect=ValueError('down')):
            with self.assertRaises(ValueError): news('AVGO')

    def test_rss_publisher_and_time(self):
        xml = b'<rss><channel><item><title>Company earnings - Publisher</title><source>Publisher</source><link>https://example.com</link><pubDate>Mon, 28 Sep 2026 08:00:00 GMT</pubDate></item></channel></rss>'
        row = _rss_news(xml, 'Google News')[0]
        self.assertEqual(row['title'], 'Company earnings')
        self.assertEqual(row['source'], 'Publisher')
        self.assertEqual(row['published_at'], '2026-09-28T08:00:00+00:00')
