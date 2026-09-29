"""MU 局部底策略：最近 10 个交易日、每天 5000 美元、同时只持一仓。"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

backend = Path(__file__).resolve().parents[2]
if str(backend) not in sys.path:
    sys.path.insert(0, str(backend))

from app.services.user_research import wash_bars
from app.services.ohlc import fetch_closes
from app.user_stocks.tsla_golden import (
    STRATEGY,
    et_day,
    et_time,
    is_rth,
    signal_at,
)

BUDGET = 5000.0
SLIP_BP = 1.0  # 每边 1bp
DAYS = 10


def slip(px: float, side: str) -> float:
    adj = px * SLIP_BP / 10000.0
    return px + adj if side == "buy" else px - adj


def last_n_rth_days(bars_5: list[dict], n: int):
    days = []
    seen = set()
    for bar in reversed(bars_5):
        if not is_rth(bar["ts"]):
            continue
        day = et_day(bar["ts"])
        if day in seen:
            continue
        seen.add(day)
        days.append(day)
        if len(days) >= n:
            break
    return list(reversed(days))


def bar_index(bars: list[dict]) -> dict[int, int]:
    return {int(bar["ts"]): i for i, bar in enumerate(bars)}


def run_backtest() -> dict:
    raw5 = fetch_closes(STRATEGY["symbol"], "5m", apply_live=False)
    raw30 = fetch_closes(STRATEGY["symbol"], "30m", apply_live=False)
    bars_5 = wash_bars(raw5)
    bars_30 = wash_bars(raw30)
    days = last_n_rth_days(bars_5, DAYS)
    day_set = set(days)
    idx = bar_index(bars_5)

    trades = []
    open_pos = None
    skipped = 0

    rth_i = [i for i, bar in enumerate(bars_5) if is_rth(bar["ts"]) and et_day(bar["ts"]) in day_set]
    rth_by_day = defaultdict(list)
    for i in rth_i:
        rth_by_day[et_day(bars_5[i]["ts"])].append(i)

    for i in rth_i:
        bar = bars_5[i]
        day = et_day(bar["ts"])
        last_i = rth_by_day[day][-1]
        snap = signal_at(bars_5, bars_30, i)

        if open_pos is not None:
            buy_i = open_pos["buy_i"]
            if i > buy_i:
                reason = None
                fill = None
                if bar["low"] <= open_pos["stop"]:
                    reason, fill = "止损", open_pos["stop"]
                elif bar["high"] >= open_pos["up"]:
                    reason, fill = "+1σ", open_pos["up"]
                elif bar["high"] >= open_pos["expect"]:
                    reason, fill = "目标E", open_pos["expect"]
                elif snap and snap["fade"]:
                    reason, fill = "涨速变慢", bar["close"]
                elif i == last_i:
                    reason, fill = "收盘平仓", bar["close"]

                if reason:
                    sell = slip(fill, "sell")
                    pnl = (sell - open_pos["buy"]) * open_pos["shares"]
                    pnl_pct = (sell / open_pos["buy"] - 1) * 100
                    trades.append(
                        {
                            **open_pos,
                            "sell": round(sell, 4),
                            "sell_raw": round(fill, 4),
                            "sell_time": et_time(bar["ts"]),
                            "sell_ts": int(bar["ts"]),
                            "exit": reason,
                            "pnl": round(pnl, 2),
                            "pnl_pct": round(pnl_pct, 3),
                            "hold_bars": i - buy_i,
                        }
                    )
                    open_pos = None

        if open_pos is None and snap and snap["fire"]:
            if i == last_i:
                skipped += 1
                continue
            raw_buy = bar["close"]
            buy = slip(raw_buy, "buy")
            shares = BUDGET / buy
            open_pos = {
                "day": str(day),
                "buy_i": i,
                "buy_time": et_time(bar["ts"]),
                "buy_ts": int(bar["ts"]),
                "buy": round(buy, 4),
                "buy_raw": round(raw_buy, 4),
                "shares": round(shares, 6),
                "notional": round(shares * buy, 2),
                "expect": snap["expect"],
                "up": snap["up"],
                "stop": snap["stop"],
                "mu": round(snap["mu"], 5),
            }

    if open_pos is not None:
        bar = bars_5[open_pos["buy_i"]]
        last = bars_5[rth_i[-1]]
        sell = slip(last["close"], "sell")
        pnl = (sell - open_pos["buy"]) * open_pos["shares"]
        trades.append(
            {
                **open_pos,
                "sell": round(sell, 4),
                "sell_raw": round(last["close"], 4),
                "sell_time": et_time(last["ts"]),
                "sell_ts": int(last["ts"]),
                "exit": "未平仓·市价",
                "pnl": round(pnl, 2),
                "pnl_pct": round((sell / open_pos["buy"] - 1) * 100, 3),
                "hold_bars": idx[int(last["ts"])] - open_pos["buy_i"],
                "open": True,
            }
        )

    daily = []
    by_day = defaultdict(list)
    for t in trades:
        by_day[t["day"]].append(t)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    curve = []
    for day in days:
        rows = by_day.get(str(day), [])
        pnl = round(sum(r["pnl"] for r in rows), 2)
        equity += pnl
        peak = max(peak, equity)
        dd = equity - peak
        max_dd = min(max_dd, dd)
        curve.append(round(equity, 2))
        daily.append(
            {
                "day": str(day),
                "trades": len(rows),
                "wins": sum(1 for r in rows if r["pnl"] > 0),
                "pnl": pnl,
                "equity": round(equity, 2),
                "exits": [r["exit"] for r in rows],
            }
        )

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    first_open = None
    last_close = None
    for i in rth_i:
        d = et_day(bars_5[i]["ts"])
        if d == days[0] and first_open is None:
            first_open = bars_5[i]["open"]
        if d == days[-1]:
            last_close = bars_5[i]["close"]
    bh_shares = BUDGET / first_open if first_open else 0
    bh = (last_close - first_open) * bh_shares if first_open and last_close else 0

    total = round(sum(t["pnl"] for t in trades), 2)
    return {
        "symbol": STRATEGY["symbol"],
        "source": raw5.get("source"),
        "budget": BUDGET,
        "slip_bp": SLIP_BP,
        "days": [str(d) for d in days],
        "n_days": len(days),
        "n_trades": len(trades),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0,
        "total_pnl": total,
        "total_pct_on_budget": round(100 * total / BUDGET, 2),
        "avg_pnl": round(total / len(trades), 2) if trades else 0,
        "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        "best": max((t["pnl"] for t in trades), default=0),
        "worst": min((t["pnl"] for t in trades), default=0),
        "max_dd": round(max_dd, 2),
        "skipped_eod_buys": skipped,
        "buy_hold_pnl": round(bh, 2),
        "first_open": first_open,
        "last_close": last_close,
        "daily": daily,
        "curve": curve,
        "trades": [
            {
                k: t[k]
                for k in (
                    "day",
                    "buy_time",
                    "buy",
                    "sell_time",
                    "sell",
                    "exit",
                    "pnl",
                    "pnl_pct",
                    "hold_bars",
                    "expect",
                    "up",
                    "stop",
                    "mu",
                    "notional",
                )
                if k in t
            }
            for t in trades
        ],
        "bars_5": len(bars_5),
        "price": bars_5[-1]["close"] if bars_5 else None,
    }


if __name__ == "__main__":
    out = run_backtest()
    print(json.dumps(out, ensure_ascii=False, indent=2))
