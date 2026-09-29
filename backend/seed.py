"""Generate realistic demo trading history so the dashboard is usable immediately."""

from __future__ import annotations

import random
from datetime import date, timedelta

from app.database import Base, SessionLocal, engine
from app.models import DailySnapshot, Side, TradeLog
from app.services.tags import get_or_create_tags

SYMBOLS = ["AAPL", "NVDA", "TSLA", "MSFT", "AMD", "META", "AMZN", "QQQ"]
TAG_POOL = [
    ("突破", 0.28),
    ("趋势跟随", 0.22),
    ("按计划止损", 0.18),
    ("日内冲高", 0.12),
    ("财报博弈", 0.08),
    ("FOMO追高", 0.12),
]

# Tag expected edge: FOMO and 财报 are worse; 突破 / 按计划止损 are better.
TAG_EDGE = {
    "突破": 0.22,
    "趋势跟随": 0.12,
    "按计划止损": 0.08,
    "日内冲高": -0.04,
    "财报博弈": -0.10,
    "FOMO追高": -0.28,
}


def trading_days(start: date, end: date) -> list[date]:
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def pick_tags(rng: random.Random) -> list[str]:
    names = []
    for name, p in TAG_POOL:
        if rng.random() < p:
            names.append(name)
    if not names:
        names.append("趋势跟随")
    if rng.random() < 0.15 and "按计划止损" not in names:
        names.append("按计划止损")
    return names[:3]


def seed() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    rng = random.Random(42)
    db = SessionLocal()

    start = date(2026, 3, 2)
    end = date(2026, 8, 12)
    equity = 100_000.0
    snapshots: list[DailySnapshot] = []
    trades: list[TradeLog] = []

    for i, d in enumerate(trading_days(start, end)):
        # Mild Friday drag + a June drawdown cluster.
        weekday_bias = -80 if d.weekday() == 4 else 40
        regime = -180 if date(2026, 6, 2) <= d <= date(2026, 6, 20) else 90
        day_pnl = 0.0
        n_trades = rng.choice([1, 1, 2, 2, 3])

        for _ in range(n_trades):
            tags = pick_tags(rng)
            edge = sum(TAG_EDGE[t] for t in tags) / len(tags)
            win = rng.random() < (0.52 + edge)
            magnitude = rng.uniform(80, 620)
            if d.weekday() == 4:
                magnitude *= 1.15
            pnl = magnitude if win else -magnitude * rng.uniform(0.7, 1.15)
            if "FOMO追高" in tags:
                pnl = -abs(pnl) * rng.uniform(0.9, 1.4) if rng.random() < 0.7 else pnl
            symbol = rng.choice(SYMBOLS)
            side = Side.LONG if rng.random() < 0.78 else Side.SHORT
            trade = TradeLog(
                date=d,
                symbol=symbol,
                side=side,
                pnl_amount=round(pnl, 2),
                pnl_pct=round(pnl / 8000, 4),
                notes=rng.choice(
                    [
                        "开盘回踩均线入场",
                        "突破前高后回踩确认",
                        "情绪偏热，仓位偏大",
                        "按计划止盈 1R",
                        "午后动能衰竭离场",
                        None,
                        None,
                    ]
                ),
                tags=get_or_create_tags(db, tags),
            )
            trades.append(trade)
            day_pnl += pnl

        day_pnl = round(day_pnl + weekday_bias * 0.15 + regime * 0.12 + rng.gauss(0, 40), 2)
        # Reconcile: keep snapshot PnL close to trade sum with a small fee/slippage drag.
        trade_sum = round(sum(t.pnl_amount for t in trades if t.date == d), 2)
        day_pnl = round(trade_sum - abs(rng.gauss(12, 8)), 2)
        if i == 0:
            day_pnl = round(trade_sum, 2)

        prev = equity
        equity = round(equity + day_pnl, 2)
        snapshots.append(
            DailySnapshot(
                date=d,
                total_equity=equity,
                daily_pnl=day_pnl,
                daily_return_pct=day_pnl / prev if prev else 0.0,
                cash_balance=round(equity * rng.uniform(0.18, 0.42), 2),
                notes="演示数据" if i == 0 else None,
            )
        )

    db.add_all(snapshots)
    db.add_all(trades)
    db.commit()
    db.close()
    print(f"Seeded {len(snapshots)} snapshots and {len(trades)} trades. Latest equity: {equity:.2f}")


if __name__ == "__main__":
    seed()
