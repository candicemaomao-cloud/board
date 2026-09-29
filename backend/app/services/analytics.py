from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import AppSettings, DailySnapshot, TradeLog
from app.services.capital import account_totals

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
MONTH_NAMES = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"]


def _in_range(query, column, start: date | None, end: date | None):
    if start:
        query = query.where(column >= start)
    if end:
        query = query.where(column <= end)
    return query


def load_snapshots(
    db: Session,
    start: date | None = None,
    end: date | None = None,
    user_id: int | None = None,
) -> list[DailySnapshot]:
    stmt = _in_range(select(DailySnapshot), DailySnapshot.date, start, end).order_by(DailySnapshot.date)
    if user_id is not None:
        stmt = stmt.where(DailySnapshot.user_id == user_id)
    return list(db.scalars(stmt))


def load_trades(
    db: Session,
    start: date | None = None,
    end: date | None = None,
    user_id: int | None = None,
) -> list[TradeLog]:
    stmt = (
        _in_range(select(TradeLog), TradeLog.date, start, end)
        .options(selectinload(TradeLog.tags))
        .order_by(TradeLog.date.desc(), TradeLog.id.desc())
    )
    if user_id is not None:
        stmt = stmt.where(TradeLog.user_id == user_id)
    return list(db.scalars(stmt))


def _max_drawdown(equities: list[float]) -> dict:
    if not equities:
        return {"amount": 0.0, "pct": 0.0}
    peak = equities[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for equity in equities:
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        pct = drawdown / peak if peak else 0.0
        if drawdown > max_dd:
            max_dd = drawdown
            max_dd_pct = pct
    return {"amount": round(max_dd, 2), "pct": round(max_dd_pct * 100, 2)}


def _streaks(pnls: list[float]) -> dict:
    longest_win = longest_loss = current_win = current_loss = 0
    run = 0
    run_sign = 0
    for pnl in pnls:
        sign = 1 if pnl > 0 else -1 if pnl < 0 else 0
        if sign == 0:
            run = 0
            run_sign = 0
            continue
        if sign == run_sign:
            run += 1
        else:
            run_sign = sign
            run = 1
        if sign > 0:
            longest_win = max(longest_win, run)
        else:
            longest_loss = max(longest_loss, run)

    if pnls:
        last = pnls[-1]
        current = 0
        sign = 1 if last > 0 else -1 if last < 0 else 0
        for pnl in reversed(pnls):
            this = 1 if pnl > 0 else -1 if pnl < 0 else 0
            if this != sign or this == 0:
                break
            current += 1
        if sign > 0:
            current_win = current
        elif sign < 0:
            current_loss = current

    return {
        "longest_win_days": longest_win,
        "longest_loss_days": longest_loss,
        "current_win_days": current_win,
        "current_loss_days": current_loss,
    }


def _period_pnl(snapshots: list[DailySnapshot], start: date, end: date) -> float:
    return round(sum(s.daily_pnl for s in snapshots if start <= s.date <= end), 2)


def _period_pnl_with_trades(
    snapshots: list[DailySnapshot],
    trades: list[TradeLog],
    start: date,
    end: date,
) -> tuple[float, str]:
    """
    区间盈亏：有周资金快照则用快照（避免与逐笔交易重复）；
    否则回退到交易日志合计。
    """
    snap = [s for s in snapshots if start <= s.date <= end]
    if snap:
        return round(sum(s.daily_pnl for s in snap), 2), "snapshot"
    tr = [t for t in trades if start <= t.date <= end]
    return round(sum(t.pnl_amount for t in tr), 2), "trades"


def build_overview(
    db: Session,
    start: date | None = None,
    end: date | None = None,
    user_id: int | None = None,
) -> dict:
    snapshots = load_snapshots(db, start, end, user_id=user_id)
    all_snapshots = load_snapshots(db, user_id=user_id)
    trades = load_trades(db, start, end, user_id=user_id)
    all_trades = load_trades(db, user_id=user_id)

    pnls = [s.daily_pnl for s in snapshots]
    # 无周快照时，用交易日志驱动胜率 / PF / 复盘
    using_trade_stats = not pnls and bool(trades)
    if using_trade_stats:
        pnls = [t.pnl_amount for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    flats = [p for p in pnls if p == 0]

    total_pnl = round(sum(pnls), 2)
    win_sum = sum(wins)
    loss_sum = abs(sum(losses))
    if loss_sum > 0:
        profit_factor = round(win_sum / loss_sum, 2)
    elif wins:
        profit_factor = None  # 无亏损，前端展示 ∞
    else:
        profit_factor = 0.0

    latest = all_snapshots[-1] if all_snapshots else None
    first = all_snapshots[0] if all_snapshots else None
    from app.models import User

    user = db.get(User, user_id) if user_id else None
    settings_row = db.get(AppSettings, 1)
    if user is not None:
        fallback_principal = float(user.total_amount or 0)
        fallback_equity = fallback_principal
    else:
        fallback_principal = (settings_row.account_a + settings_row.account_b) if settings_row else 0.0
        fallback_equity = (fallback_principal + settings_row.current_pnl) if settings_row else 0.0
    starting_equity = round((first.total_equity - first.daily_pnl), 2) if first else round(fallback_principal, 2)
    totals = account_totals(db, user_id=user_id)
    total_equity = totals["equity"]
    latest_day = latest.date if latest else None
    latest_pnl = latest.daily_pnl if latest else 0.0
    latest_return_pct = round((latest.daily_return_pct or 0) * 100, 2) if latest else 0.0

    ref = latest.date if latest else date.today()
    month_start = ref.replace(day=1)
    year_start = ref.replace(month=1, day=1)
    mtd, mtd_src = _period_pnl_with_trades(all_snapshots, all_trades, month_start, ref)
    ytd, ytd_src = _period_pnl_with_trades(all_snapshots, all_trades, year_start, ref)

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week_snaps = [s for s in all_snapshots if s.date >= monday]
    week_trades = [t for t in all_trades if t.date >= monday]
    if week_snaps:
        this_week_pnl = round(sum(s.daily_pnl for s in week_snaps), 2)
        week_src = "snapshot"
        week_label = latest_day.isoformat() if latest_day and latest_day >= monday else monday.isoformat()
        week_return_pct = latest_return_pct if latest_day and latest_day >= monday else 0.0
    else:
        this_week_pnl = round(sum(t.pnl_amount for t in week_trades), 2)
        week_src = "trades"
        week_label = (
            f"交易 {len(week_trades)} 笔"
            if week_trades
            else "本周尚未记账"
        )
        week_return_pct = 0.0

    # 本周卡片：优先展示本周合计（含交易日志回退），不再只看「最近一笔快照」
    display_week_pnl = this_week_pnl
    if week_snaps and latest and latest.date >= monday:
        # 有本周快照时，主数字仍可用最新一周快照，与合计一致时更直观
        display_week_pnl = this_week_pnl

    equities = [s.total_equity for s in snapshots]
    cumulative = []
    running = 0.0
    for s in snapshots:
        running += s.daily_pnl
        cumulative.append(
            {
                "date": s.date.isoformat(),
                "equity": round(s.total_equity, 2),
                "daily_pnl": round(s.daily_pnl, 2),
                "cumulative_pnl": round(running, 2),
                "daily_return_pct": round((s.daily_return_pct or 0) * 100, 2),
            }
        )
    # 无快照时，用交易日志画简易累计曲线
    if not cumulative and trades:
        running = 0.0
        chron = sorted(trades, key=lambda t: (t.date, t.id or 0))
        for t in chron:
            running += t.pnl_amount
            cumulative.append(
                {
                    "date": t.date.isoformat(),
                    "equity": round(total_equity, 2),
                    "daily_pnl": round(t.pnl_amount, 2),
                    "cumulative_pnl": round(running, 2),
                    "daily_return_pct": 0.0,
                }
            )

    month_map: dict[int, list[float]] = defaultdict(list)
    for s in snapshots:
        month_map[s.date.month].append(s.daily_pnl)
    if not snapshots:
        for t in trades:
            month_map[t.date.month].append(t.pnl_amount)
    monthly = []
    for i in range(1, 13):
        values = month_map.get(i, [])
        w = [v for v in values if v > 0]
        monthly.append(
            {
                "month": i,
                "name": MONTH_NAMES[i - 1],
                "weeks": len(values),
                "total_pnl": round(sum(values), 2),
                "avg_pnl": round(sum(values) / len(values), 2) if values else 0.0,
                "win_rate": round(len(w) / len(values) * 100, 1) if values else 0.0,
            }
        )

    tag_map: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        names = [t.name for t in trade.tags] or ["未打标"]
        for name in names:
            tag_map[name].append(trade.pnl_amount)
    tags = []
    for name, values in tag_map.items():
        w = [v for v in values if v > 0]
        tags.append(
            {
                "name": name,
                "count": len(values),
                "total_pnl": round(sum(values), 2),
                "avg_pnl": round(sum(values) / len(values), 2),
                "win_rate": round(len(w) / len(values) * 100, 1) if values else 0.0,
            }
        )
    tags.sort(key=lambda x: x["total_pnl"], reverse=True)

    trade_wins = [t.pnl_amount for t in trades if t.pnl_amount > 0]
    trade_losses = [t.pnl_amount for t in trades if t.pnl_amount < 0]

    range_pnl, range_src = _period_pnl_with_trades(
        snapshots if snapshots else all_snapshots,
        trades if trades else all_trades,
        start or date(1970, 1, 1),
        end or date.today(),
    )
    # 区间筛选：有快照用快照合计，否则用当前筛选下的交易合计
    if snapshots:
        range_pnl = total_pnl
        range_src = "snapshot"
    elif trades:
        range_pnl = total_pnl
        range_src = "trades"

    return {
        "cards": {
            "total_equity": round(total_equity, 2),
            "starting_equity": starting_equity,
            "latest_date": week_label if week_src == "trades" else (latest_day.isoformat() if latest_day else None),
            "daily_pnl": round(display_week_pnl, 2),
            "daily_return_pct": week_return_pct,
            "this_week_pnl": this_week_pnl,
            "mtd_pnl": mtd,
            "ytd_pnl": ytd,
            "range_pnl": range_pnl,
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
            "profit_factor": profit_factor,
            "max_drawdown": _max_drawdown(equities),
            "pnl_source": {
                "week": week_src,
                "mtd": mtd_src,
                "ytd": ytd_src,
                "range": range_src,
            },
        },
        "review": {
            "trading_days": len(pnls),
            "win_weeks": len(wins),
            "loss_weeks": len(losses),
            "flat_weeks": len(flats),
            "trading_weeks": len(pnls),
            "win_days": len(wins),
            "loss_days": len(losses),
            "flat_days": len(flats),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "max_win": round(max(wins), 2) if wins else 0.0,
            "max_loss": round(min(losses), 2) if losses else 0.0,
            "expectancy": round(total_pnl / len(pnls), 2) if pnls else 0.0,
            "trade_count": len(trades),
            "trade_win_rate": round(len(trade_wins) / len(trades) * 100, 1) if trades else 0.0,
            "avg_win_trade": round(sum(trade_wins) / len(trade_wins), 2) if trade_wins else 0.0,
            "avg_loss_trade": round(sum(trade_losses) / len(trade_losses), 2) if trade_losses else 0.0,
            "streaks": _streaks(pnls),
            "stats_from": "trades" if using_trade_stats else "snapshots",
        },
        "equity_curve": cumulative,
        "monthly": monthly,
        "tags": tags,
        "calendar": (
            [{"date": s.date.isoformat(), "pnl": round(s.daily_pnl, 2)} for s in snapshots]
            if snapshots
            else [{"date": t.date.isoformat(), "pnl": round(t.pnl_amount, 2)} for t in sorted(trades, key=lambda x: x.date)]
        ),
    }
