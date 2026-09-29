from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app.models import AppSettings, DailySnapshot
from app.services.analytics import load_snapshots, load_trades
from app.services.capital import account_totals

DEFAULTS = {
    "account_a": 11000.0,
    "account_b": 9641.0,
    "current_pnl": -1000.0,
    "target_profit": 30_000_000.0,
    "monthly_return": 0.08,
    "weekly_plan": "",
    "watchlist": "NVDA,AAPL,TSLA,QQQ",
}

SCENARIOS = [
    {"key": "m3", "label": "月化 3%", "monthly": 0.03},
    {"key": "m5", "label": "月化 5%", "monthly": 0.05},
    {"key": "m8", "label": "月化 8%", "monthly": 0.08},
    {"key": "m10", "label": "月化 10%", "monthly": 0.10},
    {"key": "m15", "label": "月化 15%", "monthly": 0.15},
    {"key": "w1", "label": "每周 1%", "monthly": (1 + 0.01) ** 4.345 - 1},
    {"key": "w2", "label": "每周 2%", "monthly": (1 + 0.02) ** 4.345 - 1},
]

DEADLINES_YEARS = [1, 3, 5, 8, 10]


def get_settings(db: Session) -> AppSettings:
    row = db.get(AppSettings, 1)
    if row is None:
        row = AppSettings(id=1, **DEFAULTS)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _compound_days(start: float, target: float, monthly_rate: float) -> float | None:
    if start <= 0:
        return None
    if target <= start:
        return 0.0
    if monthly_rate <= 0:
        return None
    daily_rate = (1 + monthly_rate) ** (12 / 365) - 1
    return math.log(target / start) / math.log(1 + daily_rate)


def _duration(days: float | None) -> dict:
    if days is None:
        return {
            "reachable": False,
            "days": None,
            "months": None,
            "years": None,
            "trading_weeks": None,
            "label": "收益率为 0 或为负，无法到达",
        }
    total = max(0, int(round(days)))
    if total <= 0:
        return {
            "reachable": True,
            "days": 0,
            "months": 0,
            "years": 0,
            "trading_weeks": 0,
            "label": "已达到目标",
        }
    return {
        "reachable": True,
        "days": total,
        "months": round(total / 30.44, 1),
        "years": round(total / 365, 2),
        "trading_weeks": int(round(total / 7)),
        "label": f"{total} 天",
    }


def _required_rate(start: float, target: float, years: int) -> dict:
    months = years * 12
    trading_weeks = years * 52
    if start <= 0 or target <= start or months <= 0:
        return {"years": years, "monthly": None, "weekly": None, "weekly_dollars": None}
    monthly = (target / start) ** (1 / months) - 1
    weekly = (target / start) ** (1 / trading_weeks) - 1
    remaining = target - start
    return {
        "years": years,
        "monthly": round(monthly * 100, 2),
        "weekly": round(weekly * 100, 3),
        "weekly_dollars": round(remaining / trading_weeks, 2),
    }


def _history_pace(snapshots: list[DailySnapshot], equity: float, target: float) -> dict | None:
    if len(snapshots) < 5:
        return None
    returns = [s.daily_return_pct or 0.0 for s in snapshots]
    avg = sum(returns) / len(returns)
    monthly = (1 + avg) ** 4.345 - 1 if avg > -1 else -1
    duration = _duration(_compound_days(equity, target, monthly) if avg > 0 else None)
    return {
        "sample_days": len(snapshots),
        "avg_daily_return_pct": round(avg * 100, 3),
        "implied_monthly_pct": round(monthly * 100, 2),
        **duration,
    }


def _live_equity(db: Session, settings: AppSettings, principal: float, user_id: int | None = None) -> float:
    snapshots = load_snapshots(db, user_id=user_id)
    trades = load_trades(db, user_id=user_id)
    if snapshots:
        latest = snapshots[-1]
        extra = sum(t.pnl_amount for t in trades if t.date > latest.date)
        return latest.total_equity + extra
    journaled = sum(t.pnl_amount for t in trades)
    return principal + (0.0 if user_id is not None else float(settings.current_pnl or 0)) + journaled


def build_goal(db: Session, user_id: int | None = None) -> dict:
    from app.models import User

    s = get_settings(db)
    user = db.get(User, user_id) if user_id else None
    if user is not None:
        principal = float(user.total_amount or 0)
    else:
        principal = float(s.account_a or 0) + float(s.account_b or 0)
    totals = account_totals(db, user_id=user_id)
    equity = totals["equity"]
    target_equity = principal + s.target_profit
    remaining = max(target_equity - equity, 0.0)
    live_pnl = equity - principal
    progress = (equity / target_equity * 100) if target_equity else 0.0

    assumed = _duration(_compound_days(equity, target_equity, s.monthly_return))
    scenarios = []
    for item in SCENARIOS:
        row = _duration(_compound_days(equity, target_equity, item["monthly"]))
        scenarios.append(
            {
                "key": item["key"],
                "name": item["label"],
                "monthly_pct": round(item["monthly"] * 100, 2),
                "highlight": abs(item["monthly"] - s.monthly_return) < 1e-9,
                **row,
            }
        )

    snapshots = load_snapshots(db, user_id=user_id)
    return {
        "account_a": s.account_a if user is None or user.role == "admin" else 0.0,
        "account_b": s.account_b if user is None or user.role == "admin" else 0.0,
        "principal": round(principal, 2),
        "starting_pnl": 0.0 if user is not None else s.current_pnl,
        "current_pnl": round(live_pnl, 2),
        "book_equity": totals["book_equity"],
        "equity": round(equity, 2),
        "available": totals["available"],
        "target_profit": s.target_profit,
        "target_equity": round(target_equity, 2),
        "remaining": round(remaining, 2),
        "progress_pct": round(progress, 4),
        "monthly_return": s.monthly_return,
        "weekly_plan": s.weekly_plan or "",
        "watchlist": s.watchlist or "NVDA,AAPL,TSLA,QQQ",
        "binance_api_key": s.binance_api_key or "",
        "has_binance_secret": bool(s.binance_api_secret),
        "binance_symbols": s.binance_symbols or "SKHY",
        "assumed": assumed,
        "scenarios": scenarios,
        "deadlines": [_required_rate(equity, target_equity, y) for y in DEADLINES_YEARS],
        "history": _history_pace(snapshots, equity, target_equity),
        "total_amount": float(user.total_amount or 0) if user else None,
    }
