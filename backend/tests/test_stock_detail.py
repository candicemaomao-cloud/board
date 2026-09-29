import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import User, DailyWatch, StockAnalysisNote
from app.routers.daily_watch import router
from app.services.auth import require_user


class StockDetailTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine, tables=[User.__table__, DailyWatch.__table__, StockAnalysisNote.__table__])
        self.db = Session(self.engine)
        self.user = User(username='first', password_hash='unused', permissions='["menu.dailyWatch"]')
        self.other = User(username='second', password_hash='unused', permissions='["menu.dailyWatch"]')
        self.db.add_all([self.user, self.other, DailyWatch(symbol='AAPL'), DailyWatch(symbol='MSFT')])
        self.db.commit()
        app = FastAPI()
        app.include_router(router, prefix='/api/daily-watch')
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_user] = lambda: self.user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_notes_persist_and_are_private_per_symbol_and_user(self):
        response = self.client.post('/api/daily-watch/1/notes', json={'content': '  观察支撑位  '})
        self.assertEqual(response.status_code, 201)
        self.db.expire_all()
        self.assertEqual(self.client.get('/api/daily-watch/1/notes').json()['items'][0]['content'], '观察支撑位')
        self.assertEqual(self.client.get('/api/daily-watch/2/notes').json()['items'], [])
        self.user = self.other
        self.assertEqual(self.client.get('/api/daily-watch/1/notes').json()['items'], [])

    def test_validation_and_permissions(self):
        self.assertEqual(self.client.post('/api/daily-watch/1/notes', json={'content': '  '}).status_code, 400)
        self.assertEqual(self.client.get('/api/daily-watch/999/notes').status_code, 404)
        self.user.permissions = '[]'
        self.assertEqual(self.client.get('/api/daily-watch/1/notes').status_code, 403)
        self.assertEqual(self.client.post('/api/daily-watch/1/notes', json={'content': 'x'}).status_code, 403)
        self.assertEqual(self.client.get('/api/daily-watch/1/detail/price').status_code, 403)

    def test_entry_endpoint_validation_and_permissions(self):
        with patch('app.services.entry_analysis.analyze', return_value={'horizon': 10}) as analyze:
            response = self.client.post('/api/daily-watch/1/entry-analysis', json={'horizon': 10, 'cost_bps': 20, 'borrow_pct': 5})
            self.assertEqual(response.status_code, 200)
            analyze.assert_called_once_with('AAPL', 10, 20, 5)
        self.assertEqual(self.client.post('/api/daily-watch/1/entry-analysis', json={'horizon': 1}).status_code, 422)
        self.assertEqual(self.client.post('/api/daily-watch/1/entry-analysis', json={'cost_bps': -1}).status_code, 422)
        self.user.permissions = '[]'
        self.assertEqual(self.client.post('/api/daily-watch/1/entry-analysis', json={}).status_code, 403)

    def test_save_generated_entry_price_to_watch_list(self):
        self.user.permissions = '["menu.dailyWatch", "btn.daily_watch.write"]'
        response = self.client.post('/api/daily-watch/1/entry-price', json={'side': 'long', 'price': 123.45})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['long_entry_price'], 123.45)
        self.assertIsNone(response.json()['short_entry_price'])

        response = self.client.post('/api/daily-watch/1/entry-price', json={'side': 'short', 'price': 135.25})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['long_entry_price'], 123.45)
        self.assertEqual(response.json()['short_entry_price'], 135.25)

        self.assertEqual(self.client.post('/api/daily-watch/1/entry-price', json={'side': 'long', 'price': 0}).status_code, 422)
        self.user.permissions = '[]'
        self.assertEqual(self.client.post('/api/daily-watch/1/entry-price', json={'side': 'long', 'price': 100}).status_code, 403)

    def test_independent_data_and_upstream_failure(self):
        with patch('app.services.ohlc.fetch_closes', return_value={'ohlc_bars': [], 'price': 42}) as fetch:
            self.assertEqual(self.client.get('/api/daily-watch/1/detail/price').json()['price'], 42)
            fetch.assert_called_once_with('AAPL', '1d', apply_live=False)
        with patch('app.services.stock_detail.news', side_effect=ValueError('upstream unavailable')):
            self.assertEqual(self.client.get('/api/daily-watch/1/detail/news').status_code, 502)
        self.assertEqual(self.client.get('/api/daily-watch/1/detail/invalid').status_code, 422)

    def test_symbol_detail_and_entry_analysis_but_no_notes(self):
        self.user.permissions = '["menu.stockScreener"]'
        with patch('app.services.ohlc.fetch_closes', return_value={'price': 88}) as fetch:
            response = self.client.get('/api/daily-watch/symbol/NVDA/detail/price')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['price'], 88)
        fetch.assert_called_once_with('NVDA', '1d', apply_live=False)

        self.assertEqual(self.client.post('/api/daily-watch/symbol/NVDA/notes', json={'content': '筛选后观察'}).status_code, 404)

        with patch('app.services.entry_analysis.analyze', return_value={'horizon': 30}) as analyze:
            response = self.client.post('/api/daily-watch/symbol/NVDA/entry-analysis', json={'horizon': 30})
        self.assertEqual(response.status_code, 200)
        analyze.assert_called_once_with('NVDA', 30, 20, 5)


if __name__ == '__main__':
    unittest.main()
