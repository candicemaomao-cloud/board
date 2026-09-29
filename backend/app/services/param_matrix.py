"""因股制宜：性格画像 + 样本内网格 + 样本外验收。

不扫 500 只。先对一组性格差得开的股票，用同一套 5min 局部底规则，
只改 μ 门槛 / ATR 回撤 / ATR 止损 / 目标带宽。

参数在「训练日」上选，拿到「测试日」上才报成绩——同一段行情里选参再回测，
那叫过拟合，不叫工业级。
"""

from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict
from datetime import date, timedelta

from app.services.arb_strategy import _adf
from app.services.ohlc import fetch_closes, fetch_closes_covering
from app.services.user_research import wash_bars
from app.user_stocks.tsla_golden import (
    LOOKBACK_30,
    TREND_THRESHOLD,
    Z,
    et_day,
    is_rth,
    log_returns,
    mu_index,
    remaining_5m,
    signal_at,
    stdev,
)

BUDGET = 5000.0
SLIP_BP = 1.0
TEST_DAYS = 10
ATR_N = 14

# 科技高波动、半导体、黄金、消费。先看矩阵能不能分开性格，再谈 500 只。
UNIVERSE = [
    {"symbol": "TSLA", "role": "高波动科技"},
    {"symbol": "MU", "role": "高波动半导体"},
    {"symbol": "GLD", "role": "黄金 ETF"},
    {"symbol": "WMT", "role": "消费防御"},
]

# 小网格：围绕 ATR 缩放，不穷举几百组。
K_MU = (0.15, 0.40)
Z_GRID = (0.75, 1.25)
K_STOP = (0.20, 0.45)
K_DIP = (0.15, 0.35)


def slip(px: float, side: str) -> float:
    adj = px * SLIP_BP / 10000.0
    return px + adj if side == "buy" else px - adj


def true_ranges(bars: list[dict]) -> list[float]:
    out = []
    prev = None
    for bar in bars:
        hl = bar["high"] - bar["low"]
        if prev is None:
            out.append(hl)
        else:
            out.append(max(hl, abs(bar["high"] - prev), abs(bar["low"] - prev)))
        prev = bar["close"]
    return out


def wilder_atr(trs: list[float], n: int = ATR_N) -> float | None:
    if len(trs) < n:
        return None
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def atr_ending_at(daily: list[dict], asof: date) -> float | None:
    """只用 asof 当天及之前的日 K，避免把测试日波动泄漏进训练。"""
    closed = [bar for bar in daily if et_day(bar["ts"]) <= asof]
    if len(closed) < ATR_N + 1:
        return None
    return wilder_atr(true_ranges(closed[-(ATR_N + 40) :]))


def half_life(series: list[float]) -> float | None:
    if len(series) < 20:
        return None
    x, y = series[:-1], series[1:]
    mx = sum(x) / len(x)
    varx = sum((xi - mx) ** 2 for xi in x)
    if varx < 1e-18:
        return None
    my = sum(y) / len(y)
    phi = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / varx
    if not (0 < phi < 1):
        return None
    return math.log(0.5) / math.log(phi)


def rth_days(bars_5: list[dict]) -> list:
    days = []
    seen = set()
    for bar in bars_5:
        if not is_rth(bar["ts"]):
            continue
        day = et_day(bar["ts"])
        if day not in seen:
            seen.add(day)
            days.append(day)
    return days


def split_days(days: list):
    if len(days) < 8:
        return days[: max(1, len(days) // 2)], days[max(1, len(days) // 2) :]
    n_test = min(TEST_DAYS, max(4, len(days) // 3))
    return days[:-n_test], days[-n_test:]


def persona(symbol: str, role: str, daily: list[dict]) -> dict:
    window = daily[-90:] if len(daily) >= 40 else daily
    px = [bar["close"] for bar in window]
    last = px[-1]
    rets = log_returns(px)
    logs = [math.log(max(p, 1e-12)) for p in px]
    atr = wilder_atr(true_ranges(window))
    atr_pct = (atr / last * 100) if atr and last else 0.0
    vol = stdev(rets) * math.sqrt(252) * 100 if rets else 0.0
    adf_px = _adf(logs)
    adf_r = _adf(rets)
    hl = half_life(rets)
    if adf_px and adf_px.get("pass"):
        kind = "价位均值回归（少见）"
    elif atr_pct >= 2.5:
        kind = "高波动趋势油箱"
    elif atr_pct >= 1.2:
        kind = "中波动"
    else:
        kind = "低波动防御"
    sigma_d = stdev(rets) if rets else 0.0
    sigma_30 = sigma_d / math.sqrt(13) if sigma_d else 0.0
    return {
        "symbol": symbol,
        "role": role,
        "kind": kind,
        "price": round(last, 4),
        "atr": round(atr, 4) if atr else None,
        "atr_pct": round(atr_pct, 3),
        "vol_ann": round(vol, 2),
        "sigma_d": round(sigma_d, 5),
        "sigma_30": round(sigma_30, 5),
        "adf_price": adf_px,
        "adf_return": adf_r,
        "ret_half_life": round(hl, 2) if hl else None,
        "n_daily": len(window),
    }


def prices_from_persona(persona_row: dict, k_dip: float, z: float, sigma_5: float, n: int = 20) -> dict:
    """现价附近的抄底带 / 卖出带。P_low 是相对今日高点的 ATR 回撤，不是全局最低。"""
    s = persona_row["price"]
    atr = persona_row.get("atr") or 0.0
    p_low = s - k_dip * atr
    p_stop = s - (0.3 * atr)
    p_target = s * math.exp(z * max(sigma_5, 1e-9) * math.sqrt(n))
    return {
        "p_low": round(p_low, 4),
        "p_stop": round(p_stop, 4),
        "p_target": round(p_target, 4),
        "p_low_pct": round((p_low / s - 1) * 100, 3) if s else 0.0,
        "p_target_pct": round((p_target / s - 1) * 100, 3) if s else 0.0,
    }


def simulate(bars_5, bars_30, days, params: dict) -> dict:
    day_set = set(days)
    rth_i = [i for i, bar in enumerate(bars_5) if is_rth(bar["ts"]) and et_day(bar["ts"]) in day_set]
    if not rth_i:
        return empty_sim()
    rth_by_day = defaultdict(list)
    for i in rth_i:
        rth_by_day[et_day(bars_5[i]["ts"])].append(i)

    trades = []
    open_pos = None
    for i in rth_i:
        bar = bars_5[i]
        day = et_day(bar["ts"])
        last_i = rth_by_day[day][-1]
        snap = signal_at(bars_5, bars_30, i, params)

        if open_pos is not None and i > open_pos["buy_i"]:
            reason = fill = None
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
                trades.append({"day": str(open_pos["day"]), "pnl": pnl, "exit": reason})
                open_pos = None

        if open_pos is None and snap and snap["fire"] and i != last_i:
            buy = slip(bar["close"], "buy")
            shares = BUDGET / buy
            open_pos = {
                "day": day,
                "buy_i": i,
                "buy": buy,
                "shares": shares,
                "expect": snap["expect"],
                "up": snap["up"],
                "stop": snap["stop"],
            }

    if open_pos is not None:
        last = bars_5[rth_i[-1]]
        sell = slip(last["close"], "sell")
        pnl = (sell - open_pos["buy"]) * open_pos["shares"]
        trades.append({"day": str(open_pos["day"]), "pnl": pnl, "exit": "未平仓"})

    pnls = [t["pnl"] for t in trades]
    total = sum(pnls)
    equity = peak = max_dd = 0.0
    by_day = defaultdict(float)
    for t in trades:
        by_day[t["day"]] += t["pnl"]
    for day in days:
        equity += by_day.get(str(day), 0.0)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    wins = [p for p in pnls if p > 0]
    return {
        "n_trades": len(trades),
        "n_wins": len(wins),
        "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "total_pnl": round(total, 2),
        "max_dd": round(max_dd, 2),
        "avg_pnl": round(total / len(trades), 2) if trades else 0.0,
    }


def empty_sim() -> dict:
    return {"n_trades": 0, "n_wins": 0, "win_rate": 0.0, "total_pnl": 0.0, "max_dd": 0.0, "avg_pnl": 0.0}


def score(sim: dict) -> float:
    if sim["n_trades"] < 3:
        return -1e9
    return sim["total_pnl"] / (80.0 + abs(sim["max_dd"]))


def load_symbol(symbol: str) -> dict:
    start = date.today() - timedelta(days=70)
    raw_d = fetch_closes(symbol, "1d", apply_live=False)
    raw_5 = fetch_closes_covering(symbol, "5m", start=start)
    raw_30 = fetch_closes_covering(symbol, "30m", start=start)
    return {
        "daily": wash_bars(raw_d),
        "bars_5": wash_bars(raw_5),
        "bars_30": wash_bars(raw_30),
        "source_5": raw_5.get("source"),
        "source_d": raw_d.get("source"),
    }


def grid_for(persona_row: dict) -> list[dict]:
    sigma_30 = persona_row.get("sigma_30") or 0.001
    rows = [
        {
            "name": "共用默认",
            "trend_threshold": TREND_THRESHOLD,
            "z": Z,
            "k_stop": 0.0,
            "k_dip": 0.0,
            "lookback_5": 5,
        }
    ]
    for k_mu, z, k_stop, k_dip in itertools.product(K_MU, Z_GRID, K_STOP, K_DIP):
        rows.append(
            {
                "name": "网格",
                "trend_threshold": max(0.0002, k_mu * sigma_30),
                "z": z,
                "k_stop": k_stop,
                "k_dip": k_dip,
                "lookback_5": 5,
                "k_mu": k_mu,
            }
        )
    return rows


def last_sigma_5(bars_5: list[dict]) -> float:
    px = [bar["close"] for bar in bars_5[-78:] if is_rth(bar["ts"])] or [bar["close"] for bar in bars_5[-78:]]
    return stdev(log_returns(px)) if len(px) > 3 else 0.0


def precompute_sigma(bars_5: list[dict]) -> list[float]:
    out = [0.0] * len(bars_5)
    for i in range(len(bars_5)):
        start = max(0, i - 77)
        px = [bars_5[j]["close"] for j in range(start, i + 1)]
        out[i] = stdev(log_returns(px)) if len(px) > 2 else 0.0
    return out


def precompute_session_highs(bars_5: list[dict]) -> list[float]:
    out = [0.0] * len(bars_5)
    hi = 0.0
    prev = None
    for i, bar in enumerate(bars_5):
        if not is_rth(bar["ts"]):
            out[i] = hi
            continue
        day = et_day(bar["ts"])
        if day != prev:
            hi = bar["high"]
            prev = day
        else:
            hi = max(hi, bar["high"])
        out[i] = hi
    return out


def run_symbol(item: dict) -> dict:
    symbol = item["symbol"]
    print(f"... {symbol} {item['role']}", flush=True)
    data = load_symbol(symbol)
    daily, bars_5, bars_30 = data["daily"], data["bars_5"], data["bars_30"]
    face = persona(symbol, item["role"], daily)
    days = rth_days(bars_5)
    train_days, test_days = split_days(days)
    index = mu_index(bars_30, LOOKBACK_30)
    sigs = precompute_sigma(bars_5)
    highs = precompute_session_highs(bars_5)
    atr_train = atr_ending_at(daily, train_days[-1]) if train_days else face.get("atr")
    atr_test = atr_ending_at(daily, test_days[-1]) if test_days else face.get("atr")
    cache = {"mu_index": index, "sigma_index": sigs, "session_highs": highs}

    best = None
    evaluated = []
    for cand in grid_for(face):
        train_params = {
            **cand,
            "atr": atr_train or 0.0,
            **cache,
        }
        train = simulate(bars_5, bars_30, train_days, train_params)
        row = {**cand, "train": train, "train_score": score(train)}
        evaluated.append(row)
        if best is None or row["train_score"] > best["train_score"]:
            best = row

    frozen_atr = atr_train or 0.0
    test_params = {
        "trend_threshold": best["trend_threshold"],
        "z": best["z"],
        "k_stop": best["k_stop"],
        "k_dip": best["k_dip"],
        "lookback_5": best["lookback_5"],
        "atr": frozen_atr,
        **cache,
    }
    test = simulate(bars_5, bars_30, test_days, test_params)
    baseline_test = simulate(
        bars_5,
        bars_30,
        test_days,
        {
            "trend_threshold": TREND_THRESHOLD,
            "z": Z,
            "k_stop": 0.0,
            "k_dip": 0.0,
            "lookback_5": 5,
            "atr": frozen_atr,
            **cache,
        },
    )
    n_typ = remaining_5m(bars_5[-1]["ts"]) if bars_5 else 20
    bands = prices_from_persona(face, best["k_dip"] or 0.3, best["z"], last_sigma_5(bars_5), n=max(n_typ, 8))
    return {
        "symbol": symbol,
        "role": item["role"],
        "source_5": data["source_5"],
        "persona": face,
        "train_days": [str(d) for d in train_days],
        "test_days": [str(d) for d in test_days],
        "n_train": len(train_days),
        "n_test": len(test_days),
        "n_grid": len(evaluated),
        "best": {
            "name": best["name"],
            "trend_threshold": round(best["trend_threshold"], 6),
            "z": best["z"],
            "k_stop": best["k_stop"],
            "k_dip": best["k_dip"],
            "k_mu": best.get("k_mu"),
            "train": best["train"],
            "train_score": round(best["train_score"], 4) if best["train_score"] > -1e8 else None,
        },
        "test": test,
        "baseline_test": baseline_test,
        "bands": bands,
        "atr_train": round(atr_train, 4) if atr_train else None,
        "atr_test": round(atr_test, 4) if atr_test else None,
    }


def run_matrix(universe: list[dict] | None = None) -> dict:
    rows = []
    errors = []
    for item in universe or UNIVERSE:
        try:
            rows.append(run_symbol(item))
        except Exception as exc:
            errors.append({"symbol": item["symbol"], "error": str(exc)})
    return {
        "budget": BUDGET,
        "slip_bp": SLIP_BP,
        "grid": {"k_mu": K_MU, "z": Z_GRID, "k_stop": K_STOP, "k_dip": K_DIP},
        "note": "参数只在训练日上选；测试日从未参与选参。ADF 做在日线对数价格 vs 对数收益。",
        "rows": rows,
        "errors": errors,
    }


if __name__ == "__main__":
    print(json.dumps(run_matrix(), ensure_ascii=False, indent=2))
