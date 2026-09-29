"""重来的新策略 · 診断 + 回测，不下单，只打印/统计。

思路：
1. 逐日滚动日线 ATR%，供止损计算（每天用"该日之前"的日线历史重新算）
2. 前 N 个交易日 5min K 线做铺垫，串成连续序列，保证 f'/f'' 从今天开盘头几根就有真实值
3. 一阶导数 f'（价格变化速度，lookback 根滑窗算出）决定涨跌方向；当日 VWAP 仍然
   计算并保留在输出里供参考，但不再参与开仓判断（每天从 0 重新累计，不跨日）
4. 成交量：按时间槽历史分位数（过去 N 天，90/10 分位）+ N 天滚动最高量，任一触发即算"放量"
5. SMA 短/长周期（用铺垫+今天的连续收盘价滚动计算，今天开盘头几根也有真实值）
6. 进场：
   - 做多：一阶导数 f' > 0（涨）+ 放量 -> 进场做多
   - 做空：一阶导数 f' < 0（跌）+ 放量 -> 进场做空；use_sma_precondition=True 时还要求先出现
     "价格站上 SMA 长/短周期、再跌破"的过程（防止在持续弱势、从未站上均线的下跌中盲目
     追空），条件满足开空后该"站上"状态会被消费掉，下一次开空需要重新出现该过程；
     use_sma_precondition=False 时做空和做多完全对称，不看 SMA。
7. 止盈：二阶导数 f'' 反转信号（f' 变号 + f'' 同向）+ 放量 -> 真反转，平仓（不设时间
   限制，让盈利充分走）
8. 止损：入场后每根检查 high/low 是否触及止损线（= stop_loss_atr_mult × 当天生效的日线
   ATR 美元），触及即平仓；若发生隔夜跳空导致开盘就已经越过止损线，按跳空后的开盘价成交
9. 仓位可以跨日持有（不再收盘前强平），只有止损或真反转信号才会平仓
10. 回测支持两种模式：
    - 不指定日期：回测最近 backtest_trading_days 个交易日
    - 指定日期区间：只统计区间内的交易日，便于按月分段测试
      （区间之前会自动多拉一段历史做铺垫，铺垫段不计入统计；
       如果数据源实际覆盖不到你要的区间，会打印/返回出实际能覆盖的范围）
11. 每天的信号数量和信号准确度（按开仓日期统计的胜率），方便按天判断信号质量的
    波动，而不是只看整个回测区间的总胜率。

命令行用法：
    python3 backend/app/user_stocks/_new_strategy_probe.py MU
        -> 回测最近 backtest_trading_days 个交易日
    python3 backend/app/user_stocks/_new_strategy_probe.py MU 2026-07-01 2026-07-31
        -> 只回测这个日期区间内的交易日（用于按月分段测试）
    python3 backend/app/user_stocks/_new_strategy_probe.py MU --all-signals
        -> 每一次买/卖信号都执行交易（不再要求"空仓才进场"），用于测算"如果每笔信号
           都交易，胜率是多少"
    python3 backend/app/user_stocks/_new_strategy_probe.py MU --detail 2026-08-05
        -> 打印 2026-08-05 这天每一根 5min bar 的完整明细（开高低收/成交量/VWAP/
           SMA/是否触发信号等），用来诊断"这天为什么会/不会出现某个信号"；也可以
           用逗号一次看多天，如 --detail 2026-08-05,2026-08-06
    python3 backend/app/user_stocks/_new_strategy_probe.py MU --predict-shape
        -> 用历史数据训练一个多元逻辑回归，预测"今天"最终会落进6种VWAP轨道形态
           （下跌横盘/上涨/上涨下跌/下跌上涨/下跌/上涨横盘）里的哪一种，给出概率
    python3 backend/app/user_stocks/_new_strategy_probe.py MU --shape-filter
        -> 回测时启用形态过滤：每天开盘前用历史数据预测当天的VWAP轨道形态，按
           形态决定当天/当天某个阶段只做多、只做空、双向都开、还是观望不开新仓
           （具体对应关系见 SHAPE_DAY_PLAN）

所有参数都通过 run_backtest() 的关键字参数传入，模块顶部的常量只是命令行模式的默认值，
Web API 会把每个参数暴露成可选表单项。
"""

from __future__ import annotations

import bisect
import math
import pickle
import sys
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

backend = Path(__file__).resolve().parents[2]
if str(backend) not in sys.path:
    sys.path.insert(0, str(backend))

from app.services.ohlc import OhlcError, fetch_closes, fetch_closes_covering
from app.services.user_research import wash_bars
from app.user_stocks.tsla_golden import et_day, et_time, gradient, is_rth

# crypto 模式：按 UTC 日切分、全天候交易（不套美股 RTH）
_market_tls = threading.local()


@contextmanager
def market_mode(market: str = "stock", timeframe: str = "5m"):
    prev_m = getattr(_market_tls, "market", "stock")
    prev_tf = getattr(_market_tls, "timeframe", "5m")
    _market_tls.market = (market or "stock").strip().lower() or "stock"
    tf = (timeframe or "5m").strip().lower()
    if tf in ("5min", "5"):
        tf = "5m"
    elif tf in ("30min", "30"):
        tf = "30m"
    elif tf in ("60m", "60min", "1hour"):
        tf = "1h"
    elif tf in ("1day", "d", "day"):
        tf = "1d"
    if tf not in ("5m", "30m", "1h", "1d"):
        tf = "5m"
    _market_tls.timeframe = tf
    try:
        yield _market_tls.market
    finally:
        _market_tls.market = prev_m
        _market_tls.timeframe = prev_tf


def _is_crypto() -> bool:
    return getattr(_market_tls, "market", "stock") == "crypto"


def _bar_tf() -> str:
    return getattr(_market_tls, "timeframe", "5m") or "5m"


def _bar_minutes() -> int:
    return {"5m": 5, "30m": 30, "1h": 60, "1d": 1440}.get(_bar_tf(), 5)


def session_day(ts: int):
    if _is_crypto():
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
    return et_day(ts)


def session_time(ts: int) -> str:
    if _is_crypto():
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%H:%M")
    return et_time(ts)


def in_session(ts: int) -> bool:
    if _is_crypto():
        return True
    return is_rth(ts)

SYMBOL = "MU"
LOOKBACK = 5  # 一阶/二阶导数的滑窗根数
CONTEXT_DAYS = 3  # 铺垫天数（给 f'/f''、SMA 提供跨日连续序列）
VWAP_TREND_LOOKBACK = 6  # 用当前 VWAP 对比 N 根之前的 VWAP 判断趋势方向（6 根 ≈ 30 分钟）
VOLUME_DIST_DAYS = 15  # 用过去 N 个交易日（不含今天）建立每个时间槽的成交量分布
VOLUME_HIGH_PCT = 90  # 分位数 >= 此值算"放量"
VOLUME_LOW_PCT = 10  # 分位数 <= 此值算"缩量"
MIN_SLOT_SAMPLES = 8  # 该时间槽历史样本数不足此值时，不下判断
VOLUME_MAX_WINDOW_DAYS = 14  # 用过去 N 个交易日（不含今天）所有 5min bar 找全局最大成交量
SMA_SHORT_PERIOD = 10
SMA_LONG_PERIOD = 20
USE_SMA_PRECONDITION = True  # 做空是否要求先"站上再跌破" SMA；关掉后做空跟做多完全对称
ALLOW_SHORT = True  # 允许做空；有些票（不方便融券/不想做空）可以直接关掉，只做多
DAILY_ATR_N = 14  # 日线 ATR 窗口期数
STOP_LOSS_ATR_MULT = 0.5  # 止损空间 = 此倍数 × 当天生效的日线 ATR(美元)
BACKTEST_TRADING_DAYS = 21  # 不指定日期区间时，默认回测最近多少个交易日
CAPITAL_PER_TRADE = 20000.0  # 起始本金
COMPOUND = True  # 复利：每笔用当前账户权益算仓位（本金随盈亏滚动），关掉则每笔都固定按起始本金算
TRADE_EVERY_SIGNAL = False  # True 时：只要买/卖信号触发就交易——如果当前反向持仓则先平仓再反手，
                            # 不再要求"必须空仓才能进场"；同向信号（已经是对应方向仓位）不重复开仓
CLOSE_NO_TRADE_MINUTES = 0  # 收盘前这么多分钟内不再开新仓（0 表示关闭，默认关闭）；
                             # 这是全局规则，不分形态、对所有交易日生效——如果只想让某个
                             # 特定形态收盘前不开仓（比如"上涨"），不要用这个，改在
                             # SHAPE_UPTREND_CLOSE_NO_TRADE_MINUTES 那条设置

# "上涨"形态三态强弱判断用的参数（强上涨 / 普通上涨衰竭 / 反转确认，详见 walk_forward
# 里对 SHAPE_DAY_PLAN["上涨"] 的处理）
UPTREND_EMA_SHORT_PERIOD = 20  # 强上涨判断用的短周期EMA
UPTREND_EMA_LONG_PERIOD = 50  # 强上涨判断用的长周期EMA
UPTREND_ADX_PERIOD = 14  # ADX 窗口期数
UPTREND_ADX_THRESHOLD = 20  # ADX 高于这个值算"趋势够强"
UPTREND_VWAP_DIST_LOOKBACK = 6  # 判断"价格离VWAP越来越远"时，跟几根之前的距离比较（6根≈30分钟）
UPTREND_VOLUME_Z_LOOKBACK = 20  # 成交量 z-score 用过去多少根算均值/标准差
UPTREND_VOLUME_Z_THRESHOLD = 1.0  # 反转确认要求成交量 z-score 超过这个值
UPTREND_STRUCTURE_LOOKBACK = 10  # 判断"跌破短期结构"时，看过去多少根的最低点
UPTREND_MARKET_SYMBOL = "SPY"  # 强上涨判断用的大盘参照标的；设为 None/空字符串可以关掉这条限制
UPTREND_MARKET_LOOKBACK = 5  # 算大盘方向时，用最近几根收盘价的斜率

# "横盘"形态四态细分用的参数（窄横盘/宽横盘/横盘收缩/横盘扩张）
SIDEWAYS_WIDTH_LOOKBACK = 6  # 判断"区间在收缩还是在扩张"时，跟几根之前的区间宽度比较
SIDEWAYS_NARROW_THRESHOLD_PCT = 1.0  # 区间宽度（占价格百分比）低于这个值算"窄横盘"
SIDEWAYS_WIDTH_CHANGE_RATIO = 0.25  # 区间宽度变化超过这个比例（25%）才算"收缩"/"扩张"，不到算"稳定"
SIDEWAYS_RANGE_ZONE_FRACTION = 0.33  # "宽横盘"里，收盘价落在区间最下面/最上面这个比例算"下沿"/"上沿"



# ---------------------------------------------------------------------------
# 基础指标
# ---------------------------------------------------------------------------

def true_range(bars: list[dict], i: int) -> float:
    """计算第 i 根 bar 的 True Range（真实波幅）。"""
    bar = bars[i]
    if i < 1:
        return max(bar["high"] - bar["low"], 1e-9)
    prev_c = bars[i - 1]["close"]
    return max(bar["high"] - bar["low"], abs(bar["high"] - prev_c), abs(bar["low"] - prev_c))


def daily_atr_pct(daily_bars: list[dict], n: int) -> float | None:
    """计算最近 n 期日线 ATR 占最新收盘价的百分比。"""
    if len(daily_bars) < n + 1:
        return None
    trs = [true_range(daily_bars, i) for i in range(len(daily_bars))]
    atr = sum(trs[-n:]) / n
    last_price = daily_bars[-1]["close"]
    return atr / last_price * 100 if last_price else None


def build_daily_atr_series(daily_bars: list[dict], n: int) -> tuple[list, dict]:
    """把日线 bar 列表转换成"逐日滚动 ATR%"，供查询"该日之前"的止损基准用。"""
    sorted_dates: list = []
    date_to_record: dict = {}
    for idx in range(n, len(daily_bars)):
        window = daily_bars[: idx + 1]
        pct = daily_atr_pct(window, n)
        if pct is None:
            continue
        d = session_day(daily_bars[idx]["ts"])
        sorted_dates.append(d)
        date_to_record[d] = {"atr_pct": pct, "close": daily_bars[idx]["close"]}
    return sorted_dates, date_to_record


def atr_dollar_asof(sorted_dates: list, date_to_record: dict, target_day, mult: float) -> float | None:
    """查询"严格早于 target_day"的最近一条 ATR 记录，换算成美元止损空间。"""
    pos = bisect.bisect_left(sorted_dates, target_day)
    if pos == 0:
        return None
    d = sorted_dates[pos - 1]
    rec = date_to_record[d]
    atr_dollar = rec["atr_pct"] / 100 * rec["close"]
    return atr_dollar * mult


def bar_map(bars: list[dict]) -> dict[int, dict]:
    """{ts: bar}，配合 bar_at() 按时间戳查另一只票同一时刻的 bar（做空走 ETF 时用）。"""
    return {int(bar["ts"]): bar for bar in bars}


def bar_at(index: dict[int, dict], ts: int, stamps: list[int]) -> dict | None:
    """找 <= ts 的最近一根 bar（asof 查找），两边数据没有逐根精确对齐时也能用。"""
    j = bisect.bisect_right(stamps, int(ts)) - 1
    if j < 0:
        return None
    return index.get(stamps[j])


def trading_days(bars: list[dict], indices: list[int]) -> list:
    """从给定下标中提取出按时间升序排列、去重后的交易日列表（RTH 部分）。"""
    seen = []
    for i in indices:
        d = session_day(bars[i]["ts"])
        if d not in seen:
            seen.append(d)
    return seen


def running_vwap(bars: list[dict], indices: list[int]) -> list[float | None]:
    """按给定顺序逐根累计计算当日 VWAP（成交量加权平均价）。"""
    vwap_num = vwap_den = 0.0
    out: list[float | None] = []
    for i in indices:
        bar = bars[i]
        typical = (bar["high"] + bar["low"] + bar["close"]) / 3.0
        vol = max(float(bar.get("volume") or 0.0), 1.0)
        vwap_num += typical * vol
        vwap_den += vol
        out.append(vwap_num / vwap_den if vwap_den else None)
    return out


def vwap_trend(vwap_today: list[float | None], pos: int, lookback: int) -> str:
    """判断 VWAP 自身的趋势方向：当前 VWAP 相对 N 根之前的 VWAP。"""
    if pos < lookback:
        return "—"
    now = vwap_today[pos]
    ref = vwap_today[pos - lookback]
    if now is None or ref is None:
        return "—"
    if now > ref:
        return "涨"
    if now < ref:
        return "跌"
    return "平"


def percentile(values: list[float], pct: float) -> float | None:
    """用线性插值法计算给定百分位数（不依赖 numpy）。"""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    d0 = s[int(f)] * (c - k)
    d1 = s[int(c)] * (k - f)
    return d0 + d1


def build_slot_volume_history(bars: list[dict], hist_day_indices: dict) -> dict[str, list[float]]:
    """按"时间槽"（如 09:30、09:35...）汇总过去若干个历史交易日的成交量。"""
    slots: dict[str, list[float]] = {}
    for _day, idxs in hist_day_indices.items():
        for i in idxs:
            t = session_time(bars[i]["ts"])
            slots.setdefault(t, []).append(float(bars[i].get("volume") or 0.0))
    return slots


def slot_thresholds(
    slot_history: dict[str, list[float]], volume_high_pct: float, volume_low_pct: float, min_slot_samples: int
) -> dict[str, tuple[float, float, int]]:
    """把每个时间槽的历史成交量列表，转换成 (高分位阈值, 低分位阈值, 样本数)。"""
    out: dict[str, tuple[float, float, int]] = {}
    for t, vols in slot_history.items():
        if len(vols) < min_slot_samples:
            continue
        hi = percentile(vols, volume_high_pct)
        lo = percentile(vols, volume_low_pct)
        if hi is not None and lo is not None:
            out[t] = (hi, lo, len(vols))
    return out


def classify_volume(volume: float, thresholds: dict[str, tuple[float, float, int]], time_slot: str) -> str:
    """用当前时间槽的历史分位阈值，判断这一根成交量是"放量"/"缩量"/"正常"。"""
    entry = thresholds.get(time_slot)
    if entry is None:
        return "—"
    hi, lo, _n = entry
    if volume >= hi:
        return "放量"
    if volume <= lo:
        return "缩量"
    return "正常"


def initial_rolling_max_volume(bars: list[dict], hist_day_indices: dict) -> float:
    """计算过去 N 个交易日（不含今天）所有 5min bar 里成交量的全局最大值。"""
    max_vol = 0.0
    for _day, idxs in hist_day_indices.items():
        for i in idxs:
            v = float(bars[i].get("volume") or 0.0)
            if v > max_vol:
                max_vol = v
    return max_vol


def rolling_sma(closes: list[float], period: int) -> list[float | None]:
    """计算简单移动平均线（SMA），窗口不足 period 根时返回 None。"""
    out: list[float | None] = []
    window_sum = 0.0
    for idx, c in enumerate(closes):
        window_sum += c
        if idx >= period:
            window_sum -= closes[idx - period]
        if idx >= period - 1:
            out.append(window_sum / period)
        else:
            out.append(None)
    return out


def ema_series(closes: list[float], period: int) -> list[float | None]:
    """计算指数移动平均线（EMA）。前 period-1 根返回 None；第 period 根用简单均值
    作为种子，之后按标准 EMA 公式（k = 2/(period+1)）滚动更新。
    """
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(closes)):
        prev = closes[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def adx_series(bars: list[dict], idx_list: list[int], period: int) -> list[float | None]:
    """计算 ADX（平均趋向指标，Wilder 平滑），返回跟 idx_list 一一对应的序列。
    前 2×period 根左右会是 None（TR/+DM/-DM 需要先累积 period 根种子，DX 的
    Wilder 平滑又需要再攒 period 根才有第一个 ADX 值）。ADX 本身不分涨跌方向，
    只衡量"趋势有多强"——搭配 +DI/-DI 才能判断方向，这里只用 ADX 的数值本身
    （配合 f' 的正负号表达方向）。
    """
    n = len(idx_list)
    out: list[float | None] = [None] * n
    if n < period * 2 + 1:
        return out

    trs, plus_dms, minus_dms = [], [], []
    for pos in range(n):
        bar = bars[idx_list[pos]]
        if pos == 0:
            trs.append(max(bar["high"] - bar["low"], 1e-9))
            plus_dms.append(0.0)
            minus_dms.append(0.0)
            continue
        prev_bar = bars[idx_list[pos - 1]]
        tr = max(
            bar["high"] - bar["low"],
            abs(bar["high"] - prev_bar["close"]),
            abs(bar["low"] - prev_bar["close"]),
        )
        up_move = bar["high"] - prev_bar["high"]
        down_move = prev_bar["low"] - bar["low"]
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    # Wilder 平滑：先用 period 根简单和做种子，之后 smoothed = prev - prev/period + new
    def wilder_smooth(values: list[float]) -> list[float | None]:
        sm: list[float | None] = [None] * n
        if n <= period:
            return sm
        seed = sum(values[1:period + 1])
        sm[period] = seed
        prev = seed
        for pos in range(period + 1, n):
            prev = prev - prev / period + values[pos]
            sm[pos] = prev
        return sm

    tr_smooth = wilder_smooth(trs)
    plus_dm_smooth = wilder_smooth(plus_dms)
    minus_dm_smooth = wilder_smooth(minus_dms)

    dx: list[float | None] = [None] * n
    for pos in range(n):
        if tr_smooth[pos] is None or tr_smooth[pos] == 0:
            continue
        plus_di = 100 * plus_dm_smooth[pos] / tr_smooth[pos]
        minus_di = 100 * minus_dm_smooth[pos] / tr_smooth[pos]
        denom = plus_di + minus_di
        if denom > 0:
            dx[pos] = 100 * abs(plus_di - minus_di) / denom

    first_dx_pos = period
    adx_seed_pos = first_dx_pos + period
    if adx_seed_pos >= n:
        return out
    dx_seed_vals = [v for v in dx[first_dx_pos:adx_seed_pos] if v is not None]
    if len(dx_seed_vals) < period:
        return out
    prev_adx = sum(dx_seed_vals) / period
    out[adx_seed_pos] = prev_adx
    for pos in range(adx_seed_pos + 1, n):
        if dx[pos] is None:
            out[pos] = prev_adx
            continue
        prev_adx = (prev_adx * (period - 1) + dx[pos]) / period
        out[pos] = prev_adx
    return out


def rolling_zscore(values: list[float], lookback: int) -> list[float | None]:
    """给定一串数值，逐个位置算"相对前 lookback 根的 z-score"（不含自己）。
    前 lookback 根数据不够，返回 None。用来判断"这根成交量是不是明显异常放大"
    （volume z-score），比单纯的历史分位数更敏感一些短期的剧烈变化。
    """
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(n):
        if i < lookback:
            continue
        window = values[i - lookback:i]
        mean = sum(window) / lookback
        var = sum((v - mean) ** 2 for v in window) / lookback
        std = math.sqrt(var)
        out[i] = (values[i] - mean) / std if std > 1e-9 else 0.0
    return out


def stop_hit_price(side: str, stop_price: float, bar: dict) -> float | None:
    """判断这根 bar 是否触及止损，并返回实际应成交的价格（考虑跳空打滑）。"""
    if side == "多":
        if bar["open"] <= stop_price:
            return bar["open"]
        if bar["low"] <= stop_price:
            return stop_price
    else:
        if bar["open"] >= stop_price:
            return bar["open"]
        if bar["high"] >= stop_price:
            return stop_price
    return None


def minutes_to_last_bar(time_slot: str) -> int:
    """给实盘用：这根 K 线距离当天最后一根还有多少分钟。

    美股：最后一根约 15:55 ET；虚拟币：UTC 日最后一根约 23:55。
    """
    h, m = (int(x) for x in time_slot.split(":"))
    end_h, end_m = (23, 55) if _is_crypto() else (15, 55)
    return max(0, (end_h * 60 + end_m) - (h * 60 + m))


def market_direction_asof(
    market_index: dict[int, dict], market_stamps: list[int], ts: int, lookback: int
) -> str | None:
    """用大盘/参照标的（比如SPY）在某个时间戳附近的最近 lookback 根收盘价，算一个
    简单的方向（涨/跌/平），给"上涨"形态的强弱判断用（SPY方向一致这个条件）。
    market_stamps 为空、或者这个时间点前数据不够时返回 None——调用方应把 None
    当成"没有大盘数据，这条限制不参与判断"处理，不强行卡住整个流程。
    """
    if not market_stamps:
        return None
    j = bisect.bisect_right(market_stamps, int(ts)) - 1
    if j < lookback:
        return None
    closes = [market_index[market_stamps[k]]["close"] for k in range(j - lookback + 1, j + 1)]
    speed = gradient(closes)
    f1 = speed[-1]
    if f1 > 0:
        return "涨"
    if f1 < 0:
        return "跌"
    return "平"


# ---------------------------------------------------------------------------
# 单日信号计算（不做进出场决策，因为仓位可能跨日，出场逻辑统一在 walk_forward 里处理）
# ---------------------------------------------------------------------------

def compute_day_signals(
    bars_5: list[dict],
    today_idx: list[int],
    series_idx: list[int],
    thresholds: dict[str, tuple[float, float, int]],
    rolling_max_init: float,
    lookback: int,
    vwap_trend_lookback: int,
    sma_short_period: int,
    sma_long_period: int,
    ema_short_period: int = UPTREND_EMA_SHORT_PERIOD,
    ema_long_period: int = UPTREND_EMA_LONG_PERIOD,
    adx_period: int = UPTREND_ADX_PERIOD,
    vwap_dist_lookback: int = UPTREND_VWAP_DIST_LOOKBACK,
    volume_z_lookback: int = UPTREND_VOLUME_Z_LOOKBACK,
    structure_lookback: int = UPTREND_STRUCTURE_LOOKBACK,
) -> list[dict]:
    """逐根计算当天每个 5min bar 的指标和信号，返回结构化列表供 walk_forward 使用。

    ema_short/ema_long/adx/vwap_dist_pct/vwap_dist_growing/is_new_high/
    new_high_volume_confirmed/volume_zscore/recent_low/close_below_recent_low
    这些是专门给"上涨"形态的三态强弱判断（强上涨/普通上涨衰竭/反转确认）用的，
    其他形态用不到，但为了逻辑简单统一在这里算，不额外为"上涨"形态单独跑一遍。
    """
    vwap_values = running_vwap(bars_5, today_idx)
    series_pos_of = {i: p for p, i in enumerate(series_idx)}
    rolling_max = rolling_max_init

    series_closes = [bars_5[j]["close"] for j in series_idx]
    series_lows = [bars_5[j]["low"] for j in series_idx]
    series_highs = [bars_5[j]["high"] for j in series_idx]
    series_volumes = [float(bars_5[j].get("volume") or 0.0) for j in series_idx]
    sma_short_series = rolling_sma(series_closes, sma_short_period)
    sma_long_series = rolling_sma(series_closes, sma_long_period)
    ema_short_series = ema_series(series_closes, ema_short_period)
    ema_long_series = ema_series(series_closes, ema_long_period)
    adx_series_vals = adx_series(bars_5, series_idx, adx_period)
    volume_z_series = rolling_zscore(series_volumes, volume_z_lookback)

    day_high = None
    day_high_volume = 0.0
    day_low = None
    day_low_volume = 0.0
    range_width_history: list[float | None] = []  # 只在今天内部比较，不跨日

    out = []
    for pos_today, i in enumerate(today_idx):
        pos_series = series_pos_of[i]
        bar = bars_5[i]
        time_slot = session_time(bar["ts"])
        vtrend = vwap_trend(vwap_values, pos_today, vwap_trend_lookback)
        vwap_now = vwap_values[pos_today]

        volume = float(bar.get("volume") or 0.0)
        vol_state = classify_volume(volume, thresholds, time_slot)
        is_new_record = volume > rolling_max
        if is_new_record:
            rolling_max = volume
        vol_high = vol_state == "放量" or is_new_record

        turning = ""
        dtrend = "—"
        f1 = f2 = None
        if pos_series >= lookback - 1:
            window = [bars_5[j]["close"] for j in range(i - lookback + 1, i + 1)]
            speed = gradient(window)
            accel = gradient(speed)
            f1 = speed[-1]
            f2 = accel[-1]
            prev = speed[-2] if len(speed) >= 2 else 0.0
            if prev < 0 and f1 >= 0 and f2 > 0:
                turning = "转多"
            elif prev > 0 and f1 <= 0 and f2 < 0:
                turning = "转空"
            # 一阶导数（f1，价格变化速度）决定涨跌方向，用于开仓判断
            if f1 > 0:
                dtrend = "涨"
            elif f1 < 0:
                dtrend = "跌"
            else:
                dtrend = "平"

        ema_short = ema_short_series[pos_series]
        ema_long = ema_long_series[pos_series]
        ema_short_rising = (
            pos_series > 0 and ema_short_series[pos_series - 1] is not None and ema_short is not None
            and ema_short > ema_short_series[pos_series - 1]
        )
        ema_short_falling = (
            pos_series > 0 and ema_short_series[pos_series - 1] is not None and ema_short is not None
            and ema_short < ema_short_series[pos_series - 1]
        )
        adx = adx_series_vals[pos_series]

        vwap_dist_pct = ((bar["close"] - vwap_now) / vwap_now * 100) if vwap_now else None
        vwap_dist_growing = False
        if vwap_dist_pct is not None and pos_today >= vwap_dist_lookback:
            prior_vwap = vwap_values[pos_today - vwap_dist_lookback]
            prior_bar = bars_5[today_idx[pos_today - vwap_dist_lookback]]
            if prior_vwap:
                prior_dist = (prior_bar["close"] - prior_vwap) / prior_vwap * 100
                same_sign = (vwap_dist_pct > 0 and prior_dist > 0) or (vwap_dist_pct < 0 and prior_dist < 0)
                vwap_dist_growing = same_sign and abs(vwap_dist_pct) > abs(prior_dist)

        # 当天（不跨日）新高 + 新高时成交量有没有同步放大
        is_new_high = day_high is None or bar["close"] > day_high
        new_high_volume_confirmed = True
        if is_new_high and day_high is not None:
            new_high_volume_confirmed = volume >= day_high_volume
        if is_new_high:
            day_high = bar["close"]
            day_high_volume = volume

        # 当天（不跨日）新低 + 新低时成交量有没有同步放大（"下跌"形态衰竭判断用）
        is_new_low = day_low is None or bar["close"] < day_low
        new_low_volume_confirmed = True
        if is_new_low and day_low is not None:
            new_low_volume_confirmed = volume >= day_low_volume
        if is_new_low:
            day_low = bar["close"]
            day_low_volume = volume

        volume_zscore = volume_z_series[pos_series]

        recent_low = None
        close_below_recent_low = False
        recent_high = None
        close_above_recent_high = False
        if pos_series >= structure_lookback:
            recent_low = min(series_lows[pos_series - structure_lookback:pos_series])
            close_below_recent_low = bar["close"] < recent_low
            recent_high = max(series_highs[pos_series - structure_lookback:pos_series])
            close_above_recent_high = bar["close"] > recent_high

        # 区间宽度（占价格百分比）+ 宽度是在收缩还是扩张——"横盘"四态细分用
        range_width_pct = None
        if recent_high is not None and recent_low is not None and bar["close"]:
            range_width_pct = (recent_high - recent_low) / bar["close"] * 100
        range_width_history.append(range_width_pct)
        width_trend = None
        if range_width_pct is not None and pos_today >= vwap_dist_lookback:
            prior_width = range_width_history[pos_today - vwap_dist_lookback]
            if prior_width is not None and prior_width > 0:
                change_ratio = (range_width_pct - prior_width) / prior_width
                if change_ratio > SIDEWAYS_WIDTH_CHANGE_RATIO:
                    width_trend = "expanding"
                elif change_ratio < -SIDEWAYS_WIDTH_CHANGE_RATIO:
                    width_trend = "compressing"
                else:
                    width_trend = "stable"

        out.append({
            "idx": i,
            "bar": bar, "time_slot": time_slot, "turning": turning,
            "vwap": vwap_now, "vtrend": vtrend,
            "dtrend": dtrend, "f1": f1, "f2": f2,
            "vol_high": vol_high, "vol_state": vol_state, "is_new_record": is_new_record,
            "sma_short": sma_short_series[pos_series], "sma_long": sma_long_series[pos_series],
            "ema_short": ema_short, "ema_long": ema_long,
            "ema_short_rising": ema_short_rising, "ema_short_falling": ema_short_falling,
            "adx": adx,
            "vwap_dist_pct": vwap_dist_pct, "vwap_dist_growing": vwap_dist_growing,
            "is_new_high": is_new_high, "new_high_volume_confirmed": new_high_volume_confirmed,
            "is_new_low": is_new_low, "new_low_volume_confirmed": new_low_volume_confirmed,
            "volume_zscore": volume_zscore,
            "recent_low": recent_low, "close_below_recent_low": close_below_recent_low,
            "recent_high": recent_high, "close_above_recent_high": close_above_recent_high,
            "range_width_pct": range_width_pct, "width_trend": width_trend,
        })
    return out


# ---------------------------------------------------------------------------
# 三态趋势线（虚拟币）：急涨/急跌（一小时直线拉/砸）、横盘、普通趋势
# ---------------------------------------------------------------------------

REGIME_ATR_N = 14  # K 线级 ATR 周期（不是日线 ATR）
REGIME_IMPULSE_MINUTES = 60  # 急涨/急跌看多长时间的走势
REGIME_IMPULSE_ER = 0.6  # 走势"直"的程度：净位移 / 路径总长，1 = 一根直线
REGIME_IMPULSE_ATR = 4.0  # 净位移 ≥ 这么多倍 K 线 ATR 才算急涨/急跌
REGIME_TREND_BARS = 48  # 趋势线（线性回归）用多少根 K 线
REGIME_SIDEWAYS_ER = 0.25  # 趋势窗口的效率比低于它算横盘
REGIME_SIDEWAYS_ADX = 20.0  # 同时 ADX 低于它才算横盘
REGIME_CHANNEL_K = 1.5  # 通道宽度 = 趋势线 ± K × 回归残差标准差
REGIME_TRAIL_ATR = 4.0  # 急涨急跌/趋势仓位的跟踪止损 = 最有利收盘价 ∓ 这么多倍 K 线 ATR
REGIME_IMPULSE_GIVEBACK = 0.4  # 急涨急跌仓位回吐这一段拉升/砸盘幅度的比例就走
REGIME_TREND_BREAK_ATR = 0.25  # 收盘跌破/升破趋势线这么多倍 ATR 才算破线
REGIME_RANGE_STOP_ATR = 1.0  # 横盘高抛低吸的止损 = 通道外沿再加这么多倍 ATR

REGIME_UP = "急涨"
REGIME_DOWN = "急跌"
REGIME_SIDEWAYS = "横盘"
REGIME_TREND_UP = "上升趋势"
REGIME_TREND_DOWN = "下降趋势"


def _linreg_tail(values: list[float]) -> tuple[float, float, float]:
    """对 values 做最小二乘直线，返回 (斜率/根, 末端直线值, 残差标准差)。"""
    n = len(values)
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    sxx = sum((k - mean_x) ** 2 for k in range(n))
    sxy = sum((k - mean_x) * (values[k] - mean_y) for k in range(n))
    slope = sxy / sxx if sxx else 0.0
    intercept = mean_y - slope * mean_x
    resid = [values[k] - (intercept + slope * k) for k in range(n)]
    std = math.sqrt(sum(r * r for r in resid) / n) if n else 0.0
    return slope, intercept + slope * (n - 1), std


def build_regime_map(
    bars: list[dict],
    idx_list: list[int],
    *,
    atr_n: int = REGIME_ATR_N,
    impulse_bars: int = 12,
    impulse_er: float = REGIME_IMPULSE_ER,
    impulse_atr: float = REGIME_IMPULSE_ATR,
    trend_bars: int = REGIME_TREND_BARS,
    sideways_er: float = REGIME_SIDEWAYS_ER,
    sideways_adx: float = REGIME_SIDEWAYS_ADX,
    channel_k: float = REGIME_CHANNEL_K,
) -> dict[int, dict]:
    """沿 idx_list（连续 K 线序列，虚拟币 24h 不断档）逐根判定所处状态。

    只用这一根及之前的数据，没有未来函数。返回 {bar 下标: 状态字典}。
    """
    closes = [bars[i]["close"] for i in idx_list]
    n = len(idx_list)
    tr = [0.0] * n
    for p, i in enumerate(idx_list):
        b = bars[i]
        if p == 0:
            tr[p] = b["high"] - b["low"]
        else:
            pc = closes[p - 1]
            tr[p] = max(b["high"] - b["low"], abs(b["high"] - pc), abs(b["low"] - pc))
    path = [0.0] * n
    for p in range(1, n):
        path[p] = path[p - 1] + abs(closes[p] - closes[p - 1])
    adx_vals = adx_series(bars, idx_list, 14)

    out: dict[int, dict] = {}
    tr_sum = 0.0
    for p, i in enumerate(idx_list):
        tr_sum += tr[p]
        if p >= atr_n:
            tr_sum -= tr[p - atr_n]
        if p < max(atr_n, impulse_bars, trend_bars):
            continue
        atr = tr_sum / atr_n
        if atr <= 0:
            continue

        move = closes[p] - closes[p - impulse_bars]
        fast_path = path[p] - path[p - impulse_bars]
        er_fast = abs(move) / fast_path if fast_path > 0 else 0.0
        move_atr = abs(move) / atr

        window = closes[p - trend_bars + 1 : p + 1]
        slope, line, std = _linreg_tail(window)
        slow_move = closes[p] - closes[p - trend_bars + 1]
        slow_path = path[p] - path[p - trend_bars + 1]
        er_slow = abs(slow_move) / slow_path if slow_path > 0 else 0.0
        adx = adx_vals[p]

        if er_fast >= impulse_er and move_atr >= impulse_atr:
            regime = REGIME_UP if move > 0 else REGIME_DOWN
        elif er_slow < sideways_er and (adx is None or adx < sideways_adx):
            regime = REGIME_SIDEWAYS
        else:
            regime = REGIME_TREND_UP if slope > 0 else REGIME_TREND_DOWN

        band = max(std * channel_k, atr * 0.5)
        out[i] = {
            "regime": regime,
            "atr": atr,
            "line": line,
            "upper": line + band,
            "lower": line - band,
            "slope": slope,
            "slope_atr": slope * trend_bars / atr,
            "er_fast": er_fast,
            "er_slow": er_slow,
            "move_atr": move_atr,
            "leg_start": closes[p - impulse_bars],
            "adx": adx,
        }
    return out


# ---------------------------------------------------------------------------
# 跨日连续 walk-forward：仓位可以跨日持有，只有止损/真反转才平仓
# ---------------------------------------------------------------------------

def walk_forward(
    bars_5: list[dict],
    day_setups: list[dict],
    lookback: int,
    vwap_trend_lookback: int,
    sma_short_period: int,
    sma_long_period: int,
    use_sma_precondition: bool,
    allow_short: bool,
    capital_per_trade: float,
    primary_symbol: str,
    short_symbol: str | None = None,
    etf_index: dict[int, dict] | None = None,
    etf_stamps: list[int] | None = None,
    compound: bool = True,
    trade_every_signal: bool = False,
    debug_days: set[str] | None = None,
    close_no_trade_minutes: int = CLOSE_NO_TRADE_MINUTES,
    market_index: dict[int, dict] | None = None,
    market_stamps: list[int] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """按交易日顺序连续模拟，仓位可以跨日持有。

    allow_short=False 时完全不开空单，只做多——有些票不方便融券或不想做空，直接关掉。
    做空前提（allow_short=True 且 use_sma_precondition=True 时）：必须先出现"收盘价
    站上 SMA 长/短周期、再跌破"的过程，才允许开空；一旦真正开出空单，"站上"状态会被
    消费掉，下一次开空需要重新出现该过程，不能吃老本。use_sma_precondition=False 时
    做空跟做多完全对称，只看一阶导数（f'）方向 + 放量。

    trade_every_signal=False（默认，原有行为）：只有当前空仓时才会响应买/卖信号进场；
    如果已经持有反向仓位，买/卖信号会被完全忽略（signal_log 里记为 taken=False），
    要等现有仓位先被止损或"真反转"平掉，下一根才有机会进场。

    trade_every_signal=True：只要买/卖信号触发就一定成交——如果当前持有反向仓位，
    先按当前 bar 价格平掉那笔仓位（记一笔平仓交易，reason="信号反手"），再立即反手
    开新仓（记一笔开仓）；如果当前空仓，直接开仓；如果当前已经持有同方向仓位，则视为
    该信号已经被满足，不重复开仓（不会产生"自己平自己再开"的零盈亏假交易）。用这个
    模式可以回答"如果我对每一次信号都采取行动（不因为仓位状态而跳过），统计出来的
    胜率/交易笔数是多少"。

    short_symbol 给了值时：方向信号（一阶导数、放量、SMA 前提、二阶导数反转）全部
    还是看 primary_symbol 自己的走势，但实际开/平空单的价格、止损检查，都换成
    short_symbol（一般是 ETF）在同一时间戳的 bar——因为有些票不方便融券，只能用
    相关的 ETF 做空来表达同样的看空判断。如果那个时间点 ETF 没有数据（asof 查不到），
    这次做空信号就跳过，不强行开仓。

    compound=True（默认）：capital_per_trade 是起始本金，只有这一笔钱可用，每次开仓
    用的是当前账户权益（上一笔平仓后的本金+累计盈亏），不是死的固定金额——账户涨了
    下一笔本金变多，亏了下一笔本金变少，跟真实账户一样滚动使用。权益跌到 0 或以下时
    停止开新仓（本金亏光）。compound=False 时保留旧行为：每笔都固定按 capital_per_trade
    算仓位，互不影响，方便单纯对比"这条信号本身"的胜率/赔率，不受资金曲线干扰。

    还会返回 signal_log：每一根只要"原始条件"成立就记一条（买入/卖出/多头反转/空头
    反转），不管当时是不是已经持仓、有没有真的开/平仓——taken 字段说明这次触发有没
    有真的变成一笔操作，方便你看"一天到底响了多少次"，而不是只看最终成交了几笔。

    debug_days 给了一组日期字符串（"YYYY-MM-DD"）时，会额外返回 bar_log——那几天
    每一根 5min bar 的完整原始数据（开高低收、成交量、VWAP 数值、VWAP趋势、放量
    判断、SMA短/长、二阶导数转折、SMA站上再跌破的前提状态、以及这一根是否真的触发
    了买入/卖出/多头反转/空头反转条件），用来诊断"某天为什么会/不会出现某个信号"。
    不传则不记录（避免正常回测时产生不必要的开销），返回的 bar_log 是空列表。

    close_no_trade_minutes：收盘前这么多分钟内不再开新仓（默认用 CLOSE_NO_TRADE_MINUTES，
    传 0 关闭这条限制）。这是全局规则，跟形态过滤（use_shape_filter）是否开启无关，
    也不区分预测出的是哪种形态——收盘前仓促开仓容易被隔夜跳空直接打止损。已有仓位
    的止损/真反转平仓不受影响，只影响"要不要开新仓"。
    """
    position: dict | None = None
    trades: list[dict] = []
    signal_log: list[dict] = []
    bar_log: list[dict] = []
    debug_days = debug_days or set()
    stood_above_sma_long = False
    stood_above_sma_short = False
    equity = capital_per_trade

    def _close_position(exit_bar_for_side: dict, exit_time: str, today, reason: str) -> None:
        nonlocal position, equity
        side = position["side"]
        shares = position["shares"]
        fill = exit_bar_for_side["close"]
        pnl = shares * (fill - position["price"]) if side == "多" else shares * (position["price"] - fill)
        if compound:
            equity += pnl
        trades.append({
            "entry_day": str(position["entry_day"]), "exit_day": str(today), "side": side,
            "symbol": position["symbol"],
            "entry_time": position["time"], "entry_price": position["price"],
            "exit_time": exit_time, "exit_price": fill,
            "exit_reason": reason, "pnl": pnl, "equity_after": equity,
            "entry_shape": position.get("entry_shape"),
            "entry_substate": position.get("entry_substate"),
        })
        position = None

    for setup in day_setups:
        today = setup["today"]
        signals = compute_day_signals(
            bars_5, setup["today_idx"], setup["series_idx"],
            setup["thresholds"], setup["rolling_max_init"],
            lookback, vwap_trend_lookback, sma_short_period, sma_long_period,
        )
        stop_dollar_long = setup["stop_dollar_long"]
        stop_dollar_short = setup["stop_dollar_short"]

        # 形态过滤：这天的开单模式（None = 不启用形态过滤，双向不受限）
        shape_plan = setup.get("shape_day_plan")
        shape_predicted_name = setup.get("shape_predicted_name")
        shape_switched = False
        uptrend_phase = "normal"  # "上涨"形态三态状态机专用：normal -> watch -> confirmed，只会往前切
        downtrend_phase = "normal"  # "下跌"形态三态状态机专用，跟上面完全对称
        day_len = len(setup["today_idx"])
        mid_pos_today = day_len // 2 if shape_plan and shape_plan["type"] == "time_split" else None

        for pos_today, sig in enumerate(signals):
            bar = sig["bar"]
            time_slot = sig["time_slot"]
            sma_short = sig["sma_short"]
            sma_long = sig["sma_long"]
            action_taken = False
            position_before = position["side"] if position else None

            short_bar = bar
            if short_symbol and etf_index is not None:
                short_bar = bar_at(etf_index, bar["ts"], etf_stamps or [])

            if use_sma_precondition:
                if sma_long is not None and bar["close"] > sma_long:
                    stood_above_sma_long = True
                if sma_short is not None and bar["close"] > sma_short:
                    stood_above_sma_short = True
                broken_long = sma_long is not None and bar["close"] < sma_long
                broken_short = sma_short is not None and bar["close"] < sma_short
                short_precondition = (stood_above_sma_long and broken_long) or (stood_above_sma_short and broken_short)
            else:
                short_precondition = True

            # 形态过滤：开盘头 SHAPE_FIRST_CHECK_MINUTES 分钟内，不管预测出的是
            # 哪种形态（横盘、横盘上涨、横盘下跌、上涨、下跌……全部一样），统一
            # 不开新仓——这段时间本身数据还不够、行情也容易横盘震荡，信号不可靠。
            # "上涨"这个形态额外多一条收盘前 SHAPE_UPTREND_CLOSE_NO_TRADE_MINUTES
            # 分钟也不开新仓的限制（回测发现这段时间开的多单亏损明显偏多，很多是
            # 隔夜止损），只对"上涨"生效，其他形态正常交易到收盘。开盘头一小时、
            # 收盘前这两段之外，按这天的开单模式决定允不允许开多/开空：time_split
            # 按"全天对半分"切换（跟 classify_day_shape 打标签用的是同一套上/下
            # 半场逻辑），signal_split 按"真反转"（turning="转空"）信号触发切换，
            # 两种模式一旦切换过一次就不会切回去。
            allow_buy, allow_sell = True, True
            current_substate = None
            if shape_plan is not None:
                minutes_since_open = pos_today * 5
                minutes_to_close = (day_len - 1 - pos_today) * 5
                in_open_blackout = minutes_since_open < SHAPE_FIRST_CHECK_MINUTES
                in_uptrend_close_blackout = (
                    shape_predicted_name == "上涨" and minutes_to_close < SHAPE_UPTREND_CLOSE_NO_TRADE_MINUTES
                )
                if in_open_blackout or in_uptrend_close_blackout:
                    allow_buy, allow_sell = False, False
                else:
                    if shape_plan["type"] == "fixed":
                        mode = shape_plan["mode"]
                    elif shape_plan["type"] == "time_split":
                        if not shape_switched and mid_pos_today is not None and pos_today >= mid_pos_today:
                            shape_switched = True
                        mode = shape_plan["phase2"] if shape_switched else shape_plan["phase1"]
                    elif shape_plan["type"] == "signal_split":
                        if not shape_switched and sig["turning"] == "转空":
                            shape_switched = True
                        mode = shape_plan["phase2"] if shape_switched else shape_plan["phase1"]
                    elif shape_plan["type"] == "uptrend_regime":
                        # "上涨"专用三态状态机。强上涨条件满足时，不管当前处在
                        # normal/watch/confirmed 哪个阶段，这一根都强制锁回只做多——
                        # 不是永久重置阶段状态，只是这一根不给做空机会（下一根如果
                        # 不再是强上涨，阶段该怎样还是怎样）。不是强上涨时，才走
                        # normal(只做多) -> watch(观察，不开新仓) -> confirmed(只做空，
                        # 反转确认之后原方向反过来，不是双向都开)
                        # 这套单向切换的状态机。
                        spy_direction = None
                        if market_index is not None and market_stamps:
                            spy_direction = market_direction_asof(
                                market_index, market_stamps, bar["ts"], UPTREND_MARKET_LOOKBACK
                            )
                        if is_strong_uptrend(sig, spy_direction):
                            mode = "only_long"
                            current_substate = "强上涨"
                        else:
                            if uptrend_phase == "normal" and is_uptrend_exhaustion_trigger(sig):
                                uptrend_phase = "watch"
                            if uptrend_phase in ("normal", "watch") and is_uptrend_reversal_confirmed(sig):
                                uptrend_phase = "confirmed"
                            mode = {"normal": "only_long", "watch": "watch", "confirmed": "only_short"}[uptrend_phase]
                            current_substate = classify_uptrend_substate(sig, uptrend_phase)
                    elif shape_plan["type"] == "downtrend_regime":
                        # "下跌"专用三态状态机，跟"上涨"完全对称。强下跌条件满足时，
                        # 不管当前处在 normal/watch/confirmed 哪个阶段，这一根都强制
                        # 锁回只做空；不是强下跌时，走 normal(只做空) -> watch(观察)
                        # -> confirmed(只做多，反转确认之后原方向反过来，不是双向都开)
                        # 这套单向切换的状态机。
                        spy_direction = None
                        if market_index is not None and market_stamps:
                            spy_direction = market_direction_asof(
                                market_index, market_stamps, bar["ts"], UPTREND_MARKET_LOOKBACK
                            )
                        if is_strong_downtrend(sig, spy_direction):
                            mode = "only_short"
                            current_substate = "强下跌"
                        else:
                            if downtrend_phase == "normal" and is_downtrend_exhaustion_trigger(sig):
                                downtrend_phase = "watch"
                            if downtrend_phase in ("normal", "watch") and is_downtrend_reversal_confirmed(sig):
                                downtrend_phase = "confirmed"
                            mode = {"normal": "only_short", "watch": "watch", "confirmed": "only_long"}[downtrend_phase]
                            current_substate = classify_downtrend_substate(sig, downtrend_phase)
                    elif shape_plan["type"] == "sideways_regime":
                        # "横盘"四态细分：窄横盘/横盘收缩不交易，横盘扩张双向都开，
                        # 宽横盘按收盘价在区间里的位置决定方向（下沿只做多、上沿只
                        # 做空、中间不交易）。这个分支直接算出 allow_buy/allow_sell，
                        # 不走下面统一的 shape_allowed_actions(mode) 那一步。
                        current_substate = classify_sideways_substate(sig)
                        allow_buy, allow_sell = sideways_allowed_actions(current_substate, sig)
                        mode = None
                    else:
                        mode = "both"
                    if shape_plan["type"] != "sideways_regime":
                        allow_buy, allow_sell = shape_allowed_actions(mode)

            # 收盘前不再开新仓：可选的全局规则（默认关闭，CLOSE_NO_TRADE_MINUTES=0），
            # 不分形态、对所有交易日生效，跟上面"上涨"专属的收盘前限制是两回事、
            # 互不冲突（都触发的话效果是叠加的）。只影响新开仓，已有仓位的止损/
            # 真反转平仓不受影响。
            if close_no_trade_minutes > 0:
                minutes_to_close = (day_len - 1 - pos_today) * 5
                if minutes_to_close < close_no_trade_minutes:
                    allow_buy, allow_sell = False, False

            # 开仓方向用一阶导数（f'，价格变化速度）判断涨跌，不用 VWAP 趋势
            buy_condition = sig["dtrend"] == "涨" and sig["vol_high"] and allow_buy
            sell_condition = bool(allow_short and sig["dtrend"] == "跌" and sig["vol_high"] and short_precondition and allow_sell)
            # "真反转"平仓：在原来的 turning+放量 基础上，再叠加一层跟开仓状态机
            # 同一套的反转确认（f'/f''方向、VWAP得失、成交量z-score、结构破位），
            # 不然开仓四道确认、平仓只有一道，标准不对称——之前发现在MSFT这类
            # 波动较小的股票上，"真反转"太容易被噪音级别的小波动触发，赚小赔小，
            # 加上这层确认能让平仓信号更谨慎一些，减少这种情况。
            exit_long_condition = (
                sig["turning"] == "转空" and sig["vol_high"] and is_uptrend_reversal_confirmed(sig)
            )
            exit_short_condition = bool(
                allow_short and sig["turning"] == "转多" and sig["vol_high"] and is_downtrend_reversal_confirmed(sig)
            )

            # ---- 第一优先级：止损（含隔夜跳空打滑处理）----
            if position is not None:
                check_bar = short_bar if (position["side"] == "空" and short_symbol) else bar
                if check_bar is not None:
                    fill = stop_hit_price(position["side"], position["stop_price"], check_bar)
                    if fill is not None:
                        side = position["side"]
                        shares = position["shares"]
                        pnl = shares * (fill - position["price"]) if side == "多" else shares * (position["price"] - fill)
                        if compound:
                            equity += pnl
                        trades.append({
                            "entry_day": str(position["entry_day"]), "exit_day": str(today), "side": side,
                            "symbol": position["symbol"],
                            "entry_time": position["time"], "entry_price": position["price"],
                            "exit_time": time_slot, "exit_price": fill,
                            "exit_reason": "止损", "pnl": pnl, "equity_after": equity,
                            "entry_shape": position.get("entry_shape"),
                            "entry_substate": position.get("entry_substate"),
                        })
                        action_taken = True
                        position = None

            # ---- 持仓中：先处理"真反转+放量"平仓（跟进场信号互相独立）----
            if position is not None and not action_taken:
                side = position["side"]
                if (side == "多" and sig["turning"] == "转空") or (side == "空" and sig["turning"] == "转多"):
                    if sig["vol_high"]:
                        exit_bar = short_bar if (side == "空" and short_symbol) else bar
                        if exit_bar is not None:
                            _close_position(exit_bar, time_slot, today, "真反转+放量")
                            action_taken = True

            # ---- 进场 / 反手：trade_every_signal=False 时只在空仓时进场（原行为）；
            #      trade_every_signal=True 时，只要信号触发就动作——反向持仓先平再反手，
            #      同向持仓视为已满足、不重复开仓 ----
            notional = equity if compound else capital_per_trade
            can_enter = (not compound) or notional > 0
            if can_enter:
                if buy_condition and (position is None or (trade_every_signal and position["side"] == "空")):
                    if position is not None and position["side"] == "空":
                        exit_bar = short_bar if short_symbol else bar
                        if exit_bar is not None:
                            _close_position(exit_bar, time_slot, today, "信号反手")
                        else:
                            exit_bar = None  # ETF 没数据，无法平掉现有空单，跳过本次反手
                    if position is None:
                        sp = bar["close"] - stop_dollar_long if stop_dollar_long is not None else None
                        position = {
                            "side": "多", "symbol": primary_symbol, "time": time_slot,
                            "price": bar["close"], "stop_price": sp, "entry_day": today,
                            "shares": notional / bar["close"],
                            "entry_shape": shape_predicted_name,
                            "entry_substate": current_substate,
                        }
                elif sell_condition and (position is None or (trade_every_signal and position["side"] == "多")):
                    if short_symbol and short_bar is None:
                        pass  # 这一刻 ETF 没有数据，跳过这次做空信号，不强行开仓/反手
                    else:
                        if position is not None and position["side"] == "多":
                            _close_position(bar, time_slot, today, "信号反手")
                        if position is None:
                            entry_bar = short_bar if short_symbol else bar
                            sp = entry_bar["close"] + stop_dollar_short if stop_dollar_short is not None else None
                            position = {
                                "side": "空", "symbol": short_symbol or primary_symbol, "time": time_slot,
                                "price": entry_bar["close"], "stop_price": sp, "entry_day": today,
                                "shares": notional / entry_bar["close"],
                                "entry_shape": shape_predicted_name,
                                "entry_substate": current_substate,
                            }
                            if use_sma_precondition:
                                stood_above_sma_long = False
                                stood_above_sma_short = False

            if buy_condition:
                signal_log.append({
                    "day": str(today), "time": time_slot, "type": "买入",
                    "price": round(bar["close"], 4),
                    "taken": bool(
                        position is not None and position["time"] == time_slot
                        and str(position["entry_day"]) == str(today) and position["side"] == "多"
                    ),
                })
            if sell_condition:
                sell_price = (short_bar or bar)["close"]
                signal_log.append({
                    "day": str(today), "time": time_slot, "type": "卖出/做空",
                    "price": round(sell_price, 4),
                    "taken": bool(
                        position is not None and position["time"] == time_slot
                        and str(position["entry_day"]) == str(today) and position["side"] == "空"
                    ),
                })
            if exit_long_condition:
                last_trade = trades[-1] if trades else None
                signal_log.append({
                    "day": str(today), "time": time_slot, "type": "多头反转",
                    "price": round(bar["close"], 4),
                    "taken": bool(
                        last_trade and last_trade.get("exit_time") == time_slot
                        and last_trade.get("exit_day") == str(today) and last_trade.get("side") == "多"
                        and last_trade.get("exit_reason") == "真反转+放量"
                    ),
                })
            if exit_short_condition:
                last_trade = trades[-1] if trades else None
                signal_log.append({
                    "day": str(today), "time": time_slot, "type": "空头反转",
                    "price": round(bar["close"], 4),
                    "taken": bool(
                        last_trade and last_trade.get("exit_time") == time_slot
                        and last_trade.get("exit_day") == str(today) and last_trade.get("side") == "空"
                        and last_trade.get("exit_reason") == "真反转+放量"
                    ),
                })

            if str(today) in debug_days:
                bar_log.append({
                    "day": str(today), "time": time_slot,
                    "open": bar["open"], "high": bar["high"], "low": bar["low"], "close": bar["close"],
                    "volume": float(bar.get("volume") or 0.0),
                    "vwap": round(sig["vwap"], 4) if sig["vwap"] is not None else None,
                    "vtrend": sig["vtrend"],
                    "dtrend": sig["dtrend"],
                    "f1": round(sig["f1"], 4) if sig["f1"] is not None else None,
                    "f2": round(sig["f2"], 4) if sig["f2"] is not None else None,
                    "vol_state": sig["vol_state"],
                    "vol_high": sig["vol_high"], "is_new_record": sig["is_new_record"],
                    "turning": sig["turning"],
                    "sma_short": round(sma_short, 4) if sma_short is not None else None,
                    "sma_long": round(sma_long, 4) if sma_long is not None else None,
                    "short_precondition": short_precondition,
                    "buy_condition": buy_condition, "sell_condition": sell_condition,
                    "exit_long_condition": exit_long_condition, "exit_short_condition": exit_short_condition,
                    "position_before": position_before,
                    "position_after": position["side"] if position else None,
                    "action_taken": action_taken,
                })

    if position is not None:
        trades.append({
            "entry_day": str(position["entry_day"]), "exit_day": None, "side": position["side"],
            "symbol": position["symbol"],
            "entry_time": position["time"], "entry_price": position["price"],
            "exit_time": None, "exit_price": None,
            "exit_reason": "区间结束仍持仓（未实现）", "pnl": 0.0,
            "entry_shape": position.get("entry_shape"),
            "entry_substate": position.get("entry_substate"),
        })

    return trades, signal_log, bar_log


def walk_forward_regime(
    bars_5: list[dict],
    day_setups: list[dict],
    regime_map: dict[int, dict],
    *,
    lookback: int,
    vwap_trend_lookback: int,
    sma_short_period: int,
    sma_long_period: int,
    use_sma_precondition: bool,
    allow_short: bool,
    capital_per_trade: float,
    primary_symbol: str,
    compound: bool = True,
    impulse_bars: int = 12,
    trail_atr: float = REGIME_TRAIL_ATR,
    impulse_giveback: float = REGIME_IMPULSE_GIVEBACK,
    trend_break_atr: float = REGIME_TREND_BREAK_ATR,
    range_stop_atr: float = REGIME_RANGE_STOP_ATR,
    impulse_chase: bool = True,
    range_trade: bool = True,
    trend_trade: bool = True,
    trend_entry: str = "breakout",
    trend_min_slope_atr: float = 1.5,
) -> tuple[list[dict], list[dict]]:
    """三态趋势线版的连续模拟，仓位可跨日。

    每根先看状态（build_regime_map）：
      急涨 / 急跌：顺势追（要求放量或量能 z-score ≥ 1），不逆势开仓；跟踪止损 +
                  回吐 impulse_giveback 比例离场；出现反向急涨/急跌立即离场。
      横盘：在通道下沿且 f′ 拐头向上做多、上沿且 f′ 向下做空，回到趋势线（中轨）止盈，
            止损放在通道外 range_stop_atr 倍 ATR。
      上升/下降趋势：趋势线斜率 ≥ trend_min_slope_atr 才做，只顺趋势线方向。
            trend_entry="pullback"：回踩到趋势线附近、收盘仍在线同侧且 f′ 拐回趋势方向才开；
            "breakout"：原导数信号（f′ 方向 + 放量）且价格在线同侧。收盘破趋势线
            trend_break_atr 倍 ATR 或原"真反转+放量"离场，跟踪止损。
    止损（含跳空）永远最先检查。只在空仓时开新仓，同方向急涨急跌离场后 impulse_bars
    根内不再追同方向。
    """
    position: dict | None = None
    trades: list[dict] = []
    signal_log: list[dict] = []
    equity = capital_per_trade
    stood_long = stood_short = False
    bar_no = 0
    cooldown_until = {"多": -1, "空": -1}

    def log(today, time_slot, etype, price, reason, regime):
        signal_log.append({
            "day": str(today), "time": time_slot, "type": etype, "price": round(price, 4),
            "taken": True, "reason": reason, "regime": regime,
        })

    def close(fill: float, today, time_slot: str, reason: str, regime: str | None) -> None:
        nonlocal position, equity
        side = position["side"]
        pnl = position["shares"] * (fill - position["price"]) if side == "多" else position["shares"] * (position["price"] - fill)
        if compound:
            equity += pnl
        trades.append({
            "entry_day": str(position["entry_day"]), "exit_day": str(today), "side": side,
            "symbol": position["symbol"],
            "entry_time": position["time"], "entry_price": position["price"],
            "exit_time": time_slot, "exit_price": fill,
            "exit_reason": reason, "pnl": pnl, "equity_after": equity,
            "entry_shape": None,
            "entry_substate": position["label"],
        })
        log(today, time_slot, "多头反转" if side == "多" else "空头反转", fill, reason, regime)
        if position["mode"] == "impulse":
            cooldown_until[side] = bar_no + impulse_bars
        position = None

    def open_pos(side: str, bar: dict, today, time_slot: str, mode: str, stop: float, reg: dict, label: str, reason: str):
        nonlocal position
        notional = equity if compound else capital_per_trade
        if compound and notional <= 0:
            return
        position = {
            "side": side, "symbol": primary_symbol, "time": time_slot, "price": bar["close"],
            "stop_price": stop, "initial_stop": stop, "entry_day": today,
            "shares": notional / bar["close"], "mode": mode, "extreme": bar["close"],
            "leg_start": reg.get("leg_start"), "label": label,
        }
        log(today, time_slot, "买入" if side == "多" else "卖出/做空", bar["close"], reason, reg["regime"])

    for setup in day_setups:
        today = setup["today"]
        signals = compute_day_signals(
            bars_5, setup["today_idx"], setup["series_idx"],
            setup["thresholds"], setup["rolling_max_init"],
            lookback, vwap_trend_lookback, sma_short_period, sma_long_period,
        )
        stop_dollar_long = setup["stop_dollar_long"]
        stop_dollar_short = setup["stop_dollar_short"]

        for sig in signals:
            bar_no += 1
            bar = sig["bar"]
            close_px = bar["close"]
            time_slot = sig["time_slot"]
            reg = regime_map.get(sig["idx"])
            regime = reg["regime"] if reg else None

            if use_sma_precondition:
                if sig["sma_long"] is not None and close_px > sig["sma_long"]:
                    stood_long = True
                if sig["sma_short"] is not None and close_px > sig["sma_short"]:
                    stood_short = True
                short_pre = (stood_long and sig["sma_long"] is not None and close_px < sig["sma_long"]) or (
                    stood_short and sig["sma_short"] is not None and close_px < sig["sma_short"]
                )
            else:
                short_pre = True

            if position is not None:
                fill = stop_hit_price(position["side"], position["stop_price"], bar)
                if fill is not None:
                    moved = position["stop_price"] != position["initial_stop"]
                    close(fill, today, time_slot, "跟踪止损" if moved else "止损", regime)

            if position is not None and reg:
                side = position["side"]
                mode = position["mode"]
                atr = reg["atr"]
                if side == "多" and regime == REGIME_DOWN:
                    close(close_px, today, time_slot, "逆向急跌离场", regime)
                elif side == "空" and regime == REGIME_UP:
                    close(close_px, today, time_slot, "逆向急涨离场", regime)
                elif mode == "impulse":
                    leg = abs(position["extreme"] - (position["leg_start"] or position["price"]))
                    give = (position["extreme"] - close_px) if side == "多" else (close_px - position["extreme"])
                    if leg > 0 and give >= impulse_giveback * leg and give > atr:
                        close(close_px, today, time_slot, "急涨回吐" if side == "多" else "急跌回吐", regime)
                elif mode == "trend":
                    if side == "多" and close_px < reg["line"] - trend_break_atr * atr:
                        close(close_px, today, time_slot, "跌破趋势线", regime)
                    elif side == "空" and close_px > reg["line"] + trend_break_atr * atr:
                        close(close_px, today, time_slot, "升破趋势线", regime)
                    elif side == "多" and sig["turning"] == "转空" and sig["vol_high"] and is_uptrend_reversal_confirmed(sig):
                        close(close_px, today, time_slot, "真反转+放量", regime)
                    elif side == "空" and sig["turning"] == "转多" and sig["vol_high"] and is_downtrend_reversal_confirmed(sig):
                        close(close_px, today, time_slot, "真反转+放量", regime)
                elif mode == "range":
                    if side == "多" and close_px >= reg["line"]:
                        close(close_px, today, time_slot, "回归中轨止盈", regime)
                    elif side == "空" and close_px <= reg["line"]:
                        close(close_px, today, time_slot, "回归中轨止盈", regime)

            if position is not None and reg and position["mode"] in ("impulse", "trend"):
                atr = reg["atr"]
                if position["side"] == "多":
                    position["extreme"] = max(position["extreme"], close_px)
                    trail = position["extreme"] - trail_atr * atr
                    if trail > position["stop_price"]:
                        position["stop_price"] = trail
                else:
                    position["extreme"] = min(position["extreme"], close_px)
                    trail = position["extreme"] + trail_atr * atr
                    if trail < position["stop_price"]:
                        position["stop_price"] = trail

            if position is not None or not reg:
                continue
            atr = reg["atr"]
            vol_ok = sig["vol_high"] or (sig.get("volume_zscore") or 0) >= 1.0

            if regime == REGIME_UP and impulse_chase and vol_ok and bar_no > cooldown_until["多"]:
                open_pos("多", bar, today, time_slot, "impulse", close_px - trail_atr * atr, reg, "急涨追多", "直线拉升顺势追多")
            elif regime == REGIME_DOWN and impulse_chase and allow_short and vol_ok and bar_no > cooldown_until["空"]:
                open_pos("空", bar, today, time_slot, "impulse", close_px + trail_atr * atr, reg, "急跌追空", "直线砸盘顺势追空")
            elif regime == REGIME_SIDEWAYS and range_trade:
                if close_px <= reg["lower"] and (sig["f1"] or 0) > 0:
                    open_pos("多", bar, today, time_slot, "range", reg["lower"] - range_stop_atr * atr, reg, "横盘下沿", "横盘下沿拐头做多")
                elif allow_short and close_px >= reg["upper"] and (sig["f1"] or 0) < 0:
                    open_pos("空", bar, today, time_slot, "range", reg["upper"] + range_stop_atr * atr, reg, "横盘上沿", "横盘上沿拐头做空")
            elif regime in (REGIME_TREND_UP, REGIME_TREND_DOWN) and trend_trade and abs(reg["slope_atr"]) >= trend_min_slope_atr:
                up = regime == REGIME_TREND_UP
                if trend_entry == "pullback":
                    touched = (bar["low"] <= reg["line"] + 0.3 * atr) if up else (bar["high"] >= reg["line"] - 0.3 * atr)
                    held = close_px > reg["line"] if up else close_px < reg["line"]
                    turn = (sig["f1"] or 0) > 0 if up else (sig["f1"] or 0) < 0
                    hit = touched and held and turn
                    why = "回踩趋势线拐头"
                else:
                    held = close_px > reg["line"] if up else close_px < reg["line"]
                    hit = held and sig["vol_high"] and sig["dtrend"] == ("涨" if up else "跌")
                    why = "沿趋势线 f′+放量"
                if hit and up:
                    dist = stop_dollar_long if stop_dollar_long else trail_atr * atr
                    stop = min(close_px - dist, reg["line"] - trail_atr * atr)
                    open_pos("多", bar, today, time_slot, "trend", stop, reg, "上升趋势", why)
                elif hit and allow_short and short_pre:
                    dist = stop_dollar_short if stop_dollar_short else trail_atr * atr
                    stop = max(close_px + dist, reg["line"] + trail_atr * atr)
                    open_pos("空", bar, today, time_slot, "trend", stop, reg, "下降趋势", why)
                    if use_sma_precondition:
                        stood_long = stood_short = False

    if position is not None:
        trades.append({
            "entry_day": str(position["entry_day"]), "exit_day": None, "side": position["side"],
            "symbol": position["symbol"],
            "entry_time": position["time"], "entry_price": position["price"],
            "exit_time": None, "exit_price": None,
            "exit_reason": "区间结束仍持仓（未实现）", "pnl": 0.0,
            "entry_shape": None, "entry_substate": position["label"],
            "stop_price": position["stop_price"],
        })
    return trades, signal_log


def regime_summary_extras(trades: list[dict]) -> dict:
    realized = [t for t in trades if t["exit_day"] is not None]
    exit_counts: dict[str, int] = {}
    for t in realized:
        exit_counts[t["exit_reason"]] = exit_counts.get(t["exit_reason"], 0) + 1

    def pack(rows: list[dict]) -> dict:
        wins = sum(1 for t in rows if t["pnl"] > 0)
        return {
            "n_trades": len(rows),
            "win_rate": round(wins / len(rows) * 100, 1) if rows else 0.0,
            "total_pnl": round(sum(t["pnl"] for t in rows), 2),
        }

    by_regime: dict[str, dict] = {}
    for label in sorted({t.get("entry_substate") or "—" for t in realized}):
        by_regime[label] = pack([t for t in realized if (t.get("entry_substate") or "—") == label])
    return {
        "exit_counts": exit_counts,
        "by_side": {"多": pack([t for t in realized if t["side"] == "多"]), "空": pack([t for t in realized if t["side"] == "空"])},
        "by_regime": by_regime,
    }


def _impulse_bars_for(minutes: int) -> int:
    return max(3, int(round(minutes / max(_bar_minutes(), 1))))


def _current_regime_signal(
    symbol: str,
    bars_5: list[dict],
    all_rth_idx: list[int],
    days: list,
    atr_sorted_dates: list,
    atr_date_to_record: dict,
    *,
    context_days: int,
    volume_dist_days: int,
    volume_max_window_days: int,
    volume_high_pct: float,
    volume_low_pct: float,
    min_slot_samples: int,
    stop_loss_atr_mult: float,
    lookback: int,
    vwap_trend_lookback: int,
    sma_short_period: int,
    sma_long_period: int,
    use_sma_precondition: bool,
    allow_short: bool,
    regime_kwargs: dict,
    replay_days: int = 4,
) -> dict:
    """实盘：用最近 replay_days 天重放三态引擎（跟回测同一套），取今天的动作当 events。"""
    setups = []
    for dp in range(max(0, len(days) - replay_days), len(days)):
        s = build_day_setup(
            bars_5, all_rth_idx, days, dp, atr_sorted_dates, atr_date_to_record,
            context_days, volume_dist_days, volume_max_window_days,
            volume_high_pct, volume_low_pct, min_slot_samples, stop_loss_atr_mult,
        )
        if s is not None:
            setups.append(s)
    if not setups:
        raise OhlcError(f"{symbol} 历史铺垫不够")
    regime_map = build_regime_map(bars_5, all_rth_idx, **_regime_map_kwargs(regime_kwargs))
    trades, signal_log = walk_forward_regime(
        bars_5, setups, regime_map,
        lookback=lookback, vwap_trend_lookback=vwap_trend_lookback,
        sma_short_period=sma_short_period, sma_long_period=sma_long_period,
        use_sma_precondition=use_sma_precondition, allow_short=allow_short,
        capital_per_trade=10000.0, primary_symbol=symbol, compound=False,
        **_regime_walk_kwargs(regime_kwargs),
    )
    today_str = str(days[-1])
    direction_of = {"买入": "做多", "卖出/做空": "做空", "多头反转": "平多", "空头反转": "平空"}
    events = [
        {
            "day": e["day"], "time": e["time"], "direction": direction_of.get(e["type"], e["type"]),
            "type": e["type"], "price": e["price"], "reason": e.get("reason"), "regime": e.get("regime"),
        }
        for e in signal_log
        if e["day"] == today_str
    ]

    last_i = setups[-1]["today_idx"][-1]
    bar = bars_5[last_i]
    price = float(bar["close"])
    asof = session_time(bar["ts"])
    reg = regime_map.get(last_i) or {}
    open_pos = trades[-1] if trades and trades[-1]["exit_day"] is None else None
    last_events = [e for e in events if e["time"] == asof]
    if last_events:
        ev = last_events[-1]
        direction = ev["direction"]
        action = f"{ev['type']} · {ev.get('reason') or ''} @ {price}"
    elif open_pos:
        direction = "持多" if open_pos["side"] == "多" else "持空"
        stop = open_pos.get("stop_price")
        action = f"持有{open_pos['side']}单（{open_pos.get('entry_substate')}）@ {open_pos['entry_price']}" + (
            f"，止损 {round(stop, 4)}" if stop is not None else ""
        )
    else:
        direction = "观望"
        action = f"观望 · 当前{reg.get('regime') or '状态未知'}"
    return {
        "price": round(price, 4),
        "asof": asof,
        "day": today_str,
        "direction": direction,
        "action": action,
        "hit": bool(last_events),
        "events": events,
        "warnings": [],
        "regime": reg.get("regime"),
        "snap": {
            "regime": reg.get("regime"),
            "trendline": round(reg["line"], 4) if reg.get("line") is not None else None,
            "upper": round(reg["upper"], 4) if reg.get("upper") is not None else None,
            "lower": round(reg["lower"], 4) if reg.get("lower") is not None else None,
            "er_fast": round(reg["er_fast"], 2) if reg.get("er_fast") is not None else None,
            "move_atr": round(reg["move_atr"], 2) if reg.get("move_atr") is not None else None,
            "position": open_pos["side"] if open_pos else None,
        },
    }


REGIME_KEYS = (
    "regime_atr_n", "regime_impulse_minutes", "regime_impulse_er", "regime_impulse_atr",
    "regime_trend_bars", "regime_sideways_er", "regime_sideways_adx", "regime_channel_k",
    "regime_trail_atr", "regime_impulse_giveback", "regime_trend_break_atr", "regime_range_stop_atr",
    "regime_impulse_chase", "regime_range_trade", "regime_trend_trade",
    "regime_trend_entry", "regime_trend_min_slope",
)


def _regime_kwargs(scope: dict) -> dict:
    return {k: scope[k] for k in REGIME_KEYS if k in scope}


def _regime_map_kwargs(rk: dict) -> dict:
    return {
        "atr_n": int(rk["regime_atr_n"]),
        "impulse_bars": _impulse_bars_for(int(rk["regime_impulse_minutes"])),
        "impulse_er": float(rk["regime_impulse_er"]),
        "impulse_atr": float(rk["regime_impulse_atr"]),
        "trend_bars": int(rk["regime_trend_bars"]),
        "sideways_er": float(rk["regime_sideways_er"]),
        "sideways_adx": float(rk["regime_sideways_adx"]),
        "channel_k": float(rk["regime_channel_k"]),
    }


def _regime_walk_kwargs(rk: dict) -> dict:
    return {
        "impulse_bars": _impulse_bars_for(int(rk["regime_impulse_minutes"])),
        "trail_atr": float(rk["regime_trail_atr"]),
        "impulse_giveback": float(rk["regime_impulse_giveback"]),
        "trend_break_atr": float(rk["regime_trend_break_atr"]),
        "range_stop_atr": float(rk["regime_range_stop_atr"]),
        "impulse_chase": bool(rk["regime_impulse_chase"]),
        "range_trade": bool(rk["regime_range_trade"]),
        "trend_trade": bool(rk["regime_trend_trade"]),
        "trend_entry": "pullback" if rk.get("regime_trend_entry") == "pullback" else "breakout",
        "trend_min_slope_atr": float(rk.get("regime_trend_min_slope", 1.5)),
    }


# ---------------------------------------------------------------------------
# 数据准备
# ---------------------------------------------------------------------------

def load_context(symbol: str, fetch_start: date) -> tuple[list[dict], list, list[int]]:
    """从 fetch_start 开始拉取当前周期（默认 5m）K 线。"""
    tf = _bar_tf()
    raw = fetch_closes_covering(symbol, tf, start=fetch_start)
    bars = wash_bars(raw)
    if _is_crypto():
        all_rth_idx = list(range(len(bars)))
    else:
        all_rth_idx = [i for i, b in enumerate(bars) if in_session(b["ts"])]
    days = trading_days(bars, all_rth_idx)
    return bars, days, all_rth_idx


def build_day_setup(
    bars_5: list[dict], all_rth_idx: list[int], days: list, day_pos: int,
    atr_sorted_dates: list, atr_date_to_record: dict,
    context_days: int, volume_dist_days: int, volume_max_window_days: int,
    volume_high_pct: float, volume_low_pct: float, min_slot_samples: int,
    stop_loss_atr_mult: float,
    short_atr_sorted_dates: list | None = None, short_atr_date_to_record: dict | None = None,
) -> dict | None:
    """给定 days 列表中第 day_pos 天，准备好它需要的铺垫数据。

    stop_dollar_long 永远用主标的自己的日线 ATR。stop_dollar_short 默认也用主标的
    的 ATR（直接做空主标的时）；如果传了 short_atr_* （做空走 ETF 时），改用 ETF
    自己的日线 ATR——因为仓位实际落在 ETF 上，止损空间该按 ETF 自己的波动算，不是主
    标的的。
    """
    need_hist = max(volume_dist_days, volume_max_window_days, context_days)
    if day_pos < need_hist:
        return None

    today = days[day_pos]
    dist_days = days[day_pos - volume_dist_days : day_pos]
    max_window_days = days[day_pos - volume_max_window_days : day_pos]
    context_set = set(days[day_pos - context_days : day_pos])

    today_idx = [i for i in all_rth_idx if session_day(bars_5[i]["ts"]) == today]
    if not today_idx:
        return None

    hist_dist = {d: [i for i in all_rth_idx if session_day(bars_5[i]["ts"]) == d] for d in dist_days}
    hist_max = {d: [i for i in all_rth_idx if session_day(bars_5[i]["ts"]) == d] for d in max_window_days}
    thresholds = slot_thresholds(build_slot_volume_history(bars_5, hist_dist), volume_high_pct, volume_low_pct, min_slot_samples)
    rolling_max_init = initial_rolling_max_volume(bars_5, hist_max)

    series_idx = [i for i in all_rth_idx if session_day(bars_5[i]["ts"]) in context_set or session_day(bars_5[i]["ts"]) == today]

    stop_dollar_long = atr_dollar_asof(atr_sorted_dates, atr_date_to_record, today, stop_loss_atr_mult)
    if short_atr_sorted_dates is not None:
        stop_dollar_short = atr_dollar_asof(short_atr_sorted_dates, short_atr_date_to_record, today, stop_loss_atr_mult)
    else:
        stop_dollar_short = stop_dollar_long

    return {
        "today": today, "today_idx": today_idx, "series_idx": series_idx,
        "thresholds": thresholds, "rolling_max_init": rolling_max_init,
        "stop_dollar_long": stop_dollar_long, "stop_dollar_short": stop_dollar_short,
    }


def daily_signal_counts(signal_log: list[dict]) -> list[dict]:
    """按天汇总每种原始信号触发了几次、其中几次真的变成了操作（taken）。"""
    by_day: dict[str, dict] = {}
    type_key = {"买入": "buy", "卖出/做空": "sell", "多头反转": "exit_long", "空头反转": "exit_short"}
    for entry in signal_log:
        row = by_day.setdefault(entry["day"], {
            "date": entry["day"], "buy": 0, "sell": 0, "exit_long": 0, "exit_short": 0,
            "taken": 0, "total": 0,
        })
        row["total"] += 1
        if entry["taken"]:
            row["taken"] += 1
        key = type_key.get(entry["type"])
        if key:
            row[key] += 1
    return [by_day[d] for d in sorted(by_day)]


def daily_trade_stats(trades: list[dict]) -> list[dict]:
    """按"开仓日期"（entry_day）把已实现交易分组，统计当天开出的仓位一共有几笔、
    赢几笔/输几笔、胜率、总盈亏——这是"信号准确度"按天拆分的核心统计。

    注意：如果某笔仓位是跨日持仓（entry_day != exit_day），胜负要等它实际平仓才
    知道，但这里仍然按"开仓那天"归类，因为我们要回答的是"那天触发的信号，最终
    表现是赢是输"，而不是"那天收盘时账户发生了什么"。
    """
    realized = [t for t in trades if t["exit_day"] is not None]
    by_day: dict[str, dict] = {}
    for t in realized:
        day = t["entry_day"]
        row = by_day.setdefault(day, {
            "date": day, "n_trades": 0, "wins": 0, "losses": 0,
            "n_long": 0, "n_short": 0, "total_pnl": 0.0,
        })
        row["n_trades"] += 1
        if t["pnl"] > 0:
            row["wins"] += 1
        else:
            row["losses"] += 1
        if t["side"] == "多":
            row["n_long"] += 1
        else:
            row["n_short"] += 1
        row["total_pnl"] += t["pnl"]

    out = []
    for day in sorted(by_day):
        row = by_day[day]
        row["win_rate"] = round(row["wins"] / row["n_trades"] * 100, 1) if row["n_trades"] else 0.0
        row["total_pnl"] = round(row["total_pnl"], 2)
        out.append(row)
    return out


def daily_report(signal_log: list[dict], trades: list[dict]) -> list[dict]:
    """把"每天信号触发次数"（daily_signal_counts）和"每天开仓交易的胜率"
    （daily_trade_stats）按日期合并成一张表，方便一眼看出"信号准确度"随时间的变化。

    每一行包含：
        date            日期
        signals_total   当天所有类型信号（买入/卖出/多头反转/空头反转）触发总次数
        signals_taken   其中有几次真的变成了一笔开仓/平仓操作
        buy_signals / sell_signals / exit_long_signals / exit_short_signals
                        按类型拆开的触发次数
        n_trades        当天"开仓"的已实现交易笔数（信号最终变成的仓位数）
        wins / losses   这些仓位里赢/输的笔数
        win_rate        胜率（信号准确度），单位 %
        total_pnl       当天开出的这些仓位，最终合计盈亏
    """
    sig_counts = {row["date"]: row for row in daily_signal_counts(signal_log)}
    trade_stats = {row["date"]: row for row in daily_trade_stats(trades)}
    all_days = sorted(set(sig_counts) | set(trade_stats))

    out = []
    for day in all_days:
        sc = sig_counts.get(day, {"buy": 0, "sell": 0, "exit_long": 0, "exit_short": 0, "taken": 0, "total": 0})
        ts = trade_stats.get(day, {"n_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0})
        out.append({
            "date": day,
            "signals_total": sc["total"],
            "signals_taken": sc["taken"],
            "buy_signals": sc["buy"],
            "sell_signals": sc["sell"],
            "exit_long_signals": sc["exit_long"],
            "exit_short_signals": sc["exit_short"],
            "n_trades": ts["n_trades"],
            "wins": ts["wins"],
            "losses": ts["losses"],
            "win_rate": ts["win_rate"],
            "total_pnl": ts["total_pnl"],
        })
    return out


def summarize(trades: list[dict], capital_per_trade: float, compound: bool) -> dict:
    """汇总统计：胜率、总盈亏、平均盈亏、收益率等，供 CLI 和 API 共用。"""
    realized = [t for t in trades if t["exit_day"] is not None]
    unrealized = [t for t in trades if t["exit_day"] is None]
    if not realized:
        return {
            "n_trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "payoff_ratio": None,
            "stop_exits": 0, "reversal_exits": 0, "signal_flip_exits": 0, "overnight": 0,
            "n_long": 0, "n_short": 0,
            "return_pct": 0.0, "unrealized": unrealized[0] if unrealized else None,
            "compound": compound, "starting_capital": capital_per_trade, "final_equity": capital_per_trade,
        }
    total_pnl = sum(t["pnl"] for t in realized)
    wins = [t for t in realized if t["pnl"] > 0]
    losses = [t for t in realized if t["pnl"] <= 0]
    avg_win = round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0.0
    if avg_loss < 0 and avg_win > 0:
        payoff_ratio = round(avg_win / abs(avg_loss), 2)
    elif avg_loss == 0 and avg_win > 0:
        payoff_ratio = None  # 无亏损，比率无意义，前端显示「—」或「全胜」
    else:
        payoff_ratio = 0.0 if realized else None
    final_equity = realized[-1]["equity_after"] if compound and "equity_after" in realized[-1] else capital_per_trade + total_pnl
    return {
        "n_trades": len(realized),
        "win_rate": round(len(wins) / len(realized) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "stop_exits": sum(1 for t in realized if t["exit_reason"] == "止损"),
        "reversal_exits": sum(1 for t in realized if t["exit_reason"] == "真反转+放量"),
        "signal_flip_exits": sum(1 for t in realized if t["exit_reason"] == "信号反手"),
        "overnight": sum(1 for t in realized if t["entry_day"] != t["exit_day"]),
        "n_long": sum(1 for t in realized if t["side"] == "多"),
        "n_short": sum(1 for t in realized if t["side"] == "空"),
        "return_pct": round(total_pnl / capital_per_trade * 100, 2),
        "unrealized": unrealized[0] if unrealized else None,
        "compound": compound,
        "starting_capital": capital_per_trade,
        "final_equity": round(final_equity, 2),
    }


# ---------------------------------------------------------------------------
# 实时：只看最新一根 K 线的信号，给股票列表监听/推送用（5min做多，只做多不做空）
# ---------------------------------------------------------------------------

def current_signal(
    symbol: str,
    *,
    lookback: int = LOOKBACK,
    context_days: int = CONTEXT_DAYS,
    vwap_trend_lookback: int = VWAP_TREND_LOOKBACK,
    volume_dist_days: int = VOLUME_DIST_DAYS,
    volume_high_pct: float = VOLUME_HIGH_PCT,
    volume_low_pct: float = VOLUME_LOW_PCT,
    min_slot_samples: int = MIN_SLOT_SAMPLES,
    volume_max_window_days: int = VOLUME_MAX_WINDOW_DAYS,
    daily_atr_n: int = DAILY_ATR_N,
    stop_loss_atr_mult: float = STOP_LOSS_ATR_MULT,
    allow_short: bool = False,
    use_sma_precondition: bool = USE_SMA_PRECONDITION,
    sma_short_period: int = SMA_SHORT_PERIOD,
    sma_long_period: int = SMA_LONG_PERIOD,
    use_shape_filter: bool = False,
    shape_confidence_threshold: float = 40.0,  # 跟 SHAPE_CONFIDENCE_THRESHOLD 保持一致（那个常量
                                                # 定义在本函数之后，不能直接引用，只能重复字面量）
    shape_min_train_days: int = 30,  # 跟 SHAPE_MIN_TRAIN_DAYS 保持一致，原因同上
    close_no_trade_minutes: int = CLOSE_NO_TRADE_MINUTES,
    market_symbol: str | None = UPTREND_MARKET_SYMBOL,
    market: str = "stock",
    timeframe: str = "5m",
    use_regime: bool = False,
    regime_atr_n: int = REGIME_ATR_N,
    regime_impulse_minutes: int = REGIME_IMPULSE_MINUTES,
    regime_impulse_er: float = REGIME_IMPULSE_ER,
    regime_impulse_atr: float = REGIME_IMPULSE_ATR,
    regime_trend_bars: int = REGIME_TREND_BARS,
    regime_sideways_er: float = REGIME_SIDEWAYS_ER,
    regime_sideways_adx: float = REGIME_SIDEWAYS_ADX,
    regime_channel_k: float = REGIME_CHANNEL_K,
    regime_trail_atr: float = REGIME_TRAIL_ATR,
    regime_impulse_giveback: float = REGIME_IMPULSE_GIVEBACK,
    regime_trend_break_atr: float = REGIME_TREND_BREAK_ATR,
    regime_range_stop_atr: float = REGIME_RANGE_STOP_ATR,
    regime_impulse_chase: bool = True,
    regime_range_trade: bool = True,
    regime_trend_trade: bool = True,
    regime_trend_entry: str = "breakout",
    regime_trend_min_slope: float = 1.5,
    **_ignored,
) -> dict:
    """导数策略：看今天到目前为止走完的每一根 K 线，判断该不该买/卖/走。

    跟 walk_forward()（回测引擎）用同一套判断逻辑，只是不回测、不记仓位状态——
    买卖决定留给人。一阶导数向上+放量 => 买；一阶导数向下+放量（allow_short=True
    时，且满足 SMA 站上再跌破前提）=> 卖/做空；二阶导数真反转+放量+反转确认 =>
    如果手上有对应方向的仓位，提示可以考虑止盈/离场。

    use_shape_filter=True 时，跟回测一样先用历史数据预测"今天"最可能是哪种VWAP
    轨道形态，按 SHAPE_DAY_PLAN 决定当前允不允许开多/开空（含"上涨"/"下跌"专属的
    三态状态机、"横盘"四态细分、收盘前黑名单）——这些状态（uptrend_phase 之类）
    都是跨根的，所以下面会把今天已经走完的每一根从头重放一遍来算"现在"这一刻的
    状态，不是只看最后一根。形态预测模型训练一次要跑几百轮梯度下降，实盘按几十秒
    一次的轮询间隔来说重训代价太高，所以用 get_cached_shape_model() 缓存，一天
    只训一次，当天后续调用直接复用。

    做空前提（SMA站上再跌破）同样是跨根状态，也在下面这个重放循环里一起算。

    返回的 events 是今天所有触发过条件、且当时形态过滤允许开仓的根（不只是最后
    一根）——两次检查之间如果跳过了好几根 K 线（重启、网络慢），中间那些根上的
    信号不会因为"当时不是最后一根"就永久丢失，调用方可以拿 events 跟自己上次
    通知到哪儿了做对比，把漏掉的补上。
    """
    _mctx = market_mode(market or "stock", timeframe or "5m")
    _mctx.__enter__()
    try:
        symbol = (symbol or "").strip().upper()
        if not symbol:
            raise OhlcError("请填写股票代码")

        need_hist = max(volume_dist_days, volume_max_window_days, context_days)
        fetch_start = date.today() - timedelta(days=(need_hist + 5) * 2 + 20)

        raw_d = fetch_closes(symbol, "1d", apply_live=False)
        daily = wash_bars(raw_d)
        atr_sorted_dates, atr_date_to_record = build_daily_atr_series(daily, daily_atr_n)

        bars_5, days, all_rth_idx = load_context(symbol, fetch_start)
        if not days:
            raise OhlcError(f"{symbol} 没有拿到 5min 数据")

        day_pos = len(days) - 1
        today_d = days[day_pos]
        setup = build_day_setup(
            bars_5, all_rth_idx, days, day_pos, atr_sorted_dates, atr_date_to_record,
            context_days, volume_dist_days, volume_max_window_days,
            volume_high_pct, volume_low_pct, min_slot_samples, stop_loss_atr_mult,
        )
        if setup is None:
            raise OhlcError(f"{symbol} 历史铺垫不够（需要前 {need_hist} 天历史）")

        if use_regime:
            return _current_regime_signal(
                symbol, bars_5, all_rth_idx, days, atr_sorted_dates, atr_date_to_record,
                context_days=context_days, volume_dist_days=volume_dist_days,
                volume_max_window_days=volume_max_window_days, volume_high_pct=volume_high_pct,
                volume_low_pct=volume_low_pct, min_slot_samples=min_slot_samples,
                stop_loss_atr_mult=stop_loss_atr_mult, lookback=lookback,
                vwap_trend_lookback=vwap_trend_lookback, sma_short_period=sma_short_period,
                sma_long_period=sma_long_period, use_sma_precondition=use_sma_precondition,
                allow_short=allow_short, regime_kwargs=_regime_kwargs(locals()),
            )

        signals = compute_day_signals(
            bars_5, setup["today_idx"], setup["series_idx"],
            setup["thresholds"], setup["rolling_max_init"],
            lookback, vwap_trend_lookback, sma_short_period, sma_long_period,
        )
        if not signals:
            raise OhlcError(f"{symbol} 今天还没有盘中 K 线")

        warnings: list[str] = []

        market_index = market_stamps = None
        market_symbol_clean = (market_symbol or "").strip().upper() or None
        if use_shape_filter and market_symbol_clean and market_symbol_clean != symbol:
            m_bars_5, _md, _mi = load_context(market_symbol_clean, fetch_start)
            if m_bars_5:
                market_index = bar_map(m_bars_5)
                market_stamps = sorted(market_index)
            else:
                warnings.append(f"大盘参照标的「{market_symbol_clean}」拉不到5min数据，强上涨/强下跌判断跳过SPY方向一致这一条")

        shape_day_plan = None
        shape_predicted_name = shape_confidence = None
        if use_shape_filter:
            pos_in_days = days.index(today_d)
            yest_d = days[pos_in_days - 1] if pos_in_days > 0 else None
            pred = None
            if yest_d is not None:
                try:
                    model = get_cached_shape_model(symbol, today_d, min_train_days=shape_min_train_days)
                    pred = predict_shape_for_day(model, today_d, yest_d)
                except OhlcError as e:
                    warnings.append(f"形态过滤未启用：{e}")
            if pred is None or pred["confidence"] < shape_confidence_threshold:
                shape_day_plan = SHAPE_DAY_PLAN["其他"]
                shape_predicted_name = pred["shape"] if pred else None
                shape_confidence = pred["confidence"] if pred else None
            else:
                shape_day_plan = SHAPE_DAY_PLAN.get(pred["shape"], SHAPE_DAY_PLAN["其他"])
                shape_predicted_name = pred["shape"]
                shape_confidence = pred["confidence"]

        stop_dollar = setup["stop_dollar_long"]
        today_str = str(today_d)
        events: list[dict] = []
        stood_long = stood_short = False
        uptrend_phase = downtrend_phase = "normal"
        shape_switched = False
        mid_pos_today = len(signals) // 2 if shape_day_plan and shape_day_plan["type"] == "time_split" else None
        current_substate = None
        last_allow_buy = last_allow_sell = True

        for pos_today, sig in enumerate(signals):
            close = sig["bar"]["close"]
            if allow_short and use_sma_precondition:
                if sig["sma_long"] is not None and close > sig["sma_long"]:
                    stood_long = True
                if sig["sma_short"] is not None and close > sig["sma_short"]:
                    stood_short = True
                broken_long = sig["sma_long"] is not None and close < sig["sma_long"]
                broken_short = sig["sma_short"] is not None and close < sig["sma_short"]
                short_precondition = (stood_long and broken_long) or (stood_short and broken_short)
            else:
                short_precondition = True

            # 形态过滤：跟 walk_forward 用同一套状态机重放，见那边的详细注释。
            allow_buy, allow_sell = True, True
            current_substate = None
            if shape_day_plan is not None:
                minutes_since_open = pos_today * 5
                minutes_to_close = minutes_to_last_bar(sig["time_slot"])
                in_open_blackout = minutes_since_open < SHAPE_FIRST_CHECK_MINUTES
                in_uptrend_close_blackout = (
                    shape_predicted_name == "上涨" and minutes_to_close < SHAPE_UPTREND_CLOSE_NO_TRADE_MINUTES
                )
                if in_open_blackout or in_uptrend_close_blackout:
                    allow_buy, allow_sell = False, False
                else:
                    if shape_day_plan["type"] == "fixed":
                        mode = shape_day_plan["mode"]
                    elif shape_day_plan["type"] == "time_split":
                        if not shape_switched and mid_pos_today is not None and pos_today >= mid_pos_today:
                            shape_switched = True
                        mode = shape_day_plan["phase2"] if shape_switched else shape_day_plan["phase1"]
                    elif shape_day_plan["type"] == "signal_split":
                        if not shape_switched and sig["turning"] == "转空":
                            shape_switched = True
                        mode = shape_day_plan["phase2"] if shape_switched else shape_day_plan["phase1"]
                    elif shape_day_plan["type"] == "uptrend_regime":
                        spy_direction = None
                        if market_index is not None and market_stamps:
                            spy_direction = market_direction_asof(
                                market_index, market_stamps, sig["bar"]["ts"], UPTREND_MARKET_LOOKBACK
                            )
                        if is_strong_uptrend(sig, spy_direction):
                            mode = "only_long"
                            current_substate = "强上涨"
                        else:
                            if uptrend_phase == "normal" and is_uptrend_exhaustion_trigger(sig):
                                uptrend_phase = "watch"
                            if uptrend_phase in ("normal", "watch") and is_uptrend_reversal_confirmed(sig):
                                uptrend_phase = "confirmed"
                            mode = {"normal": "only_long", "watch": "watch", "confirmed": "only_short"}[uptrend_phase]
                            current_substate = classify_uptrend_substate(sig, uptrend_phase)
                    elif shape_day_plan["type"] == "downtrend_regime":
                        spy_direction = None
                        if market_index is not None and market_stamps:
                            spy_direction = market_direction_asof(
                                market_index, market_stamps, sig["bar"]["ts"], UPTREND_MARKET_LOOKBACK
                            )
                        if is_strong_downtrend(sig, spy_direction):
                            mode = "only_short"
                            current_substate = "强下跌"
                        else:
                            if downtrend_phase == "normal" and is_downtrend_exhaustion_trigger(sig):
                                downtrend_phase = "watch"
                            if downtrend_phase in ("normal", "watch") and is_downtrend_reversal_confirmed(sig):
                                downtrend_phase = "confirmed"
                            mode = {"normal": "only_short", "watch": "watch", "confirmed": "only_long"}[downtrend_phase]
                            current_substate = classify_downtrend_substate(sig, downtrend_phase)
                    elif shape_day_plan["type"] == "sideways_regime":
                        current_substate = classify_sideways_substate(sig)
                        allow_buy, allow_sell = sideways_allowed_actions(current_substate, sig)
                        mode = None
                    else:
                        mode = "both"
                    if shape_day_plan["type"] != "sideways_regime":
                        allow_buy, allow_sell = shape_allowed_actions(mode)

            if close_no_trade_minutes > 0:
                if minutes_to_last_bar(sig["time_slot"]) < close_no_trade_minutes:
                    allow_buy, allow_sell = False, False

            last_allow_buy, last_allow_sell = allow_buy, allow_sell

            # 开仓方向用一阶导数（f'）判断涨跌，跟 walk_forward 保持一致
            buy_hit = sig["dtrend"] == "涨" and sig["vol_high"] and allow_buy
            sell_hit = bool(allow_short and sig["dtrend"] == "跌" and sig["vol_high"] and short_precondition and allow_sell)
            # "真反转"平仓同样叠加反转确认，跟 walk_forward 保持一致（见那边的注释）；
            # 平仓不受形态过滤限制——形态过滤只管"要不要开新仓"，已有仓位该走还是走。
            exit_long_hit = sig["turning"] == "转空" and sig["vol_high"] and is_uptrend_reversal_confirmed(sig)
            exit_short_hit = bool(
                allow_short and sig["turning"] == "转多" and sig["vol_high"] and is_downtrend_reversal_confirmed(sig)
            )

            if buy_hit:
                events.append({"day": today_str, "time": sig["time_slot"], "direction": "做多", "type": "买入", "price": round(close, 4)})
            if sell_hit:
                events.append({"day": today_str, "time": sig["time_slot"], "direction": "做空", "type": "卖出/做空", "price": round(close, 4)})
            if exit_long_hit:
                events.append({"day": today_str, "time": sig["time_slot"], "direction": "平多", "type": "多头反转", "price": round(close, 4)})
            if exit_short_hit:
                events.append({"day": today_str, "time": sig["time_slot"], "direction": "平空", "type": "空头反转", "price": round(close, 4)})

        last = signals[-1]
        bar = last["bar"]
        price = float(bar["close"])
        last_events = [e for e in events if e["time"] == last["time_slot"]]
        stop_buy = price - stop_dollar if stop_dollar is not None else None
        stop_sell = price + stop_dollar if stop_dollar is not None else None

        if last_events:
            direction = last_events[-1]["direction"]
            etype = last_events[-1]["type"]
            action = f"{etype} @ {price}" if etype in ("买入", "卖出/做空") else f"{etype}+放量，如持有对应方向仓位建议止盈/离场"
        elif shape_day_plan is not None and not (last_allow_buy or last_allow_sell):
            direction, action = "观望", f"形态过滤中：当前不开新仓（{shape_predicted_name or '其他/观望'}）"
        elif last["dtrend"] == "涨":
            direction, action = "观望", "一阶导数向上，等放量确认"
        elif last["dtrend"] == "跌" and allow_short:
            direction, action = "观望", "一阶导数向下，等放量确认"
        else:
            direction, action = "观望", "观望"

        return {
            "price": round(price, 4),
            "asof": last["time_slot"],
            "direction": direction,
            "action": action,
            "hit": bool(last_events),
            "events": events,
            "warnings": warnings,
            "shape": {
                "enabled": use_shape_filter,
                "predicted": shape_predicted_name,
                "confidence": shape_confidence,
                "substate": current_substate,
                "allow_buy": last_allow_buy,
                "allow_sell": last_allow_sell,
            },
            "snap": {
                "dtrend": last["dtrend"],
                "vtrend": last["vtrend"],
                "vol_high": last["vol_high"],
                "turning": last["turning"],
                "stop": round(stop_buy, 4) if stop_buy is not None else None,
                "stop_short": round(stop_sell, 4) if stop_sell is not None else None,
                "atr_stop_dollar": round(stop_dollar, 4) if stop_dollar is not None else None,
            },
        }

    finally:
        _mctx.__exit__(None, None, None)

# ---------------------------------------------------------------------------
# 形态分类与预测：把每天的 VWAP 走势归到 6 种轨道形态之一，用历史数据训练一个
# 多元逻辑回归（softmax 回归），预测"今天"最终会落进哪种形态、概率各是多少。
# 纯 Python 实现，不依赖 numpy/sklearn。
# ---------------------------------------------------------------------------

SHAPE_NAMES = [
    "下跌横盘", "上涨", "上涨下跌", "下跌上涨", "下跌", "上涨横盘",
    "横盘", "横盘上涨", "横盘下跌", "其他",
]
SHAPE_FLAT_THRESHOLD_PCT = 0.15  # "平"阈值（单位：百分比），半场VWAP变化幅度小于这个值算横盘
SHAPE_SMA_PERIOD = 20  # 日线 SMA 周期，用来算"昨天收盘相对均线的位置"这个特征
SHAPE_PREMARKET_BARS = 6  # 用开盘头几根 5min bar（默认6根=30分钟）的成交量算"早盘放量"特征
SHAPE_VOL_LOOKBACK_DAYS = 15  # 早盘成交量的历史基准用过去多少个交易日
SHAPE_MIN_TRAIN_DAYS = 30  # 训练样本至少要有这么多天，太少就不训练、直接报错提示
SHAPE_EPOCHS = 300  # 逻辑回归梯度下降轮数
SHAPE_LR = 0.3  # 梯度下降学习率
SHAPE_L2 = 0.01  # L2 正则强度，防止样本少时过拟合

SHAPE_FIRST_CHECK_MINUTES = 60  # 开盘后至少这么久才开始用形态预测限制方向（早盘数据攒够前不限制）
SHAPE_RECHECK_INTERVAL_MINUTES = 30  # 之后每隔多久重新检查一次触发条件（时间对半分/真反转）
SHAPE_CONFIDENCE_THRESHOLD = 40.0  # 预测最高概率低于此百分比，当天按"其他"处理（观望）
SHAPE_UPTREND_CLOSE_NO_TRADE_MINUTES = 30  # "上涨"形态：收盘前这么多分钟内不再开新仓（回测发现
                                            # 这段时间开的多单容易亏损，很多是隔夜止损）；
                                            # 只对"上涨"形态生效，其他形态不受影响，正常交易到收盘

# 每种形态对应的开单模式：
#   fixed          全天固定一个方向（mode）
#   time_split     按"全天对半分"的时间点切换（phase1 -> phase2），对应 classify_day_shape
#                  自己打标签时用的同一套"上半场/下半场"逻辑
#   signal_split   按"真反转"信号（二阶导数 turning="转空"）触发切换（phase1 -> phase2），
#                  只有出现真反转信号才算数，不看具体时间点
#   uptrend_regime "上涨"形态专用的三态强弱状态机（强上涨/普通上涨衰竭观察/反转确认），
#                  比 signal_split 更细——不是简单看一次"真反转"，而是分"衰竭"和
#                  "确认"两道门槛，且"强上涨"条件满足时会强制锁回只做多，不管当前
#                  处在哪个阶段。逻辑写在 walk_forward 里，不是靠这里的 phase 字段。
# mode / phase 的取值：only_long（只做多）/ only_short（只做空）/ both（双向都开）/ watch（观望不开新仓）
SHAPE_DAY_PLAN = {
    "下跌横盘": {"type": "time_split", "phase1": "only_short", "phase2": "both"},
    "上涨": {"type": "uptrend_regime"},
    "上涨下跌": {"type": "signal_split", "phase1": "only_long", "phase2": "only_short"},
    "下跌上涨": {"type": "fixed", "mode": "only_long"},
    "下跌": {"type": "downtrend_regime"},
    "上涨横盘": {"type": "fixed", "mode": "only_long"},
    "横盘": {"type": "sideways_regime"},
    "横盘上涨": {"type": "fixed", "mode": "only_long"},
    "横盘下跌": {"type": "fixed", "mode": "only_short"},
    "其他": {"type": "fixed", "mode": "watch"},
}


def shape_allowed_actions(mode: str) -> tuple[bool, bool]:
    """把 SHAPE_DAY_PLAN 里的 mode/phase 字符串翻译成 (是否允许开多, 是否允许开空)。"""
    return {
        "only_long": (True, False),
        "only_short": (False, True),
        "both": (True, True),
        "watch": (False, False),
    }.get(mode, (True, True))


def is_strong_uptrend(sig: dict, spy_direction: str | None) -> bool:
    """"强上涨"判断：这几个条件必须同时满足，缺一个都不算。满足时应该强制锁定
    只做多、完全不给做空的机会——这时候做空很容易被趋势直接轧掉。

        价格 > VWAP
        EMA20 > EMA50
        EMA20 斜率 > 0（正在往上走，不是走平）
        f' > 0（一阶导数确认还在涨）
        ADX > UPTREND_ADX_THRESHOLD（趋势强度够）
        成交量放大（vol_high）
        大盘（SPY）方向一致（spy_direction 传 None 表示没有大盘数据，这条不参与判断，
        视为满足，不因为缺数据就整体判不成立）
    """
    bar = sig["bar"]
    if sig["vwap"] is None or bar["close"] <= sig["vwap"]:
        return False
    if sig["ema_short"] is None or sig["ema_long"] is None or sig["ema_short"] <= sig["ema_long"]:
        return False
    if not sig["ema_short_rising"]:
        return False
    if sig["f1"] is None or sig["f1"] <= 0:
        return False
    if sig["adx"] is None or sig["adx"] <= UPTREND_ADX_THRESHOLD:
        return False
    if not sig["vol_high"]:
        return False
    if spy_direction is not None and spy_direction != "涨":
        return False
    return True


def is_uptrend_exhaustion_trigger(sig: dict) -> bool:
    """"普通上涨"里的衰竭信号：满足任意一条就算触发（这几条不要求同时出现），
    触发后从"正常只做多"切到"观察"（暂停开新仓，等反转确认或行情自己走出结论）。

        价格创当天新高，但成交量没有同步创新高（缩量新高，追高动能不足）
        价格离 VWAP 越来越远（短期过度偏离，追高风险变大）
        f' > 0 但 f'' < 0（表面还在涨，涨的速度已经在放缓）
    """
    if sig["is_new_high"] and not sig["new_high_volume_confirmed"]:
        return True
    if sig["vwap_dist_growing"]:
        return True
    if sig["f1"] is not None and sig["f2"] is not None and sig["f1"] > 0 and sig["f2"] < 0:
        return True
    return False


def is_uptrend_reversal_confirmed(sig: dict) -> bool:
    """"上涨衰竭"之后，要不要真的允许做空——这里不用单一条件，要求同时满足：

        f' < 0 且 f'' < 0（真正开始下跌，不是还在衰竭观察阶段）
        收盘价 < VWAP（VWAP 失守）
        成交量 z-score > UPTREND_VOLUME_Z_THRESHOLD（放量确认，不是随便一根缩量假摔）
        跌破近期结构低点（close_below_recent_low）

    全部满足才算"反转确认"，从这根开始双向都可以开（LONG + SHORT）。
    """
    if sig["f1"] is None or sig["f2"] is None or sig["f1"] >= 0 or sig["f2"] >= 0:
        return False
    if sig["vwap"] is None or sig["bar"]["close"] >= sig["vwap"]:
        return False
    if sig["volume_zscore"] is None or sig["volume_zscore"] <= UPTREND_VOLUME_Z_THRESHOLD:
        return False
    if not sig["close_below_recent_low"]:
        return False
    return True


def is_strong_downtrend(sig: dict, spy_direction: str | None) -> bool:
    """"强下跌"判断，跟 is_strong_uptrend 完全对称，方向反过来：

        价格 < VWAP
        EMA20 < EMA50
        EMA20 斜率 < 0（正在往下走）
        f' < 0（一阶导数确认还在跌）
        ADX > UPTREND_ADX_THRESHOLD（趋势强度够，跟涨跌方向无关，只看强弱）
        成交量放大（vol_high）
        大盘（SPY）方向一致（None 表示没有大盘数据，这条不参与判断）

    满足时应该强制锁定只做空，这时候做多很容易被趋势直接轧掉。
    """
    bar = sig["bar"]
    if sig["vwap"] is None or bar["close"] >= sig["vwap"]:
        return False
    if sig["ema_short"] is None or sig["ema_long"] is None or sig["ema_short"] >= sig["ema_long"]:
        return False
    if not sig["ema_short_falling"]:
        return False
    if sig["f1"] is None or sig["f1"] >= 0:
        return False
    if sig["adx"] is None or sig["adx"] <= UPTREND_ADX_THRESHOLD:
        return False
    if not sig["vol_high"]:
        return False
    if spy_direction is not None and spy_direction != "跌":
        return False
    return True


def is_downtrend_exhaustion_trigger(sig: dict) -> bool:
    """"普通下跌"里的衰竭信号，跟 is_uptrend_exhaustion_trigger 对称，满足任意一条
    就算触发：

        价格创当天新低，但成交量没有同步创新低（缩量新低，杀跌动能不足）
        价格离 VWAP 越来越远（往下偏离越来越多，超跌风险变大）
        f' < 0 但 f'' > 0（表面还在跌，跌的速度已经在放缓）
    """
    if sig["is_new_low"] and not sig["new_low_volume_confirmed"]:
        return True
    if sig["vwap_dist_growing"]:
        return True
    if sig["f1"] is not None and sig["f2"] is not None and sig["f1"] < 0 and sig["f2"] > 0:
        return True
    return False


def is_downtrend_reversal_confirmed(sig: dict) -> bool:
    """"下跌衰竭"之后，要不要真的允许做多——同样要求同时满足：

        f' > 0 且 f'' > 0（真正开始上涨）
        收盘价 > VWAP（VWAP 收复）
        成交量 z-score > UPTREND_VOLUME_Z_THRESHOLD（放量确认）
        突破近期结构高点（close_above_recent_high）
    """
    if sig["f1"] is None or sig["f2"] is None or sig["f1"] <= 0 or sig["f2"] <= 0:
        return False
    if sig["vwap"] is None or sig["bar"]["close"] <= sig["vwap"]:
        return False
    if sig["volume_zscore"] is None or sig["volume_zscore"] <= UPTREND_VOLUME_Z_THRESHOLD:
        return False
    if not sig["close_above_recent_high"]:
        return False
    return True


def classify_uptrend_substate(sig: dict, uptrend_phase: str) -> str:
    """给"上涨"母状态标一个更细的子状态标签，纯粹用来诊断（比如按子状态分开统计
    胜率），不改变实际的开单许可——"慢上涨"/"横盘上涨"/"快速上涨"这三种在交易
    许可上是完全一样的（都是只做多），差别只在于"为什么判定为上涨"。真正改变
    交易许可的是 uptrend_phase 本身（normal/watch/confirmed，已经在状态机里处理）。
    """
    if uptrend_phase == "watch":
        return "上涨衰竭"
    if uptrend_phase == "confirmed":
        return "上涨反转"
    # phase == "normal"：进一步区分是慢涨、横盘式慢慢抬高，还是快涨
    if sig["adx"] is not None and sig["adx"] > UPTREND_ADX_THRESHOLD:
        return "快速上涨"
    if sig["adx"] is not None and sig["adx"] <= 15 and sig["ema_short_rising"]:
        return "横盘上涨"
    return "慢上涨"


def classify_downtrend_substate(sig: dict, downtrend_phase: str) -> str:
    """"下跌"母状态的子状态标签，跟 classify_uptrend_substate 完全对称。"""
    if downtrend_phase == "watch":
        return "下跌衰竭"
    if downtrend_phase == "confirmed":
        return "下跌反转"
    if sig["adx"] is not None and sig["adx"] > UPTREND_ADX_THRESHOLD:
        return "快速下跌"
    if sig["adx"] is not None and sig["adx"] <= 15 and sig["ema_short_falling"]:
        return "横盘下跌"
    return "慢下跌"


def classify_sideways_substate(sig: dict) -> str:
    """"横盘"母状态四态细分：横盘扩张（区间正在变宽，可能要突破成趋势）/
    横盘收缩（区间正在变窄，等待方向）/ 窄横盘（区间本来就很窄）/ 宽横盘
    （区间不窄但也没有收缩扩张的迹象，维持震荡）。优先看宽度变化趋势（扩张/
    收缩这两种更有行动价值），没有明显变化趋势时，再按绝对宽度分窄/宽。
    """
    if sig["width_trend"] == "expanding":
        return "横盘扩张"
    if sig["width_trend"] == "compressing":
        return "横盘收缩"
    if sig["range_width_pct"] is not None and sig["range_width_pct"] < SIDEWAYS_NARROW_THRESHOLD_PCT:
        return "窄横盘"
    return "宽横盘"


def sideways_allowed_actions(substate: str, sig: dict) -> tuple[bool, bool]:
    """"横盘"四态各自的开单许可：
        窄横盘   ->  不交易（区间太窄，噪音占比太高）
        横盘收缩 ->  不交易（等待收缩结束、方向明朗）
        横盘扩张 ->  双向都开（区间正在被打破，可能要走出趋势，用现有的一阶导数
                     信号顺着突破方向走）
        宽横盘   ->  区间下沿（收盘价落在区间最下面 SIDEWAYS_RANGE_ZONE_FRACTION
                     比例以内）只做多，区间上沿只做空，中间地带不交易——高抛低吸，
                     不追中间的噪音
    """
    if substate in ("窄横盘", "横盘收缩"):
        return False, False
    if substate == "横盘扩张":
        return True, True
    # 宽横盘：按收盘价在区间里的相对位置决定方向
    if sig["recent_low"] is None or sig["recent_high"] is None:
        return False, False
    rng = sig["recent_high"] - sig["recent_low"]
    if rng <= 0:
        return False, False
    pos_in_range = (sig["bar"]["close"] - sig["recent_low"]) / rng
    if pos_in_range <= SIDEWAYS_RANGE_ZONE_FRACTION:
        return True, False
    if pos_in_range >= 1 - SIDEWAYS_RANGE_ZONE_FRACTION:
        return False, True
    return False, False


def classify_day_shape(
    bars_5: list[dict], today_idx: list[int], flat_threshold_pct: float = SHAPE_FLAT_THRESHOLD_PCT
) -> tuple[int, str, dict]:
    """给定某一天已经走完的所有 RTH 5min bar 下标，把当天 VWAP 走势归类到 9 种
    形态之一（外加"其他"兜底，对应 SHAPE_NAMES 的下标）。

    做法：用当天 VWAP 序列，把全天对半分成"上半场"和"下半场"，分别看 VWAP 从
    半场开始到半场结束的变化方向——涨/跌/平（flat_threshold_pct 是"平"的容忍带，
    单位：百分比，变化幅度小于这个值就算横盘不算趋势）——再把两段方向组合映射到
    最终形态：
        (跌,平)->下跌横盘    (涨,涨)->上涨      (涨,跌)->上涨下跌
        (跌,涨)->下跌上涨    (跌,跌)->下跌      (涨,平)->上涨横盘
        (平,平)->横盘        (平,涨)->横盘上涨  (平,跌)->横盘下跌
    这九种组合覆盖了"涨/跌/平"两两配对的全部可能，"其他"作为兜底理论上不会再
    被触发，只在异常情况下（比如 direction() 返回了非预期值）才会用到。

    只有当天已经完整走完（不是还在盘中的"今天"）时调用这个函数才有意义——用来
    给"今天"这个当前尚未走完的交易日预测形态时，绝不能用这个函数算它自己的标签。
    """
    other_idx = SHAPE_NAMES.index("其他")
    if len(today_idx) < 4:
        return other_idx, "其他", {}
    vwap_vals = running_vwap(bars_5, today_idx)
    n = len(vwap_vals)
    mid = n // 2

    def direction(start: float | None, end: float | None) -> tuple[str, float]:
        if start is None or end is None or start == 0:
            return "平", 0.0
        pct = (end - start) / start * 100
        if pct > flat_threshold_pct:
            return "涨", pct
        if pct < -flat_threshold_pct:
            return "跌", pct
        return "平", pct

    d1, pct1 = direction(vwap_vals[0], vwap_vals[mid - 1])
    d2, pct2 = direction(vwap_vals[mid], vwap_vals[-1])

    mapping = {
        ("跌", "平"): "下跌横盘",
        ("涨", "涨"): "上涨",
        ("涨", "跌"): "上涨下跌",
        ("跌", "涨"): "下跌上涨",
        ("跌", "跌"): "下跌",
        ("涨", "平"): "上涨横盘",
        ("平", "平"): "横盘",
        ("平", "涨"): "横盘上涨",
        ("平", "跌"): "横盘下跌",
    }
    name = mapping.get((d1, d2), "其他")
    idx = SHAPE_NAMES.index(name)
    return idx, name, {"first_half_dir": d1, "first_half_pct": round(pct1, 3),
                        "second_half_dir": d2, "second_half_pct": round(pct2, 3)}


def _one_hot(idx: int, n: int) -> list[float]:
    """把类别下标转成 one-hot 向量，用于把"昨天的形态"这种类别特征喂给逻辑回归。"""
    v = [0.0] * n
    if 0 <= idx < n:
        v[idx] = 1.0
    return v


def softmax(scores: list[float]) -> list[float]:
    """数值稳定版 softmax：先减去最大值再指数化，避免大数溢出。"""
    m = max(scores)
    exps = [math.exp(s - m) for s in scores]
    total = sum(exps)
    if total <= 0:
        return [1.0 / len(scores)] * len(scores)
    return [e / total for e in exps]


def _standardize_fit(X: list[list[float]]) -> tuple[list[float], list[float]]:
    """算每一列特征的均值和标准差，供训练集和预测样本共用同一套标准化参数。"""
    n = len(X)
    d = len(X[0]) if X else 0
    means = [sum(row[j] for row in X) / n for j in range(d)]
    stds = []
    for j in range(d):
        var = sum((row[j] - means[j]) ** 2 for row in X) / n
        std = math.sqrt(var)
        stds.append(std if std > 1e-9 else 1.0)
    return means, stds


def _standardize_apply(x: list[float], means: list[float], stds: list[float]) -> list[float]:
    return [(x[j] - means[j]) / stds[j] for j in range(len(x))]


def train_multinomial_logreg(
    X: list[list[float]], y: list[int], n_classes: int,
    epochs: int = SHAPE_EPOCHS, lr: float = SHAPE_LR, l2: float = SHAPE_L2,
) -> tuple[list[list[float]], list[float]]:
    """朴素批量梯度下降训练多元逻辑回归（softmax 回归），纯 Python 实现，不依赖
    numpy/sklearn——样本量是几百天的量级，纯 Python 循环几百轮也就几秒钟。

    X 必须已经标准化过（均值0方差1），y 是 0..n_classes-1 的整数标签。
    返回 (W, b)：W 是 n_classes 行×n_features 列的权重矩阵，b 是长度 n_classes
    的偏置——predict_proba_logreg() 用它们对新样本算 softmax(W·x+b)。
    """
    n = len(X)
    d = len(X[0]) if X else 0
    W = [[0.0] * d for _ in range(n_classes)]
    b = [0.0] * n_classes
    for _epoch in range(epochs):
        grad_W = [[0.0] * d for _ in range(n_classes)]
        grad_b = [0.0] * n_classes
        for i in range(n):
            x = X[i]
            scores = [sum(W[c][j] * x[j] for j in range(d)) + b[c] for c in range(n_classes)]
            probs = softmax(scores)
            for c in range(n_classes):
                err = probs[c] - (1.0 if y[i] == c else 0.0)
                grad_b[c] += err
                for j in range(d):
                    grad_W[c][j] += err * x[j]
        for c in range(n_classes):
            grad_b[c] /= n
            b[c] -= lr * grad_b[c]
            for j in range(d):
                grad_W[c][j] = grad_W[c][j] / n + l2 * W[c][j]
                W[c][j] -= lr * grad_W[c][j]
    return W, b


def predict_proba_logreg(W: list[list[float]], b: list[float], x: list[float]) -> list[float]:
    """用训练好的权重给一个（已标准化的）样本算出各类别的概率。"""
    n_classes = len(W)
    d = len(x)
    scores = [sum(W[c][j] * x[j] for j in range(d)) + b[c] for c in range(n_classes)]
    return softmax(scores)


def _shape_feature_row(
    today_d, yest_d, bars_5: list[dict], day_idx_map: dict, date_to_close: dict, date_to_sma: dict,
    atr_sorted_dates: list, atr_date_to_record: dict, premarket_vol_by_day: dict, shape_label_by_day: dict,
    days_all: list, premarket_bars: int, vol_lookback_days: int, n_classes: int,
) -> list[float] | None:
    """给定"今天"和"昨天"两个日期，拼出预测形态用的特征行。抽成独立函数是因为
    训练集构建（历史上每一天）和实际预测（回测里的某一天/实时的"今天"）要用
    完全同一套算法，不能出现两边逻辑不小心写岔了的情况。返回 None 表示这天数据
    不够（缺昨天收盘、缺当天bar、缺ATR历史），调用方应该跳过或视为"无法预测"。
    """
    if yest_d not in date_to_close:
        return None
    today_bars = day_idx_map.get(today_d, [])
    if not today_bars:
        return None

    gap_pct = (bars_5[today_bars[0]]["open"] - date_to_close[yest_d]) / date_to_close[yest_d] * 100

    hist_days = [d for d in days_all if d < today_d][-vol_lookback_days:]
    hist_vols = [premarket_vol_by_day[d] for d in hist_days if d in premarket_vol_by_day and premarket_vol_by_day[d] > 0]
    avg_hist_vol = sum(hist_vols) / len(hist_vols) if hist_vols else None
    today_premarket_vol = sum(float(bars_5[j].get("volume") or 0.0) for j in today_bars[:premarket_bars])
    premarket_vol_ratio = (today_premarket_vol / avg_hist_vol) if avg_hist_vol else 1.0

    pos_atr = bisect.bisect_left(atr_sorted_dates, today_d)
    if pos_atr == 0:
        return None  # ATR 历史不够，无法算当前波动率环境
    atr_pct = atr_date_to_record[atr_sorted_dates[pos_atr - 1]]["atr_pct"]

    sma_val = date_to_sma.get(yest_d)
    sma_position_pct = ((date_to_close[yest_d] - sma_val) / sma_val * 100) if sma_val else 0.0

    prev_shape_idx = shape_label_by_day.get(yest_d, SHAPE_NAMES.index("其他"))

    return [gap_pct, premarket_vol_ratio, atr_pct, sma_position_pct] + _one_hot(prev_shape_idx, n_classes)


def build_shape_training_data(
    symbol: str,
    *,
    daily_atr_n: int = DAILY_ATR_N,
    sma_period: int = SHAPE_SMA_PERIOD,
    premarket_bars: int = SHAPE_PREMARKET_BARS,
    vol_lookback_days: int = SHAPE_VOL_LOOKBACK_DAYS,
    flat_threshold_pct: float = SHAPE_FLAT_THRESHOLD_PCT,
    cutoff_day: date | None = None,
) -> dict:
    """拉数据、给历史上每个"已经走完"的交易日打形态标签，再拼出训练用的特征矩阵。

    每一行训练样本对应"某一天"，特征全部只用"这天开盘前/开盘头几根就能拿到"的
    信息（不会看到这天后面的走势，否则就是用未来数据预测未来，没有意义）：
        gap_pct            隔夜跳空幅度：(今天开盘 - 昨天收盘) / 昨天收盘 × 100
        premarket_vol_ratio 今天开盘头 premarket_bars 根的成交量，相对过去
                            vol_lookback_days 天同一时间段成交量均值的比值
                            （>1 说明比平时更放量）
        atr_pct             用"昨天之前"日线数据算出的滚动 ATR%（当前波动率环境）
        sma_position_pct    昨天收盘相对日线 SMA 的偏离幅度：
                            (昨天收盘 - SMA) / SMA × 100，正数=站上均线
        prev_shape          昨天自己是哪种形态（one-hot，7维，对应 SHAPE_NAMES）
    标签 y 是"今天"这一天最终走完之后，用 classify_day_shape() 算出的真实形态。

    cutoff_day 给了值时：只用 cutoff_day 之前的交易日构造训练样本（X/y），避免
    回测时"用回测区间里未来的日子训练、又拿来预测同一个区间"这种数据穿越。但
    形态标签（shape_label_by_day）仍然覆盖除最后一个已拉取交易日外的所有日子
    （包括 cutoff_day 之后、属于回测区间的日子）——因为"昨天的形态"作为特征，
    对回测区间里任何一天来说都是"已经发生、可以合法使用"的信息，不算未来数据。
    cutoff_day 为 None 时（不区分训练/回测），行为等价于原来的"预测今天"用法。

    Returns:
        {"X": [[...], ...], "y": [0..6, ...], "feature_names": [...],
         "days": [date, ...]（跟 X/y 一一对应，且都 < cutoff_day）,
         "shape_label_by_day": {date: idx}（覆盖到倒数第2个已拉取交易日为止）,
         "shape_name_by_day": {date: name}, "days_all": 所有拉取到的交易日,
         以及若干供后续按天预测复用的中间数据（bars_5/day_idx_map/日线相关字典等）}
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise OhlcError("请填写股票代码")

    need_hist = max(vol_lookback_days, sma_period, daily_atr_n) + 5
    fetch_start = date.today() - timedelta(days=(need_hist + 260) * 2 + 60)

    raw_d = fetch_closes(symbol, "1d", apply_live=False)
    daily = wash_bars(raw_d)
    if len(daily) < sma_period + 2:
        raise OhlcError(f"{symbol} 日线历史不够（至少需要 {sma_period + 2} 天）")
    atr_sorted_dates, atr_date_to_record = build_daily_atr_series(daily, daily_atr_n)

    daily_closes = [b["close"] for b in daily]
    sma_series = rolling_sma(daily_closes, sma_period)
    date_to_sma: dict = {}
    date_to_close: dict = {}
    for idx, b in enumerate(daily):
        d = session_day(b["ts"])
        date_to_close[d] = b["close"]
        if sma_series[idx] is not None:
            date_to_sma[d] = sma_series[idx]

    bars_5, days_all, all_rth_idx = load_context(symbol, fetch_start)
    if not days_all:
        raise OhlcError(f"{symbol} 没有拿到 5min 数据")

    day_idx_map = {d: [i for i in all_rth_idx if session_day(bars_5[i]["ts"]) == d] for d in days_all}

    # 给每一个"已经完整走完"的交易日打形态标签（覆盖 days_all 里除最后一天之外
    # 的所有日子，含回测区间内的日子——它们对"预测后面某天"来说都是已发生的合法
    # 信息），同时记下开盘头几根的成交量（算"早盘放量比"要用到这段历史）。
    # 最后一天可能还在盘中，不给它打标签。
    shape_label_by_day: dict = {}
    shape_name_by_day: dict = {}
    premarket_vol_by_day: dict = {}
    for i, d in enumerate(days_all):
        idxs = day_idx_map[d]
        premarket_vol_by_day[d] = sum(
            float(bars_5[j].get("volume") or 0.0) for j in idxs[:premarket_bars]
        )
        if i < len(days_all) - 1:
            label_idx, label_name, _detail = classify_day_shape(bars_5, idxs, flat_threshold_pct)
            shape_label_by_day[d] = label_idx
            shape_name_by_day[d] = label_name

    feature_names = ["gap_pct", "premarket_vol_ratio", "atr_pct", "sma_position_pct"] + [
        f"prev_shape_{name}" for name in SHAPE_NAMES
    ]

    X: list[list[float]] = []
    y: list[int] = []
    out_days: list = []

    # 训练样本只用 cutoff_day 之前的日子（不给就是原来"预测今天"的用法：用
    # 除最后一天外的全部历史）。从第2天开始（需要"昨天"）。
    labeled_days = [d for d in days_all if d in shape_label_by_day]
    if cutoff_day is not None:
        labeled_days = [d for d in labeled_days if d < cutoff_day]
    for pos in range(1, len(labeled_days)):
        today_d = labeled_days[pos]
        yest_d = labeled_days[pos - 1]
        row = _shape_feature_row(
            today_d, yest_d, bars_5, day_idx_map, date_to_close, date_to_sma,
            atr_sorted_dates, atr_date_to_record, premarket_vol_by_day, shape_label_by_day,
            days_all, premarket_bars, vol_lookback_days, len(SHAPE_NAMES),
        )
        if row is None:
            continue
        X.append(row)
        y.append(shape_label_by_day[today_d])
        out_days.append(today_d)

    return {
        "X": X, "y": y, "feature_names": feature_names, "days": out_days,
        "shape_label_by_day": shape_label_by_day, "shape_name_by_day": shape_name_by_day,
        "bars_5": bars_5, "days_all": days_all, "day_idx_map": day_idx_map,
        "date_to_close": date_to_close, "date_to_sma": date_to_sma,
        "atr_sorted_dates": atr_sorted_dates, "atr_date_to_record": atr_date_to_record,
        "premarket_vol_by_day": premarket_vol_by_day,
    }


def predict_today_shape(
    symbol: str,
    *,
    daily_atr_n: int = DAILY_ATR_N,
    sma_period: int = SHAPE_SMA_PERIOD,
    premarket_bars: int = SHAPE_PREMARKET_BARS,
    vol_lookback_days: int = SHAPE_VOL_LOOKBACK_DAYS,
    flat_threshold_pct: float = SHAPE_FLAT_THRESHOLD_PCT,
    epochs: int = SHAPE_EPOCHS,
    lr: float = SHAPE_LR,
    l2: float = SHAPE_L2,
    min_train_days: int = SHAPE_MIN_TRAIN_DAYS,
) -> dict:
    """用历史数据训练一个多元逻辑回归，预测"今天"（数据里最新一天，可能还在盘中）
    最终会落进 6 种 VWAP 轨道形态里的哪一种，给出每种形态的概率。

    训练集：build_shape_training_data() 拼出的历史"特征->已知形态标签"样本对
    （每天都是用"开盘前能看到的信息"预测"这天最终的形态"，不偷看未来）。
    预测目标："今天"这一天，用同样的规则算出它当前能看到的特征（隔夜跳空、
    开盘头几根的放量情况、当前波动率环境、昨天收盘相对均线的位置、昨天自己是
    什么形态），喂进训练好的模型，得到 6+1 种形态各自的概率。

    min_train_days：训练样本至少要有这么多天才训练，太少就直接报错——默认用
    SHAPE_MIN_TRAIN_DAYS，可以按需调低/调高，不用改模块常量。

    注意：这是纯统计意义上的经验概率估计，不是精确预测——训练样本量、市场结构
    变化、flat_threshold_pct 这个"横盘"判定阈值的选取，都会实质性影响概率结果，
    仅供参考，不构成任何交易建议。
    """
    data = build_shape_training_data(
        symbol, daily_atr_n=daily_atr_n, sma_period=sma_period, premarket_bars=premarket_bars,
        vol_lookback_days=vol_lookback_days, flat_threshold_pct=flat_threshold_pct,
    )
    X, y, days_out = data["X"], data["y"], data["days"]
    if len(X) < min_train_days:
        raise OhlcError(
            f"{symbol} 可用训练样本只有 {len(X)} 天，少于 min_train_days={min_train_days}，"
            f"结果不可靠，建议换更长的历史区间或降低 min_train_days 再试"
        )

    means, stds = _standardize_fit(X)
    X_std = [_standardize_apply(row, means, stds) for row in X]
    n_classes = len(SHAPE_NAMES)
    W, b = train_multinomial_logreg(X_std, y, n_classes, epochs=epochs, lr=lr, l2=l2)

    days_all = data["days_all"]
    if len(days_all) < 2:
        raise OhlcError(f"{symbol} 交易日数量不够，无法构造\"今天\"的预测特征")
    today_d = days_all[-1]
    yest_d = days_all[-2]
    bars_5 = data["bars_5"]
    day_idx_map = data["day_idx_map"]
    date_to_close = data["date_to_close"]
    date_to_sma = data["date_to_sma"]
    premarket_vol_by_day = data["premarket_vol_by_day"]
    shape_label_by_day = data["shape_label_by_day"]
    shape_name_by_day = data["shape_name_by_day"]

    today_bars = day_idx_map.get(today_d, [])
    if not today_bars:
        raise OhlcError(f"{symbol} 今天还没有盘中 K 线，无法预测")
    if yest_d not in date_to_close:
        raise OhlcError(f"{symbol} 缺少昨天的收盘数据，无法算隔夜跳空")

    atr_sorted_dates = data["atr_sorted_dates"]
    atr_date_to_record = data["atr_date_to_record"]
    x_row = _shape_feature_row(
        today_d, yest_d, bars_5, day_idx_map, date_to_close, date_to_sma,
        atr_sorted_dates, atr_date_to_record, premarket_vol_by_day, shape_label_by_day,
        days_all, premarket_bars, vol_lookback_days, n_classes,
    )
    if x_row is None:
        raise OhlcError(f"{symbol} 当前数据不够，无法算出预测特征（可能是ATR历史不够）")
    gap_pct, premarket_vol_ratio, atr_pct, sma_position_pct = x_row[0], x_row[1], x_row[2], x_row[3]
    prev_shape_name = shape_name_by_day.get(yest_d, "其他")

    x_std = _standardize_apply(x_row, means, stds)
    probs = predict_proba_logreg(W, b, x_std)

    class_counts: dict = {}
    for label_idx in y:
        name = SHAPE_NAMES[label_idx]
        class_counts[name] = class_counts.get(name, 0) + 1

    probabilities = sorted(
        [{"shape": SHAPE_NAMES[c], "prob": round(probs[c] * 100, 2)} for c in range(n_classes)],
        key=lambda r: -r["prob"],
    )

    return {
        "symbol": symbol,
        "as_of_day": str(today_d),
        "n_train_days": len(X),
        "class_counts": class_counts,
        "features": {
            "gap_pct": round(gap_pct, 3),
            "premarket_vol_ratio": round(premarket_vol_ratio, 3),
            "atr_pct": round(atr_pct, 3),
            "sma_position_pct": round(sma_position_pct, 3),
            "prev_shape": prev_shape_name,
        },
        "probabilities": probabilities,
    }


def build_shape_model(
    symbol: str,
    cutoff_day: date,
    *,
    daily_atr_n: int = DAILY_ATR_N,
    sma_period: int = SHAPE_SMA_PERIOD,
    premarket_bars: int = SHAPE_PREMARKET_BARS,
    vol_lookback_days: int = SHAPE_VOL_LOOKBACK_DAYS,
    flat_threshold_pct: float = SHAPE_FLAT_THRESHOLD_PCT,
    epochs: int = SHAPE_EPOCHS,
    lr: float = SHAPE_LR,
    l2: float = SHAPE_L2,
    min_train_days: int = SHAPE_MIN_TRAIN_DAYS,
) -> dict:
    """训练一次形态预测模型，只用 cutoff_day 之前的历史数据（不看 cutoff_day 当天
    及之后的任何东西），给回测用。回测区间内每一天的形态预测都复用这一个模型
    （不是每天重新训练）——这是个简化：真实情况下模型应该随时间推移滚动重新
    训练，但那样每天都要重跑一次梯度下降，对一段几十上百天的回测来说太慢。
    当前这个简化对回测评估区间本身仍然是"时间点正确"的（模型只用了 cutoff_day
    之前的信息），只是没有模拟"越往后训练数据越多、模型应该越准"这件事。

    min_train_days：训练样本至少要有这么多天才训练，太少就直接报错——默认用
    SHAPE_MIN_TRAIN_DAYS，可以按需调低/调高，不用改模块常量。

    返回的字典可以直接喂给 predict_shape_for_day() 反复调用，不用每次重训练。
    """
    data = build_shape_training_data(
        symbol, daily_atr_n=daily_atr_n, sma_period=sma_period, premarket_bars=premarket_bars,
        vol_lookback_days=vol_lookback_days, flat_threshold_pct=flat_threshold_pct, cutoff_day=cutoff_day,
    )
    X, y = data["X"], data["y"]
    if len(X) < min_train_days:
        raise OhlcError(
            f"{symbol} 在 {cutoff_day} 之前只有 {len(X)} 天可用训练样本，"
            f"少于 min_train_days={min_train_days}，形态过滤不可用"
        )
    means, stds = _standardize_fit(X)
    X_std = [_standardize_apply(row, means, stds) for row in X]
    n_classes = len(SHAPE_NAMES)
    W, b = train_multinomial_logreg(X_std, y, n_classes, epochs=epochs, lr=lr, l2=l2)

    data["means"] = means
    data["stds"] = stds
    data["W"] = W
    data["b"] = b
    data["n_train_days"] = len(X)
    data["premarket_bars"] = premarket_bars
    data["vol_lookback_days"] = vol_lookback_days
    return data


def predict_shape_for_day(model: dict, today_d, yest_d) -> dict | None:
    """用 build_shape_model() 训练好的模型，预测某一天（通常是回测里的某一天，
    也可以是实时的"今天"）会落进哪种形态。返回 None 表示这天数据不够（缺
    昨天收盘、缺当天bar、缺ATR历史），调用方应视为"预测不可用"。
    """
    n_classes = len(SHAPE_NAMES)
    row = _shape_feature_row(
        today_d, yest_d, model["bars_5"], model["day_idx_map"], model["date_to_close"], model["date_to_sma"],
        model["atr_sorted_dates"], model["atr_date_to_record"], model["premarket_vol_by_day"],
        model["shape_label_by_day"], model["days_all"], model["premarket_bars"], model["vol_lookback_days"], n_classes,
    )
    if row is None:
        return None
    x_std = _standardize_apply(row, model["means"], model["stds"])
    probs = predict_proba_logreg(model["W"], model["b"], x_std)
    probabilities = sorted(
        [{"shape": SHAPE_NAMES[c], "prob": round(probs[c] * 100, 2)} for c in range(n_classes)],
        key=lambda r: -r["prob"],
    )
    top = probabilities[0]
    return {"shape": top["shape"], "confidence": top["prob"], "probabilities": probabilities}


# ---------------------------------------------------------------------------
# 形态模型缓存：给实盘监听用，一天只训练一次，同一天内多次轮询复用同一个模型。
# 训练一次要跑 300 轮梯度下降，实盘按 30 秒/次轮询的话每次都重训会很卡。
# 进程重启会丢缓存，下一次调用自动重新训练，不影响正确性，只是重启后第一次会
# 稍慢；缓存 key 只用 symbol，不同股票各自独立缓存。
# ---------------------------------------------------------------------------

_shape_model_cache: dict[str, tuple[str, dict]] = {}

# 开发时 uvicorn --reload 只要存了一个后端文件就会重启整个进程，
# _shape_model_cache 这种纯内存缓存每次都被清空——哪怕训练那一刻是在开盘前，
# 只要盘中又有一次热重载，下一次轮询照样得现场重训 20-30 秒，把这段时间内的
# 信号全部拖成事后补发。这里额外把训好的模型落一份到本地文件，进程重启后先
# 试着从磁盘读，读到当天的就直接用，不用等现场重训。
_SHAPE_MODEL_CACHE_DIR = Path("data") / "shape_model_cache"


def _shape_model_cache_path(symbol: str) -> Path:
    return _SHAPE_MODEL_CACHE_DIR / f"{symbol}.pkl"


def get_cached_shape_model(symbol: str, cutoff_day, **kwargs) -> dict:
    """cutoff_day 变了（新的一天）就重新训练，同一天内直接返回缓存的模型（先查
    内存缓存，内存没有再查磁盘缓存，磁盘也没有才真的现场训练）。"""
    cutoff_str = str(cutoff_day)
    cached = _shape_model_cache.get(symbol)
    if cached and cached[0] == cutoff_str:
        return cached[1]

    disk_path = _shape_model_cache_path(symbol)
    if disk_path.exists():
        try:
            with open(disk_path, "rb") as f:
                disk_day, disk_model = pickle.load(f)
            if disk_day == cutoff_str:
                _shape_model_cache[symbol] = (disk_day, disk_model)
                return disk_model
        except Exception:
            pass  # 磁盘缓存读坏了就当没有，走下面重新训练，不影响正确性

    model = build_shape_model(symbol, cutoff_day, **kwargs)
    _shape_model_cache[symbol] = (cutoff_str, model)
    try:
        _SHAPE_MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(disk_path, "wb") as f:
            pickle.dump((cutoff_str, model), f)
    except Exception:
        pass  # 落盘失败不影响这次返回结果，只是下次重启还得重训一次
    return model


# ---------------------------------------------------------------------------
# 回测：支持"最近 N 天"或"指定日期区间"两种模式，返回结构化数据（不打印）
# ---------------------------------------------------------------------------

def run_backtest(
    symbol: str,
    range_start: date | None = None,
    range_end: date | None = None,
    *,
    lookback: int = LOOKBACK,
    context_days: int = CONTEXT_DAYS,
    vwap_trend_lookback: int = VWAP_TREND_LOOKBACK,
    volume_dist_days: int = VOLUME_DIST_DAYS,
    volume_high_pct: float = VOLUME_HIGH_PCT,
    volume_low_pct: float = VOLUME_LOW_PCT,
    min_slot_samples: int = MIN_SLOT_SAMPLES,
    volume_max_window_days: int = VOLUME_MAX_WINDOW_DAYS,
    sma_short_period: int = SMA_SHORT_PERIOD,
    sma_long_period: int = SMA_LONG_PERIOD,
    use_sma_precondition: bool = USE_SMA_PRECONDITION,
    allow_short: bool = ALLOW_SHORT,
    short_symbol: str | None = None,
    daily_atr_n: int = DAILY_ATR_N,
    stop_loss_atr_mult: float = STOP_LOSS_ATR_MULT,
    backtest_trading_days: int = BACKTEST_TRADING_DAYS,
    capital_per_trade: float = CAPITAL_PER_TRADE,
    compound: bool = COMPOUND,
    trade_every_signal: bool = TRADE_EVERY_SIGNAL,
    debug_days: list[str] | None = None,
    use_shape_filter: bool = False,
    shape_confidence_threshold: float = SHAPE_CONFIDENCE_THRESHOLD,
    shape_min_train_days: int = SHAPE_MIN_TRAIN_DAYS,
    close_no_trade_minutes: int = CLOSE_NO_TRADE_MINUTES,
    market_symbol: str | None = UPTREND_MARKET_SYMBOL,
    market: str = "stock",
    timeframe: str = "5m",
    use_regime: bool = False,
    regime_atr_n: int = REGIME_ATR_N,
    regime_impulse_minutes: int = REGIME_IMPULSE_MINUTES,
    regime_impulse_er: float = REGIME_IMPULSE_ER,
    regime_impulse_atr: float = REGIME_IMPULSE_ATR,
    regime_trend_bars: int = REGIME_TREND_BARS,
    regime_sideways_er: float = REGIME_SIDEWAYS_ER,
    regime_sideways_adx: float = REGIME_SIDEWAYS_ADX,
    regime_channel_k: float = REGIME_CHANNEL_K,
    regime_trail_atr: float = REGIME_TRAIL_ATR,
    regime_impulse_giveback: float = REGIME_IMPULSE_GIVEBACK,
    regime_trend_break_atr: float = REGIME_TREND_BREAK_ATR,
    regime_range_stop_atr: float = REGIME_RANGE_STOP_ATR,
    regime_impulse_chase: bool = True,
    regime_range_trade: bool = True,
    regime_trend_trade: bool = True,
    regime_trend_entry: str = "breakout",
    regime_trend_min_slope: float = 1.5,
    **_ignored,
) -> dict:
    """回测，支持两种模式：

    - 不传 range_start/range_end：回测最近 backtest_trading_days 个交易日。
    - 传入 range_start/range_end：只统计这个日期区间内（含首尾）的交易日，用于按月/
      按区间分段测试。区间之前仍需要额外的历史天数做"铺垫"，铺垫数据不计入回测统计。
      如果数据源实际覆盖不到这个区间，会在返回结果的 warnings 里说明实际能覆盖的范围。

    trade_every_signal=True 时：不再要求"必须空仓才能进场"——只要买/卖信号触发，
    如果当前持有反向仓位就先平仓再反手，空仓则直接开仓，同向仓位则视为已满足、不
    重复开仓。用来回答"如果每一次信号我都采取行动，胜率/交易笔数会是多少"，summary
    里的 win_rate 就是这种打法下的胜率；daily_report 里能看到这个胜率按天怎么变化。

    use_shape_filter=True 时：开回测区间第一天之前的历史数据训练一次形态预测模型
    （不会用到回测区间本身的未来数据），然后给区间里每一天预测"今天最可能是哪种
    VWAP轨道形态"，按 SHAPE_DAY_PLAN 决定当天的开单模式：
        上涨 / 下跌上涨          -> 全天只做多
        下跌                     -> 全天只做空
        上涨下跌                 -> 出现"真反转"信号前只做多，之后只做空
        下跌横盘 / 上涨横盘      -> 全天对半分，前半场只做多/只做空，后半场双向都开
        其他 / 预测置信度不够    -> 观望，当天不开新仓
    预测置信度（最高概率）低于 shape_confidence_threshold（默认40%）时，当天按
    "其他"处理（观望）。开盘头 SHAPE_FIRST_CHECK_MINUTES 分钟内数据还不够，不
    限制方向。这套过滤只影响"要不要开新仓"，已有仓位的止损/真反转平仓不受影响。
    如果训练样本不够（历史数据太短），会在 warnings 里说明，并自动关闭这个过滤
    （不会让整个回测失败）。返回结果里的 shape_predictions 记录了每天的预测详情，
    方便你核对"这天为什么是这个开单模式"。

    注意一个简化：模型只在回测开始前训练一次，回测区间内每天都复用同一个模型，
    不是每天滚动重新训练——这样对回测评估区间本身仍然是"时间点正确"的（没有用
    未来数据训练），只是没有模拟"越往后训练数据越多、模型该更准"这件事。

    debug_days：传入一组日期字符串（如 ["2026-08-05"]）时，返回结果里会多一个
    bar_log 字段——列出这几天每一根 5min bar 的完整原始数据（开高低收、成交量、
    VWAP 数值和趋势、放量判断、SMA短/长、二阶导数转折、SMA站上再跌破前提、以及
    这一根是否真的触发了买入/卖出/多头反转/空头反转条件），用来诊断"某天为什么
    会/不会出现某个信号"。不传则 bar_log 为空列表，不影响正常回测。

    short_symbol：主标的不方便融券时，填一个 ETF 代码，做空信号照样看主标的自己的
    走势判断，但实际开/平空单、止损检查都换成这个 ETF 同一时刻的价格和它自己的日线
    ATR。留空则直接做空主标的本身（原来的行为）。

    Returns:
        结构化字典：symbol、config（回显本次用的全部参数）、data_range、
        effective_range、warnings、n_days、skipped_no_context、trades、summary、
        signal_log、daily_signal_counts、daily_trade_stats、daily_report、bar_log、
        shape_predictions。daily_report 是最方便看"信号准确度"按天变化的一张表；
        shape_predictions 只有 use_shape_filter=True 才有内容。
    """
    _mctx = market_mode(market or "stock", timeframe or "5m")
    _mctx.__enter__()
    try:
        symbol = (symbol or "").strip().upper()
        if not symbol:
            raise OhlcError("请填写股票代码")
        short_symbol = (short_symbol or "").strip().upper() or None
        debug_day_set = {str(d) for d in (debug_days or [])}

        need_hist = max(volume_dist_days, volume_max_window_days, context_days)
        if range_start is not None:
            fetch_start = range_start - timedelta(days=need_hist * 2 + 20)
        else:
            fetch_start = date.today() - timedelta(days=(need_hist + backtest_trading_days) * 2 + 40)

        config = {
            "lookback": lookback,
            "context_days": context_days,
            "vwap_trend_lookback": vwap_trend_lookback,
            "volume_dist_days": volume_dist_days,
            "volume_high_pct": volume_high_pct,
            "volume_low_pct": volume_low_pct,
            "min_slot_samples": min_slot_samples,
            "volume_max_window_days": volume_max_window_days,
            "sma_short_period": sma_short_period,
            "sma_long_period": sma_long_period,
            "use_sma_precondition": use_sma_precondition,
            "allow_short": allow_short,
            "short_symbol": short_symbol,
            "daily_atr_n": daily_atr_n,
            "stop_loss_atr_mult": stop_loss_atr_mult,
            "backtest_trading_days": backtest_trading_days,
            "capital_per_trade": capital_per_trade,
            "compound": compound,
            "trade_every_signal": trade_every_signal,
            "use_shape_filter": use_shape_filter,
            "shape_confidence_threshold": shape_confidence_threshold,
            "shape_min_train_days": shape_min_train_days,
            "close_no_trade_minutes": close_no_trade_minutes,
            "market_symbol": market_symbol,
            "timeframe": timeframe or _bar_tf(),
        }

        raw_d = fetch_closes(symbol, "1d", apply_live=False)
        daily = wash_bars(raw_d)
        atr_sorted_dates, atr_date_to_record = build_daily_atr_series(daily, daily_atr_n)

        short_atr_sorted_dates = short_atr_date_to_record = None
        etf_index = etf_stamps = None
        if allow_short and short_symbol:
            etf_daily = wash_bars(fetch_closes(short_symbol, "1d", apply_live=False))
            short_atr_sorted_dates, short_atr_date_to_record = build_daily_atr_series(etf_daily, daily_atr_n)
            etf_bars_5, _etf_days, _etf_rth_idx = load_context(short_symbol, fetch_start)
            if not etf_bars_5:
                raise OhlcError(f"做空用的 ETF「{short_symbol}」拉不到 5min 数据")
            etf_index = bar_map(etf_bars_5)
            etf_stamps = sorted(etf_index)

        market_index = market_stamps = None
        market_symbol_clean = (market_symbol or "").strip().upper() or None
        if market_symbol_clean and market_symbol_clean != symbol:
            market_bars_5, _market_days, _market_rth_idx = load_context(market_symbol_clean, fetch_start)
            if market_bars_5:
                market_index = bar_map(market_bars_5)
                market_stamps = sorted(market_index)

        bars_5, days, all_rth_idx = load_context(symbol, fetch_start)

        warnings: list[str] = []
        if market_symbol_clean and market_symbol_clean != symbol and market_index is None:
            warnings.append(
                f"大盘参照标的「{market_symbol_clean}」拉不到5min数据，"
                f"\"上涨\"形态的强上涨判断里 SPY方向一致 这一条会被跳过（不参与判断）"
            )

        if not days:
            return {
                "symbol": symbol, "config": config, "data_range": None, "effective_range": None,
                "warnings": ["没有拿到任何 5min 数据，请检查代码/网络/数据源。"],
                "n_days": 0, "skipped_no_context": 0, "trades": [], "summary": summarize([], capital_per_trade, compound),
                "signal_log": [], "daily_signal_counts": [], "daily_trade_stats": [], "daily_report": [], "bar_log": [],
                "shape_predictions": [],
            }

        data_first_day, data_last_day = days[0], days[-1]

        if range_start is not None:
            eff_start = max(range_start, data_first_day)
            eff_end = min(range_end or data_last_day, data_last_day)
            if eff_start > range_start or (range_end and eff_end < range_end):
                warnings.append(
                    f"数据源实际能覆盖的范围是 {data_first_day} ~ {data_last_day}，"
                    f"本次实际回测区间调整为 {eff_start} ~ {eff_end}"
                )
            candidate_positions = [i for i, d in enumerate(days) if eff_start <= d <= eff_end]
        else:
            candidate_positions = list(range(max(0, len(days) - backtest_trading_days), len(days)))

        day_setups = []
        skipped_no_context = 0
        for dp in candidate_positions:
            setup = build_day_setup(
                bars_5, all_rth_idx, days, dp, atr_sorted_dates, atr_date_to_record,
                context_days, volume_dist_days, volume_max_window_days,
                volume_high_pct, volume_low_pct, min_slot_samples, stop_loss_atr_mult,
                short_atr_sorted_dates, short_atr_date_to_record,
            )
            if setup is None:
                skipped_no_context += 1
                continue
            day_setups.append(setup)

        if skipped_no_context:
            warnings.append(
                f"区间开头有 {skipped_no_context} 个交易日因为历史铺垫不够"
                f"（需要前 {need_hist} 天历史）被跳过，不参与回测统计"
            )

        shape_predictions: list[dict] = []
        if use_shape_filter and day_setups:
            cutoff_day = day_setups[0]["today"]
            try:
                shape_model = build_shape_model(symbol, cutoff_day, min_train_days=shape_min_train_days)
            except OhlcError as e:
                warnings.append(f"形态过滤未启用：{e}")
                shape_model = None
            if shape_model is not None:
                for setup in day_setups:
                    today_d = setup["today"]
                    pos_in_days = days.index(today_d)
                    yest_d = days[pos_in_days - 1] if pos_in_days > 0 else None
                    pred = predict_shape_for_day(shape_model, today_d, yest_d) if yest_d is not None else None
                    setup["shape_predicted_name"] = pred["shape"] if pred else None
                    setup["shape_confidence"] = pred["confidence"] if pred else None
                    if pred is None or pred["confidence"] < shape_confidence_threshold:
                        day_plan = SHAPE_DAY_PLAN["其他"]
                        reason = "预测数据不够" if pred is None else f"置信度{pred['confidence']}%低于阈值{shape_confidence_threshold}%"
                        setup["shape_day_plan"] = day_plan
                        shape_predictions.append({
                            "day": str(today_d), "predicted_shape": pred["shape"] if pred else None,
                            "confidence": pred["confidence"] if pred else None,
                            "applied_mode": "观望（不启用过滤）", "reason": reason,
                        })
                    else:
                        day_plan = SHAPE_DAY_PLAN.get(pred["shape"], SHAPE_DAY_PLAN["其他"])
                        setup["shape_day_plan"] = day_plan
                        if day_plan["type"] == "fixed":
                            mode_desc = day_plan["mode"]
                        elif day_plan["type"] == "uptrend_regime":
                            mode_desc = "强上涨=只多 / 普通上涨=只多 / 衰竭=观望 / 反转确认=只空（uptrend_regime）"
                        elif day_plan["type"] == "downtrend_regime":
                            mode_desc = "强下跌=只空 / 普通下跌=只空 / 衰竭=观望 / 反转确认=只多（downtrend_regime）"
                        elif day_plan["type"] == "sideways_regime":
                            mode_desc = "窄横盘/横盘收缩=观望 / 横盘扩张=双向 / 宽横盘=下沿只多上沿只空（sideways_regime）"
                        else:
                            mode_desc = f"{day_plan['phase1']}->{day_plan['phase2']}（{day_plan['type']}）"
                        shape_predictions.append({
                            "day": str(today_d), "predicted_shape": pred["shape"],
                            "confidence": pred["confidence"], "applied_mode": mode_desc, "reason": "",
                        })

        effective_range = None
        if day_setups:
            effective_range = {"start": str(day_setups[0]["today"]), "end": str(day_setups[-1]["today"])}

        regime_kwargs = _regime_kwargs(locals())
        summary_extra: dict = {}
        if day_setups and use_regime:
            config.update({k: v for k, v in regime_kwargs.items()})
            regime_map = build_regime_map(bars_5, all_rth_idx, **_regime_map_kwargs(regime_kwargs))
            trades, signal_log = walk_forward_regime(
                bars_5, day_setups, regime_map,
                lookback=lookback, vwap_trend_lookback=vwap_trend_lookback,
                sma_short_period=sma_short_period, sma_long_period=sma_long_period,
                use_sma_precondition=use_sma_precondition, allow_short=allow_short,
                capital_per_trade=capital_per_trade, primary_symbol=symbol, compound=compound,
                **_regime_walk_kwargs(regime_kwargs),
            )
            bar_log = []
            summary_extra = regime_summary_extras(trades)
        elif day_setups:
            trades, signal_log, bar_log = walk_forward(
                bars_5, day_setups, lookback, vwap_trend_lookback,
                sma_short_period, sma_long_period, use_sma_precondition, allow_short, capital_per_trade,
                symbol, short_symbol, etf_index, etf_stamps, compound, trade_every_signal, debug_day_set,
                close_no_trade_minutes, market_index, market_stamps,
            )
        else:
            trades, signal_log, bar_log = [], [], []

        return {
            "symbol": symbol,
            "config": config,
            "data_range": {"first_day": str(data_first_day), "last_day": str(data_last_day)},
            "effective_range": effective_range,
            "warnings": warnings,
            "n_days": len(day_setups),
            "skipped_no_context": skipped_no_context,
            "trades": trades,
            "summary": {**summarize(trades, capital_per_trade, compound), **summary_extra},
            "signal_log": signal_log,
            "daily_signal_counts": daily_signal_counts(signal_log),
            "daily_trade_stats": daily_trade_stats(trades),
            "daily_report": daily_report(signal_log, trades),
            "bar_log": bar_log,
            "shape_predictions": shape_predictions,
        }

    finally:
        _mctx.__exit__(None, None, None)

# ---------------------------------------------------------------------------
# CLI：直接跑这个文件时打印人类可读的报告
# ---------------------------------------------------------------------------

def _print_daily_report(result: dict) -> None:
    """打印"每天信号数量 + 信号准确度（胜率）"的表格。"""
    rows = result.get("daily_report") or []
    if not rows:
        return
    print("按天信号统计（信号数量 / 开仓笔数 / 胜率 / 当天盈亏）：")
    header = f"   {'日期':<12}{'信号总数':>8}{'实际成交':>8}{'买入':>6}{'卖出':>6}{'开仓笔数':>8}{'胜':>4}{'负':>4}{'胜率':>8}{'当天盈亏':>12}"
    print(header)
    for row in rows:
        win_rate_str = f"{row['win_rate']}%" if row["n_trades"] else "—"
        pnl_str = f"${row['total_pnl']:,.2f}" if row["n_trades"] else "—"
        print(
            f"   {row['date']:<12}{row['signals_total']:>8}{row['signals_taken']:>8}"
            f"{row['buy_signals']:>6}{row['sell_signals']:>6}{row['n_trades']:>8}"
            f"{row['wins']:>4}{row['losses']:>4}{win_rate_str:>8}{pnl_str:>12}"
        )
    print()


def _print_bar_log(result: dict) -> None:
    """打印 debug_days 指定日期的逐根 5min bar 详细数据——用来诊断"某天为什么会/
    不会出现某个信号"。每根打印：时间、OHLC、成交量、一阶导数(f')/二阶导数(f'')
    数值和方向（决定开仓的核心指标）、VWAP 数值和趋势（仅作参考，不参与开仓判断）、
    放量判断、SMA短/长、二阶导数转折（决定平仓的核心指标）、SMA站上再跌破前提，
    以及这一根实际触发了哪些条件、仓位在这一根前后有没有变化。
    """
    rows = result.get("bar_log") or []
    if not rows:
        return
    by_day: dict[str, list[dict]] = {}
    for row in rows:
        by_day.setdefault(row["day"], []).append(row)

    for day in sorted(by_day):
        print(f"===== {result['symbol']} {day} 逐根明细 =====")
        header = (
            f"   {'时间':<7}{'开':>9}{'高':>9}{'低':>9}{'收':>9}{'成交量':>10}"
            f"{'f1':>9}{'f2':>9}{'导数趋势':>8}{'VWAP':>9}{'VWAP趋势':>8}"
            f"{'量':>6}{'转折':>6}"
            f"{'SMA短':>9}{'SMA长':>9}{'空前提':>7}"
            f"{'触发':<12}{'持仓前->后':<12}"
        )
        print(header)
        for row in by_day[day]:
            fired = []
            if row["buy_condition"]:
                fired.append("买入")
            if row["sell_condition"]:
                fired.append("卖出")
            if row["exit_long_condition"]:
                fired.append("多头反转")
            if row["exit_short_condition"]:
                fired.append("空头反转")
            fired_str = ",".join(fired) if fired else "—"
            pos_str = f"{row['position_before'] or '空仓'}->{row['position_after'] or '空仓'}"
            sma_s = f"{row['sma_short']:.3f}" if row["sma_short"] is not None else "—"
            sma_l = f"{row['sma_long']:.3f}" if row["sma_long"] is not None else "—"
            vwap_s = f"{row['vwap']:.3f}" if row["vwap"] is not None else "—"
            f1_s = f"{row['f1']:.4f}" if row["f1"] is not None else "—"
            f2_s = f"{row['f2']:.4f}" if row["f2"] is not None else "—"
            print(
                f"   {row['time']:<7}{row['open']:>9.3f}{row['high']:>9.3f}{row['low']:>9.3f}{row['close']:>9.3f}"
                f"{row['volume']:>10.0f}{f1_s:>9}{f2_s:>9}{row['dtrend']:>8}{vwap_s:>9}{row['vtrend']:>8}"
                f"{row['vol_state']:>6}{row['turning']:>6}"
                f"{sma_s:>9}{sma_l:>9}{str(row['short_precondition']):>7}"
                f"{fired_str:<12}{pos_str:<12}"
            )
        print()


def _print_shape_filter_report(result: dict) -> None:
    """打印形态过滤（use_shape_filter=True）每天的预测详情：预测出的形态、
    置信度、当天实际启用的开单模式（或者为什么没启用/走观望）。
    """
    rows = result.get("shape_predictions") or []
    if not rows:
        return
    print("形态过滤每日预测（predicted_shape / confidence / applied_mode）：")
    for row in rows:
        shape_str = row["predicted_shape"] or "—"
        conf_str = f"{row['confidence']}%" if row["confidence"] is not None else "—"
        note = f"  ({row['reason']})" if row["reason"] else ""
        print(f"   {row['day']:<12}{shape_str:<8}{conf_str:>8}   {row['applied_mode']}{note}")
    print()


def _print_report(result: dict) -> None:
    symbol = result["symbol"]
    cfg = result["config"]
    if not result["data_range"]:
        print(f"===== {symbol} 回测 =====")
        for w in result["warnings"]:
            print(w)
        return

    print(f"===== {symbol} 回测 =====")
    if not cfg["allow_short"]:
        short_desc = "关（只做多）"
    elif cfg["use_sma_precondition"]:
        short_desc = f"开，需先站上再跌破 SMA{cfg['sma_short_period']}/SMA{cfg['sma_long_period']}"
    else:
        short_desc = "开，跟做多对称，不看SMA"
    entry_desc = "每次信号都交易（反向持仓自动反手）" if cfg.get("trade_every_signal") else "只在空仓时进场（原有行为）"
    shape_desc = "，形态过滤开" if cfg.get("use_shape_filter") else ""
    close_desc = f"，收盘前{cfg['close_no_trade_minutes']}分钟不开新仓" if cfg.get("close_no_trade_minutes") else ""
    print(
        f"（止损={cfg['stop_loss_atr_mult']}×日线ATR，做空{short_desc}{shape_desc}{close_desc}，进场方式：{entry_desc}，"
        f"仓位可跨日，${cfg['capital_per_trade']:,.0f}/笔）"
    )
    for w in result["warnings"]:
        print(f"⚠️  {w}")
    if result["effective_range"]:
        print(f"实际参与统计的交易日：{result['effective_range']['start']} ~ {result['effective_range']['end']}（共 {result['n_days']} 天）")
    print()

    _print_daily_report(result)
    _print_shape_filter_report(result)
    _print_bar_log(result)

    trades = result["trades"]
    if not trades:
        print("回测期内没有产生任何交易（进场条件从未触发）。")
        return

    print(f"交易记录（共 {len(trades)} 条）：")
    for t in trades:
        sym = f"[{t['symbol']}] " if t.get("symbol") and t["symbol"] != symbol else ""
        shape_label = t["entry_shape"] or ""
        if t.get("entry_substate") and t["entry_substate"] != shape_label:
            shape_label = f"{shape_label}/{t['entry_substate']}" if shape_label else t["entry_substate"]
        shape_tag = f"[{shape_label}] " if shape_label else ""
        if t["exit_day"] is None:
            print(f"   {sym}{shape_tag}{t['entry_day']}  {t['side']}  {t['entry_time']}@{t['entry_price']:.3f} -> "
                  f"{t['exit_reason']}（无已实现盈亏）")
        else:
            span = "" if t["entry_day"] == t["exit_day"] else f"[{t['entry_day']}->{t['exit_day']}] "
            print(f"   {sym}{shape_tag}{span}{t['side']}  {t['entry_time']}@{t['entry_price']:.3f} -> "
                  f"{t['exit_time']}@{t['exit_price']:.3f}  {t['exit_reason']}  盈亏=${t['pnl']:.2f}")
    print()

    s = result["summary"]
    print("汇总统计：")
    print(f"   已实现交易笔数：{s['n_trades']}（多 {s['n_long']} 笔 / 空 {s['n_short']} 笔；"
          f"止损 {s['stop_exits']} 笔 / 真反转 {s['reversal_exits']} 笔 / 信号反手 {s.get('signal_flip_exits', 0)} 笔；"
          f"其中跨日持仓 {s['overnight']} 笔）")
    print(f"   胜率：{s['win_rate']}%")
    mode = "复利（每笔用当前账户权益算仓位）" if s["compound"] else "不复利（每笔固定按起始本金算仓位）"
    print(f"   总盈亏（{mode}，起始本金 ${s['starting_capital']:,.0f}）：${s['total_pnl']:,.2f}")
    print(f"   平均每笔盈利：${s['avg_win']:,.2f}  平均每笔亏损：${s['avg_loss']:,.2f}")
    print(f"   累计收益率：{s['return_pct']}%  期末权益：${s['final_equity']:,.2f}")
    if s["unrealized"]:
        u = s["unrealized"]
        print(f"   ⚠️  区间结束时仍持仓：{u['side']}  {u['entry_day']} {u['entry_time']}@{u['entry_price']:.3f}（未计入上面统计）")


def parse_date_arg(s: str) -> date:
    """解析命令行传入的日期字符串（YYYY-MM-DD）。"""
    return datetime.strptime(s, "%Y-%m-%d").date()


def _print_shape_prediction(result: dict) -> None:
    """打印 predict_today_shape() 的结果：训练样本规模、历史形态分布、今天的
    特征值、以及6+1种形态各自的预测概率（按概率从高到低排序）。
    """
    print(f"===== {result['symbol']} {result['as_of_day']} 形态预测（多元逻辑回归，仅供参考）=====")
    print(f"训练样本：{result['n_train_days']} 天")
    print("历史形态分布：")
    for name in SHAPE_NAMES:
        cnt = result["class_counts"].get(name, 0)
        print(f"   {name:<8}{cnt:>4} 天")
    print()
    f = result["features"]
    print("今天的特征值：")
    print(f"   隔夜跳空：{f['gap_pct']:+.3f}%")
    print(f"   早盘放量比：{f['premarket_vol_ratio']:.3f}（>1 表示比历史同期更放量）")
    print(f"   当前波动率环境（日线ATR%）：{f['atr_pct']:.3f}%")
    print(f"   昨天收盘相对SMA{SHAPE_SMA_PERIOD}偏离：{f['sma_position_pct']:+.3f}%")
    print(f"   昨天的形态：{f['prev_shape']}")
    print()
    print("预测概率（从高到低）：")
    for row in result["probabilities"]:
        bar = "█" * round(row["prob"] / 4)
        print(f"   {row['shape']:<8}{row['prob']:>6.2f}%  {bar}")
    print()
    print("⚠️  这是纯统计意义上的经验概率估计，不构成任何交易建议，样本量和阈值选取都会实质性影响结果。")


if __name__ == "__main__":
    args = sys.argv[1:]
    sym = SYMBOL
    range_start = None
    range_end = None
    trade_every_signal = TRADE_EVERY_SIGNAL
    debug_days: list[str] = []
    predict_shape = False
    use_shape_filter = False
    close_no_trade_minutes = CLOSE_NO_TRADE_MINUTES

    if "--all-signals" in args:
        trade_every_signal = True
        args = [a for a in args if a != "--all-signals"]

    if "--predict-shape" in args:
        predict_shape = True
        args = [a for a in args if a != "--predict-shape"]

    if "--shape-filter" in args:
        use_shape_filter = True
        args = [a for a in args if a != "--shape-filter"]

    if "--close-no-trade-minutes" in args:
        idx = args.index("--close-no-trade-minutes")
        if idx + 1 >= len(args):
            raise SystemExit("--close-no-trade-minutes 后面需要跟分钟数，如 --close-no-trade-minutes 60")
        close_no_trade_minutes = int(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    # --detail 2026-08-05  或  --detail 2026-08-05,2026-08-06  打印那几天的逐根明细
    if "--detail" in args:
        idx = args.index("--detail")
        if idx + 1 >= len(args):
            raise SystemExit("--detail 后面需要跟日期，如 --detail 2026-08-05")
        debug_days = [d.strip() for d in args[idx + 1].split(",") if d.strip()]
        args = args[:idx] + args[idx + 2:]

    if args:
        sym = args[0].strip().upper()
        args = args[1:]

    if predict_shape:
        _print_shape_prediction(predict_today_shape(sym))
        raise SystemExit(0)

    if len(args) >= 1:
        range_start = parse_date_arg(args[0])
    if len(args) >= 2:
        range_end = parse_date_arg(args[1])

    _print_report(run_backtest(
        sym, range_start, range_end,
        trade_every_signal=trade_every_signal,
        debug_days=debug_days,
        use_shape_filter=use_shape_filter,
        close_no_trade_minutes=close_no_trade_minutes,
    ))