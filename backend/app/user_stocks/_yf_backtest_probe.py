"""独立测试脚本：把 _new_strategy_probe.py 的底层数据源换成 yfinance（免费的
Yahoo Finance 接口），实际拉一次"能拿到的最大范围"5分钟数据（yfinance 对日内
数据的限制就是最近60个自然日，这也是它的硬上限，拉不了更多），再跑一遍完整的
回测（含形态过滤），看看在"确实有60天5分钟历史"的情况下效果如何。

为什么要这样写，而不是完全脱离你的项目单独写一份：
    et_day() / et_time() / gradient() / is_rth() 这几个函数（判断交易时段、
    转换成美东时间、算导数）是你项目 app.user_stocks.tsla_golden 里已经写好、
    在正式环境里跑的逻辑，本身可能有一些细节（比如夏令时、盘前盘后过滤）不
    应该我在这里凭空重新猜一遍再实现一次，容易跟你正式环境的行为对不上。所以
    这个脚本只替换"从哪里拿原始K线"这一步（app.services.ohlc 里的两个函数），
    其余全部复用你项目里已经写好、正在用的策略代码。

运行前提：
    1. pip install yfinance --break-system-packages   （如果还没装）
    2. 这个文件要放在能 import app.* 的位置——跟 _new_strategy_probe.py
       同一个目录（backend/app/user_stocks/）就行，脚本会自动把 backend 加进
       sys.path，逻辑跟 _new_strategy_probe.py 顶部处理 sys.path 的方式一样。

用法：
    python3 backend/app/user_stocks/_yf_backtest_probe.py MU
    python3 backend/app/user_stocks/_yf_backtest_probe.py MU --shape-min-train-days 40

⚠️ 需要留意的地方：
    - yfinance 是免费、非官方接口，数据质量（尤其是成交量、盘前盘后处理）可能
      跟你正式用的数据源有差异，这里跑出来的结果只能用来验证"有60天历史时
      形态过滤的表现"，不能直接当成正式回测结果使用。
    - 如果跑起来在 wash_bars() 或 et_day()/et_time() 那一层报错，大概率是
      时间戳单位或字段名跟你项目原本的数据源约定不完全一致，把报错贴给我，
      我再帮你对一下字段格式。
    - 免费接口有请求频率限制，跑太多次/太多个股票可能会被临时限流，报错的话
      等几分钟再试。
"""

import sys
from pathlib import Path

backend = Path(__file__).resolve().parents[2]
if str(backend) not in sys.path:
    sys.path.insert(0, str(backend))

import yfinance as yf
from app.services import ohlc as _ohlc_module  # noqa: E402


def _yf_bars_to_dicts(df) -> list[dict]:
    """把 yfinance 返回的 DataFrame 转成策略代码期望的 bar 格式：
    [{"ts": unix秒, "open":..., "high":..., "low":..., "close":..., "volume":...}, ...]
    """
    bars = []
    df = df.dropna()
    for idx, row in df.iterrows():
        bars.append({
            "ts": int(idx.timestamp()),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        })
    return bars


def _yf_fetch_closes(symbol: str, tf: str, apply_live: bool = False) -> list[dict]:
    if tf == "1d":
        df = yf.Ticker(symbol).history(period="2y", interval="1d")
    elif tf == "5m":
        df = yf.Ticker(symbol).history(period="60d", interval="5m")  # yfinance 日内数据上限就是60天
    else:
        raise ValueError(f"这个测试脚本还没实现 tf={tf}（策略代码目前只会用到 1d 和 5m）")
    return _yf_bars_to_dicts(df)


def _yf_fetch_closes_covering(symbol: str, tf: str, start=None) -> list[dict]:
    # yfinance 的5分钟数据本来就只能拿最近60天，传更早的 start 也没用，
    # 这里直接忽略 start，统一按 yfinance 能给的最大范围拉。
    return _yf_fetch_closes(symbol, tf)


# 关键：必须在 import _new_strategy_probe 之前替换掉，因为策略文件用的是
# `from app.services.ohlc import fetch_closes, fetch_closes_covering`，
# 这种写法会把函数直接绑定到它自己的模块命名空间里，import 完之后再替换
# app.services.ohlc 上的属性就不起作用了。
_ohlc_module.fetch_closes = _yf_fetch_closes
_ohlc_module.fetch_closes_covering = _yf_fetch_closes_covering

from app.user_stocks._new_strategy_probe import run_backtest, _print_report  # noqa: E402


if __name__ == "__main__":
    args = sys.argv[1:]
    sym = args[0].strip().upper() if args and not args[0].startswith("--") else "MU"

    shape_min_train_days = 7
    if "--shape-min-train-days" in args:
        idx = args.index("--shape-min-train-days")
        shape_min_train_days = int(args[idx + 1])

    print(f"===== 用 yfinance 拉 {sym} 最近60天5分钟数据，跑一遍回测（含形态过滤） =====\n")

    result = run_backtest(
        sym,
        backtest_trading_days=60,
        trade_every_signal=True,
        use_shape_filter=True,
        shape_min_train_days=shape_min_train_days,
    )
    _print_report(result)