"""诊断脚本：直接打印每天的原始日线ATR%数值（不是乘数），用来确认
"为什么MU和MSFT算出来的止损乘数都是0.5"——是代码有bug，还是这两支股票
这段时间的ATR%确实都在2.0%以上。

配合 _yf_backtest_probe.py 一起用（复用它已经把数据源换成 yfinance 的
monkeypatch），放在同一个目录下（backend/app/user_stocks/）运行：

    python3 backend/app/user_stocks/_atr_pct_diagnose.py MSFT
    python3 backend/app/user_stocks/_atr_pct_diagnose.py MU
"""

import sys
from pathlib import Path
from datetime import date, timedelta

backend = Path(__file__).resolve().parents[2]
if str(backend) not in sys.path:
    sys.path.insert(0, str(backend))

import yfinance as yf  # noqa: E402
from app.services import ohlc as _ohlc_module  # noqa: E402


def _yf_bars_to_dicts(df) -> list[dict]:
    bars = []
    df = df.dropna()
    for idx, row in df.iterrows():
        bars.append({
            "ts": int(idx.timestamp()),
            "open": float(row["Open"]), "high": float(row["High"]),
            "low": float(row["Low"]), "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        })
    return bars


def _yf_fetch_closes(symbol: str, tf: str, apply_live: bool = False) -> list[dict]:
    if tf == "1d":
        df = yf.Ticker(symbol).history(period="2y", interval="1d")
    elif tf == "5m":
        df = yf.Ticker(symbol).history(period="60d", interval="5m")
    else:
        raise ValueError(f"tf={tf} 没实现")
    return _yf_bars_to_dicts(df)


def _yf_fetch_closes_covering(symbol: str, tf: str, start=None) -> list[dict]:
    return _yf_fetch_closes(symbol, tf)


_ohlc_module.fetch_closes = _yf_fetch_closes
_ohlc_module.fetch_closes_covering = _yf_fetch_closes_covering

from app.user_stocks._new_strategy_probe import (  # noqa: E402
    build_daily_atr_series, resolve_stop_loss_atr_mult,
    DAILY_ATR_N, STOP_LOSS_VOL_THRESHOLD_PCT,
)
from app.services.ohlc import fetch_closes  # noqa: E402
from app.services.user_research import wash_bars  # noqa: E402


if __name__ == "__main__":
    sym = sys.argv[1].strip().upper() if len(sys.argv) > 1 else "MSFT"

    raw_d = fetch_closes(sym, "1d", apply_live=False)
    daily = wash_bars(raw_d)
    print(f"拉到 {sym} 日线数据 {len(daily)} 条")

    atr_sorted_dates, atr_date_to_record = build_daily_atr_series(daily, DAILY_ATR_N)
    print(f"ATR 记录条数：{len(atr_sorted_dates)}（DAILY_ATR_N={DAILY_ATR_N}）\n")

    # 打印最近60天（对应 yfinance 5min 数据能覆盖的范围）每天的原始 ATR%、
    # 判断门槛、以及最终解析出的乘数——这样能直接看到是数据问题还是代码问题
    cutoff = date.today() - timedelta(days=60)
    print(f"{'日期':<14}{'ATR%':>10}{'门槛':>8}{'乘数':>8}")
    shown = 0
    for d in atr_sorted_dates:
        if d < cutoff:
            continue
        rec = atr_date_to_record[d]
        mult = resolve_stop_loss_atr_mult(rec["atr_pct"])
        print(f"{str(d):<14}{rec['atr_pct']:>9.3f}%{STOP_LOSS_VOL_THRESHOLD_PCT:>7.1f}%{mult:>8}")
        shown += 1
    if shown == 0:
        print("⚠️ 最近60天没有任何ATR记录，可能是数据没拉到或日期比较问题")

    atr_values = [atr_date_to_record[d]["atr_pct"] for d in atr_sorted_dates if d >= cutoff]
    if atr_values:
        print(f"\n最近60天 ATR% 范围：{min(atr_values):.3f}% ~ {max(atr_values):.3f}%，"
              f"平均 {sum(atr_values)/len(atr_values):.3f}%")
        below = sum(1 for v in atr_values if v < STOP_LOSS_VOL_THRESHOLD_PCT)
        print(f"低于门槛({STOP_LOSS_VOL_THRESHOLD_PCT}%)的天数：{below}/{len(atr_values)}")