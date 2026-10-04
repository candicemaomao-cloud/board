from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CryptoStrategy, CryptoStrategySignal, User
from app.services import crypto_strategy_records as records


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, CryptoStrategy.__table__, CryptoStrategySignal.__table__])
    return Session(engine)


def test_signal_is_frozen_once_and_monitor_only_is_not_evaluable():
    db = _db()
    user = User(username="u", password_hash="x")
    db.add(user); db.commit(); db.refresh(user)
    strategy = CryptoStrategy(user_id=user.id, symbol="OP", binance_symbol="OPUSDT", name="OP策略")
    db.add(strategy); db.commit(); db.refresh(strategy)
    result = {"asof": "2026-10-04T00:00:00Z", "price": 0.13, "combo_details": [{"id": "vol_high", "name": "放量", "hit": True}]}
    row = records.create_signal(db, strategy, result, None, "原始报告", direction="方向不明", verdict="观察", target_price=None, stop_price=None)
    again = records.create_signal(db, strategy, result, None, "被忽略", direction="偏多", verdict="买入", target_price=1, stop_price=0)
    assert row.id == again.id
    assert again.report_text == "原始报告"
    assert again.review_status == "not_evaluable"
    assert records.summary(db, user.id)["completed"] == 0
    out = records.signal_out(again)
    assert out["audit"]["dimensions"][0]["status"] == "passed"
    assert out["audit"]["dimensions"][-1]["status"] == "pending"
    assert "新闻与宏观" in out["audit"]["excluded"][0]


def test_three_day_bull_review_and_monthly_denominator(monkeypatch):
    db = _db()
    user = User(username="u", password_hash="x")
    db.add(user); db.commit(); db.refresh(user)
    strategy = CryptoStrategy(user_id=user.id, symbol="ETH", binance_symbol="ETHUSDT", name="ETH策略")
    db.add(strategy); db.commit(); db.refresh(strategy)
    review = {
        "evidence_id": "e1", "consensus": {"ready": True, "direction": "看涨"},
        "forecast": {
            "direction": "看涨", "interval": "1d", "horizon_bars": 7,
            "upside_probe_pct": {"low": 2, "median": 3, "high": 5},
            "downside_probe_pct": {"low": -4, "median": -2, "high": -1},
            "final_return_pct": {"low": 1, "median": 2, "high": 4},
            "duration_bars": {"low": 2, "median": 3, "high": 5},
        },
    }
    result = {"asof": "2026-10-01T00:00:00Z", "price": 100, "combo_details": [{"id": "macd_golden", "name": "MACD金叉", "hit": True}]}
    review.update({"sources": ["BTC", "ETH", "OP"], "windows": [{"samples": 12}, {"samples": 12}, {"samples": 12}]})
    row = records.create_signal(db, strategy, result, review, "完整报告", direction="偏多", verdict="候选", target_price=110, stop_price=95, indicator_backtest={"trades": 24})
    row.created_at = datetime.now(timezone.utc) - timedelta(days=3)
    row.review_due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    start = int(row.created_at.replace(tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(records, "klines", lambda *args, **kwargs: [
        {"ts": start + 60, "high": 108, "low": 98, "close": 102},
        {"ts": start + 3600, "high": 112, "low": 99, "close": 106},
    ])
    reviewed = records.review_signal(db, row)
    stats = records.summary(db, user.id)
    assert reviewed.review_status == "completed"
    assert reviewed.success == 1
    assert reviewed.outcome == "target_hit"
    assert reviewed.actual_return_pct == pytest.approx(6)
    assert stats["completed"] == 1
    assert stats["success_rate"] == 100
    assert stats["monthly"][0]["success_rate"] == 100
    audit = records.signal_out(reviewed)["audit"]["dimensions"]
    assert all(item["status"] == "passed" for item in audit)
    forecast = records.signal_out(reviewed)["forecast"]
    assert forecast["upside_probe_price"]["high"] == pytest.approx(105)
    assert forecast["downside_probe_price"]["low"] == pytest.approx(96)
