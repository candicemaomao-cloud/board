"""
股票潜力筛选工具 (第二版)
================================

在第一版基础上新增三块：
1. 基本面过滤器：营收/毛利率趋势恶化的股票会被标记，避免把"该跌"误判成"超跌机会"（价值陷阱）
2. 财报日历自动扫描：可以扫描一个大股票池（比如标普500全部），自动找出未来N天内要发财报的公司
3. 内部人交易：近半年高管/董事净买入情况，买入多的加分，卖出多的减分

思路仍然是分层漏斗：
    大股票池 --(财报日历扫描)--> 近期有财报的候选 --(量化因子打分 + 基本面过滤)--> 排名候选池
    最终"买不买、方向是多是空"，仍然需要你结合新闻面自己判断，这个工具不做交易建议。

运行方式：
    python stock_screener.py

依赖：
    pip install yfinance pandas numpy
"""

import json
import math
import os
import time
import warnings
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

# =============================================================================
# 配置区
# =============================================================================

# ---- 股票池模式 ----
# "watchlist"：只用下面 WATCHLIST 里手动维护的股票
# "sp500"：自动抓取标普500全部成分股作为财报扫描的股票池（范围更广，但跑起来更慢）
UNIVERSE_MODE = "sp500"

WATCHLIST = [
    "AAPL", "TSLA", "NVDA", "AMD", "PLTR", "SOFI", "RIVN", "LCID",
    "COIN", "MARA", "RIOT", "UPST", "AFRM", "CVNA", "ROKU", "SNAP",
    "DKNG", "RBLX", "U", "PATH", "IONQ", "SMCI", "ARM", "CRWD",
]

# ---- 财报日历自动扫描 ----
AUTO_EARNINGS_SCAN = True          # 是否自动扫描"近期有财报"的股票，生成独立候选名单
EARNINGS_LOOKAHEAD_DAYS = 30       # 未来多少天内的财报算"临近"
# 财报候选是否自动并入下面的打分环节（如果 False，只单独生成一份"本月财报候选"名单，不影响主排名）
MERGE_EARNINGS_CANDIDATES_INTO_SCORING = True

# 打分环节是否要把 WATCHLIST 也混进来一起打分。
# watchlist 模式下这个开关没有实际影响（本来打分池就是 WATCHLIST 本身）。
# sp500 批次模式下，建议设成 False —— 否则每次打分结果都会被 WATCHLIST 这几十只固定股票"稀释"，
# 扫描出来的新候选评分不够高就会被淹没在 WATCHLIST 里，感觉不出批次扫描起了作用。
INCLUDE_WATCHLIST_IN_SCORING = True

# ---- 分批扫描（针对 sp500 模式）----
# RUN_ALL_IN_ONE_GO = True（默认）：一次运行内部自动把全部500多只跑完，不需要你手动跑多次。
#   跑的时间会比较长（500多只股票，逐个请求，大概几分钟到十几分钟不等，取决于网络和限流情况），
#   但只需要跑一次命令，最后所有结果汇总到一个Excel文件里。
# RUN_ALL_IN_ONE_GO = False：老的"每次只跑一批，跑多次"模式，如果你发现一次性跑500只总是卡住/
#   被限流失败，可以切换回这个模式，分成多次运行，每次只处理 BATCH_SIZE 只，进度自动接续。
RUN_ALL_IN_ONE_GO = True
BATCH_SIZE = 100
BATCH_STATE_FILE = "sp500_batch_state.json"

# ---- 基本面过滤器 ----
ENABLE_FUNDAMENTAL_FILTER = True
# True: 基本面明显恶化的股票直接从主排名剔除，单独列在"基本面过滤掉"名单里
# False: 只降低这些股票的评分，不直接剔除
HARD_EXCLUDE_DETERIORATING = True

# ---- 打分权重（数值越大，该因子对综合评分影响越大，加起来不用等于1，会自动归一化）----
WEIGHTS = {
    "oversold_score": 1.0,        # 超跌程度
    "short_squeeze_score": 1.2,   # 空头挤压潜力
    "vol_squeeze_score": 1.0,     # 波动率压缩
    "earnings_proximity_score": 0.8,  # 财报临近程度
    "liquidity_score": 0.6,       # 流动性
    "fundamental_score": 0.9,     # 基本面健康度（新增）
    "insider_score": 0.7,         # 内部人净买入（新增）
}

REQUEST_DELAY = 0.3  # 每次请求间隔秒数，避免限流


# =============================================================================
# 数据结构
# =============================================================================

@dataclass
class StockFactors:
    ticker: str
    price: float = None
    high_52w: float = None
    low_52w: float = None
    pct_off_high: float = None
    short_pct_float: float = None
    short_ratio_days: float = None
    avg_volume_10d: float = None
    hist_vol_20d: float = None
    hist_vol_120d: float = None
    vol_compression_ratio: float = None
    market_cap: float = None
    next_earnings_date: datetime = None
    days_to_earnings: int = None

    # 基本面
    revenue_growth_yoy: float = None      # 最近一期营收同比增长 (%)
    revenue_growth_trend: str = None      # "改善" / "恶化" / "持平" / 数据不足
    gross_margin_latest: float = None     # 最新毛利率 (%)
    gross_margin_trend: str = None
    fundamentals_deteriorating: bool = False  # 营收和毛利率同时恶化 -> 硬过滤标记

    # 内部人交易
    insider_net_value_6m: float = None    # 近6个月净买入金额（买入-卖出，美元），负数代表净卖出

    error: str = None

    # 子分数 (0-100)
    oversold_score: float = 0.0
    short_squeeze_score: float = 0.0
    vol_squeeze_score: float = 0.0
    earnings_proximity_score: float = 0.0
    liquidity_score: float = 0.0
    fundamental_score: float = 0.0
    insider_score: float = 0.0
    composite_score: float = 0.0


# =============================================================================
# 股票池获取
# =============================================================================

# =============================================================================
# 股票池获取
# =============================================================================

# 标普500成分股列表（静态写死，截至写入时的最新成分股，2026年8月）。
# 不再实时抓取维基百科，避免403/网络问题。
# 缺点：标普500成分股会有增减调整（比如某公司被收购、被踢出指数），这份列表会慢慢过时。
# 如果发现某只新纳入的股票不在列表里，或者列表里有已经被移出的股票，
# 可以去 https://en.wikipedia.org/wiki/List_of_S%26P_500_companies 手动核对更新这个列表。
SP500_TICKERS = [
    "MMM", "AOS", "ABT", "ABBV", "ACN", "ADBE", "AMD", "AES",
    "AFL", "A", "APD", "ABNB", "AKAM", "ALB", "ARE", "ALGN",
    "ALLE", "LNT", "ALL", "GOOGL", "GOOG", "MO", "AMZN", "AMCR",
    "AEE", "AEP", "AXP", "AIG", "AMT", "AWK", "AMP", "AME",
    "AMGN", "APH", "ADI", "AON", "APA", "APO", "AAPL", "AMAT",
    "APP", "APTV", "ACGL", "ADM", "ARES", "ANET", "AJG", "AIZ",
    "T", "ATO", "ADSK", "ADP", "AZO", "AVB", "AVY", "AXON",
    "BKR", "BALL", "BAC", "BAX", "BDX", "BRK-B", "BBY", "TECH",
    "BIIB", "BLK", "BX", "XYZ", "BNY", "BA", "BKNG", "BSX",
    "BMY", "AVGO", "BR", "BRO", "BF-B", "BLDR", "BG", "BXP",
    "CHRW", "CDNS", "CPT", "CPB", "COF", "CAH", "CCL", "CARR",
    "CVNA", "CASY", "CAT", "CBOE", "CBRE", "CDW", "COR", "CNC",
    "CNP", "CF", "CRL", "SCHW", "CHTR", "CVX", "CMG", "CB",
    "CHD", "CIEN", "CI", "CINF", "CTAS", "CSCO", "C", "CFG",
    "CLX", "CME", "CMS", "KO", "CTSH", "COHR", "COIN", "CL",
    "CMCSA", "FIX", "CAG", "COP", "ED", "STZ", "CEG", "COO",
    "CPRT", "GLW", "CPAY", "CTVA", "CSGP", "COST", "CRH", "CRWD",
    "CCI", "CSX", "CMI", "CVS", "DHR", "DRI", "DDOG", "DVA",
    "DECK", "DE", "DELL", "DAL", "DVN", "DXCM", "FANG", "DLR",
    "DG", "DLTR", "D", "DPZ", "DASH", "DOV", "DOW", "DHI",
    "DTE", "DUK", "DD", "ETN", "EBAY", "SATS", "ECL", "EIX",
    "EW", "EA", "ELV", "EME", "EMR", "ETR", "EOG", "EPAM",
    "EQT", "EFX", "EQIX", "EQR", "ERIE", "ESS", "EL", "EG",
    "EVRG", "ES", "EXC", "EXE", "EXPE", "EXPD", "EXR", "XOM",
    "FFIV", "FDS", "FICO", "FAST", "FRT", "FDX", "FIS", "FITB",
    "FSLR", "FE", "FISV", "F", "FTNT", "FTV", "FOXA", "FOX",
    "BEN", "FCX", "GRMN", "IT", "GE", "GEHC", "GEV", "GEN",
    "GNRC", "GD", "GIS", "GM", "GPC", "GILD", "GPN", "GL",
    "GDDY", "GS", "HAL", "HIG", "HAS", "HCA", "DOC", "HSIC",
    "HSY", "HPE", "HLT", "HD", "HON", "HRL", "HST", "HWM",
    "HPQ", "HUBB", "HUM", "HBAN", "HII", "IBM", "IEX", "IDXX",
    "ITW", "INCY", "IR", "PODD", "INTC", "IBKR", "ICE", "IFF",
    "IP", "INTU", "ISRG", "IVZ", "INVH", "IQV", "IRM", "JBHT",
    "JBL", "JKHY", "J", "JNJ", "JCI", "JPM", "KVUE", "KDP",
    "KEY", "KEYS", "KMB", "KIM", "KMI", "KKR", "KLAC", "KHC",
    "KR", "LHX", "LH", "LRCX", "LVS", "LDOS", "LEN", "LII",
    "LLY", "LIN", "LYV", "LMT", "L", "LOW", "LULU", "LITE",
    "LYB", "MTB", "MPC", "MAR", "MRSH", "MLM", "MAS", "MA",
    "MKC", "MCD", "MCK", "MDT", "MRK", "META", "MET", "MTD",
    "MGM", "MCHP", "MU", "MSFT", "MAA", "MRNA", "TAP", "MDLZ",
    "MPWR", "MNST", "MCO", "MS", "MOS", "MSI", "MSCI", "NDAQ",
    "NTAP", "NFLX", "NEM", "NWSA", "NWS", "NEE", "NKE", "NI",
    "NDSN", "NSC", "NTRS", "NOC", "NCLH", "NRG", "NUE", "NVDA",
    "NVR", "NXPI", "ORLY", "OXY", "ODFL", "OMC", "ON", "OKE",
    "ORCL", "OTIS", "PCAR", "PKG", "PLTR", "PANW", "PSKY", "PH",
    "PAYX", "PYPL", "PNR", "PEP", "PFE", "PCG", "PM", "PSX",
    "PNW", "PNC", "POOL", "PPG", "PPL", "PFG", "PG", "PGR",
    "PLD", "PRU", "PEG", "PTC", "PSA", "PHM", "PWR", "QCOM",
    "DGX", "Q", "RL", "RJF", "RTX", "O", "REG", "REGN",
    "RF", "RSG", "RMD", "RVTY", "HOOD", "ROK", "ROL", "ROP",
    "ROST", "RCL", "SPGI", "CRM", "SNDK", "SBAC", "SLB", "STX",
    "SRE", "NOW", "SHW", "SPG", "SWKS", "SJM", "SW", "SNA",
    "SOLV", "SO", "LUV", "SWK", "SBUX", "STT", "STLD", "STE",
    "SYK", "SMCI", "SYF", "SNPS", "SYY", "TMUS", "TROW", "TTWO",
    "TPR", "TRGP", "TGT", "TEL", "TDY", "TER", "TSLA", "TXN",
    "TPL", "TXT", "TMO", "TJX", "TKO", "TTD", "TSCO", "TT",
    "TDG", "TRV", "TRMB", "TFC", "TYL", "TSN", "USB", "UBER",
    "UDR", "ULTA", "UNP", "UAL", "UPS", "URI", "UNH", "UHS",
    "VLO", "VEEV", "VTR", "VLTO", "VRSN", "VRSK", "VZ", "VRTX",
    "VRT", "VTRS", "VICI", "V", "VST", "VMC", "WRB", "GWW",
    "WAB", "WMT", "DIS", "WBD", "WM", "WAT", "WEC", "WFC",
    "WELL", "WST", "WDC", "WY", "WSM", "WMB", "WTW", "WDAY",
    "WYNN", "XEL", "XYL", "YUM", "ZBRA", "ZBH", "ZTS",
]


def build_universe() -> list[str]:
    if UNIVERSE_MODE == "sp500":
        return SP500_TICKERS
    return WATCHLIST


# =============================================================================
# 分批扫描进度管理（针对大股票池，比如 sp500 模式）
# =============================================================================

def _load_batch_index(state_file: str) -> int:
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("next_batch_index", 0))
        except Exception:
            return 0
    return 0


def _save_batch_index(state_file: str, idx: int, total_batches: int) -> None:
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump({
            "next_batch_index": idx,
            "total_batches": total_batches,
            "updated_at": datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)


def get_next_batch(tickers: list[str], batch_size: int, state_file: str) -> tuple[list[str], int, int]:
    """
    返回 (本次要跑的股票列表, 这是第几批(从0开始), 总共多少批)
    自动把进度推进到下一批并存盘；跑完最后一批后会自动从头开始循环。
    """
    total_batches = max(1, math.ceil(len(tickers) / batch_size))
    idx = _load_batch_index(state_file) % total_batches
    start = idx * batch_size
    end = start + batch_size
    batch = tickers[start:end]
    next_idx = (idx + 1) % total_batches
    _save_batch_index(state_file, next_idx, total_batches)
    return batch, idx, total_batches


# =============================================================================
# 财报日历扫描
# =============================================================================

def fetch_earnings_date(ticker: str) -> tuple[datetime, int]:
    """只拉财报日期，比拉全量因子数据轻量，用于大范围扫描"""
    try:
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        earnings_date = None
        if isinstance(cal, dict) and "Earnings Date" in cal:
            dates = cal["Earnings Date"]
            if isinstance(dates, list) and len(dates) > 0:
                earnings_date = dates[0]
        elif hasattr(cal, "empty") and not cal.empty:
            earnings_date = cal.loc["Earnings Date"][0]

        if earnings_date is None:
            return None, None
        if isinstance(earnings_date, str):
            earnings_date = pd.to_datetime(earnings_date)
        delta = (pd.Timestamp(earnings_date) - pd.Timestamp.now()).days
        return earnings_date, delta
    except Exception:
        return None, None


def scan_earnings_calendar(universe: list[str], lookahead_days: int) -> pd.DataFrame:
    """扫描股票池，找出未来 lookahead_days 天内有财报的公司"""
    print(f"扫描财报日历（未来 {lookahead_days} 天内），股票池共 {len(universe)} 只...")
    rows = []
    total = len(universe)
    for i, t in enumerate(universe, 1):
        print(f"  [{i}/{total}] 检查 {t} ...", end="\r")
        edate, days = fetch_earnings_date(t)
        if edate is not None and days is not None and 0 <= days <= lookahead_days:
            rows.append({"股票代码": t, "财报日期": edate, "距今天数": days})
        time.sleep(REQUEST_DELAY)
    print(" " * 60, end="\r")
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("距今天数")
    return df


# =============================================================================
# 单只股票量化因子 + 基本面 + 内部人交易
# =============================================================================

def _fetch_fundamentals(tk: "yf.Ticker", factors: StockFactors) -> None:
    """
    营收增长：优先用最近一个季度 vs 去年同期(季度同比)，比年度数据更及时。
    判断标准看的是"增长的绝对水平"，不是"增长率有没有比上次低"——
    比如增长率从18%降到15%，依然是健康增长，不该被当成恶化信号；
    只有增长率真的转负（营收同比在萎缩）才算数。

    毛利率：看最新一期毛利率 相对 过去几期平均值 的变化。

    硬过滤条件（fundamentals_deteriorating）要求"营收同比实质性下滑"
    并且"毛利率同时在下降"，两者都成立才算真正意义上的基本面恶化，
    避免误伤"增速放缓但依然在增长"的健康公司（比如Roku这种情况）。
    """
    try:
        revenue_yoy = None
        margins_recent_to_old = []  # 从近到远的毛利率序列 (%)

        # ---- 优先用季度数据算最新一期的同比增长（更及时） ----
        try:
            q_fin = tk.quarterly_income_stmt
        except Exception:
            q_fin = None
        if q_fin is None or q_fin.empty:
            try:
                q_fin = tk.quarterly_financials
            except Exception:
                q_fin = None

        if q_fin is not None and not q_fin.empty and "Total Revenue" in q_fin.index:
            rev_row = q_fin.loc["Total Revenue"].dropna()
            rev_row = rev_row.sort_index(ascending=False)  # 从近到远
            if len(rev_row) >= 5:
                latest_q = rev_row.iloc[0]
                year_ago_q = rev_row.iloc[4]  # 4个季度前 = 去年同期
                if year_ago_q:
                    revenue_yoy = float((latest_q / year_ago_q - 1) * 100)

            if "Gross Profit" in q_fin.index:
                gp_row = q_fin.loc["Gross Profit"].dropna().sort_index(ascending=False)
                for i in range(min(4, len(gp_row), len(rev_row))):
                    r = rev_row.iloc[i]
                    g = gp_row.iloc[i]
                    if r:
                        margins_recent_to_old.append(float(g / r * 100))

        # ---- 季度数据不够就退回年度数据 ----
        if revenue_yoy is None or not margins_recent_to_old:
            fin = tk.financials
            if fin is not None and not fin.empty and "Total Revenue" in fin.index:
                rev_row_a = fin.loc["Total Revenue"].dropna().sort_index(ascending=False)
                if revenue_yoy is None and len(rev_row_a) >= 2 and rev_row_a.iloc[1]:
                    revenue_yoy = float((rev_row_a.iloc[0] / rev_row_a.iloc[1] - 1) * 100)
                if not margins_recent_to_old and "Gross Profit" in fin.index:
                    gp_row_a = fin.loc["Gross Profit"].dropna().sort_index(ascending=False)
                    for i in range(min(3, len(gp_row_a), len(rev_row_a))):
                        r = rev_row_a.iloc[i]
                        g = gp_row_a.iloc[i]
                        if r:
                            margins_recent_to_old.append(float(g / r * 100))

        factors.revenue_growth_yoy = revenue_yoy

        # ---- 营收健康度判断：看绝对增长水平，不是"是否比上次慢" ----
        if revenue_yoy is not None:
            if revenue_yoy > 5:
                factors.revenue_growth_trend = "健康增长"
            elif revenue_yoy > -5:
                factors.revenue_growth_trend = "持平"
            else:
                factors.revenue_growth_trend = "萎缩"

        # ---- 毛利率趋势：最新一期 vs 前面几期均值 ----
        if margins_recent_to_old:
            factors.gross_margin_latest = margins_recent_to_old[0]
            if len(margins_recent_to_old) >= 2:
                baseline = float(np.mean(margins_recent_to_old[1:]))
                latest = margins_recent_to_old[0]
                if latest > baseline + 1:
                    factors.gross_margin_trend = "改善"
                elif latest < baseline - 1:
                    factors.gross_margin_trend = "恶化"
                else:
                    factors.gross_margin_trend = "持平"

        # 硬过滤条件：营收同比"真的在萎缩" 并且 毛利率同时"恶化"，两者都成立才算数
        if factors.revenue_growth_trend == "萎缩" and factors.gross_margin_trend == "恶化":
            factors.fundamentals_deteriorating = True

    except Exception:
        pass  # 基本面数据拿不到就跳过，不影响其他因子


def _fetch_insider_transactions(tk: "yf.Ticker", factors: StockFactors) -> None:
    """拉取近半年内部人交易净买入金额"""
    try:
        insider_df = tk.insider_transactions
        if insider_df is None or insider_df.empty:
            return

        # yfinance 字段名可能是 'Start Date' / 'Value' / 'Transaction' / 'Shares' 等，做容错处理
        date_col = None
        for candidate in ["Start Date", "Date"]:
            if candidate in insider_df.columns:
                date_col = candidate
                break
        value_col = "Value" if "Value" in insider_df.columns else None
        text_col = None
        for candidate in ["Transaction", "Text"]:
            if candidate in insider_df.columns:
                text_col = candidate
                break

        if date_col is None or value_col is None or text_col is None:
            return

        df = insider_df.copy()
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=182)
        recent = df[df[date_col] >= cutoff]
        if recent.empty:
            return

        net_value = 0.0
        for _, row in recent.iterrows():
            val = row.get(value_col)
            text = str(row.get(text_col, "")).lower()
            if pd.isna(val):
                continue
            val = float(val)
            if "sale" in text or "sell" in text:
                net_value -= val
            elif "buy" in text or "purchase" in text:
                net_value += val
        factors.insider_net_value_6m = net_value
    except Exception:
        pass


def fetch_stock_factors(ticker: str) -> StockFactors:
    factors = StockFactors(ticker=ticker)
    try:
        tk = yf.Ticker(ticker)
        info = tk.info

        hist = tk.history(period="1y")
        if hist.empty or len(hist) < 30:
            factors.error = "历史数据不足"
            return factors

        factors.price = float(hist["Close"].iloc[-1])
        factors.high_52w = float(hist["Close"].max())
        factors.low_52w = float(hist["Close"].min())
        factors.pct_off_high = (factors.price / factors.high_52w - 1) * 100
        factors.avg_volume_10d = float(hist["Volume"].tail(10).mean())

        returns = hist["Close"].pct_change().dropna()
        if len(returns) >= 20:
            factors.hist_vol_20d = float(returns.tail(20).std() * np.sqrt(252) * 100)
        if len(returns) >= 120:
            factors.hist_vol_120d = float(returns.tail(120).std() * np.sqrt(252) * 100)
        elif len(returns) >= 60:
            factors.hist_vol_120d = float(returns.std() * np.sqrt(252) * 100)

        if factors.hist_vol_20d and factors.hist_vol_120d and factors.hist_vol_120d > 0:
            factors.vol_compression_ratio = factors.hist_vol_20d / factors.hist_vol_120d

        short_pct = info.get("shortPercentOfFloat")
        if short_pct is not None:
            factors.short_pct_float = float(short_pct) * 100
        factors.short_ratio_days = info.get("shortRatio")
        factors.market_cap = info.get("marketCap")

        edate, days = fetch_earnings_date(ticker)
        factors.next_earnings_date = edate
        factors.days_to_earnings = days

        # 新增：基本面 + 内部人交易
        _fetch_fundamentals(tk, factors)
        _fetch_insider_transactions(tk, factors)

    except Exception as e:
        factors.error = str(e)

    return factors


def fetch_all(tickers: list[str]) -> list[StockFactors]:
    results = []
    total = len(tickers)
    for i, t in enumerate(tickers, 1):
        print(f"  [{i}/{total}] 拉取 {t} ...", end="\r")
        results.append(fetch_stock_factors(t))
        time.sleep(REQUEST_DELAY)
    print(" " * 50, end="\r")
    return results


# =============================================================================
# 打分逻辑
# =============================================================================

def _normalize(values: list[float]) -> list[float]:
    valid = [v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]
    if not valid:
        return [0.0] * len(values)
    lo, hi = min(valid), max(valid)
    if hi == lo:
        return [50.0 if v is not None else 0.0 for v in values]
    return [
        0.0 if v is None or (isinstance(v, float) and np.isnan(v)) else (v - lo) / (hi - lo) * 100
        for v in values
    ]


def score_all(factors_list: list[StockFactors]) -> tuple[list[StockFactors], list[StockFactors]]:
    """
    返回 (排名候选, 被基本面硬过滤掉的名单)
    """
    candidates = [f for f in factors_list if f.error is None]

    excluded = []
    if ENABLE_FUNDAMENTAL_FILTER and HARD_EXCLUDE_DETERIORATING:
        excluded = [f for f in candidates if f.fundamentals_deteriorating]
        candidates = [f for f in candidates if not f.fundamentals_deteriorating]

    if not candidates:
        return candidates, excluded

    oversold_raw = [abs(f.pct_off_high) if f.pct_off_high is not None else None for f in candidates]
    oversold_scores = _normalize(oversold_raw)

    short_pct_scores = _normalize([f.short_pct_float for f in candidates])
    short_ratio_scores = _normalize([f.short_ratio_days for f in candidates])
    squeeze_scores = [(a + b) / 2 for a, b in zip(short_pct_scores, short_ratio_scores)]

    compression_raw = [
        (1 / f.vol_compression_ratio) if f.vol_compression_ratio and f.vol_compression_ratio > 0 else None
        for f in candidates
    ]
    compression_scores = _normalize(compression_raw)

    earnings_raw = []
    for f in candidates:
        d = f.days_to_earnings
        if d is None or d < 0 or d > EARNINGS_LOOKAHEAD_DAYS:
            earnings_raw.append(0.0)
        else:
            earnings_raw.append(EARNINGS_LOOKAHEAD_DAYS - d)
    earnings_scores = _normalize(earnings_raw)

    liquidity_scores = _normalize([f.avg_volume_10d for f in candidates])

    # 基本面健康度评分（软性评分，即使没开硬过滤，趋势差的也会拉低分数）
    fundamental_raw = []
    for f in candidates:
        if f.revenue_growth_trend is None and f.gross_margin_trend is None:
            fundamental_raw.append(None)  # 数据不足，不参与排名影响（给中性分）
            continue
        score = 50.0  # 基线
        if f.revenue_growth_trend == "健康增长":
            score += 25
        elif f.revenue_growth_trend == "萎缩":
            score -= 25
        # "持平"不加不减
        if f.gross_margin_trend == "改善":
            score += 25
        elif f.gross_margin_trend == "恶化":
            score -= 25
        fundamental_raw.append(max(0.0, min(100.0, score)))
    # 直接用0-100，不做min-max归一化（因为这个本身就是有意义的绝对分数），缺失的给50中性分
    fundamental_scores = [v if v is not None else 50.0 for v in fundamental_raw]

    # 内部人净买入评分
    insider_scores = _normalize([f.insider_net_value_6m for f in candidates])

    total_weight = sum(WEIGHTS.values())
    for i, f in enumerate(candidates):
        f.oversold_score = oversold_scores[i]
        f.short_squeeze_score = squeeze_scores[i]
        f.vol_squeeze_score = compression_scores[i]
        f.earnings_proximity_score = earnings_scores[i]
        f.liquidity_score = liquidity_scores[i]
        f.fundamental_score = fundamental_scores[i]
        f.insider_score = insider_scores[i]

        f.composite_score = (
            f.oversold_score * WEIGHTS["oversold_score"]
            + f.short_squeeze_score * WEIGHTS["short_squeeze_score"]
            + f.vol_squeeze_score * WEIGHTS["vol_squeeze_score"]
            + f.earnings_proximity_score * WEIGHTS["earnings_proximity_score"]
            + f.liquidity_score * WEIGHTS["liquidity_score"]
            + f.fundamental_score * WEIGHTS["fundamental_score"]
            + f.insider_score * WEIGHTS["insider_score"]
        ) / total_weight

    return candidates, excluded


# =============================================================================
# 输出
# =============================================================================

def to_dataframe(factors_list: list[StockFactors]) -> pd.DataFrame:
    rows = []
    for f in factors_list:
        rows.append({
            "股票代码": f.ticker,
            "现价": f.price,
            "距52周高点(%)": round(f.pct_off_high, 1) if f.pct_off_high is not None else None,
            "空头占流通股(%)": round(f.short_pct_float, 1) if f.short_pct_float is not None else None,
            "空头回补天数": f.short_ratio_days,
            "波动压缩比(20d/120d)": round(f.vol_compression_ratio, 2) if f.vol_compression_ratio is not None else None,
            "距财报天数": f.days_to_earnings,
            "营收同比增长(%)": round(f.revenue_growth_yoy, 1) if f.revenue_growth_yoy is not None else None,
            "营收趋势": f.revenue_growth_trend,
            "毛利率趋势": f.gross_margin_trend,
            "内部人净买入(6个月,美元)": round(f.insider_net_value_6m) if f.insider_net_value_6m is not None else None,
            "市值(亿美元)": round(f.market_cap / 1e8, 1) if f.market_cap else None,
            "超跌得分": round(f.oversold_score, 1),
            "空头挤压得分": round(f.short_squeeze_score, 1),
            "波动压缩得分": round(f.vol_squeeze_score, 1),
            "财报临近得分": round(f.earnings_proximity_score, 1),
            "流动性得分": round(f.liquidity_score, 1),
            "基本面得分": round(f.fundamental_score, 1),
            "内部人得分": round(f.insider_score, 1),
            "综合评分": round(f.composite_score, 1),
            "备注": f.error or "",
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("综合评分", ascending=False, na_position="last")
    return df


def main():
    universe = build_universe()

    scoring_tickers = list(WATCHLIST) if INCLUDE_WATCHLIST_IN_SCORING else []
    earnings_df = pd.DataFrame()

    # 1. 财报日历自动扫描
    if AUTO_EARNINGS_SCAN:
        if UNIVERSE_MODE == "sp500" and RUN_ALL_IN_ONE_GO:
            # 一次运行内部把全部股票池跑完，不需要手动跑多次
            print(f"一次性扫描模式：股票池共 {len(universe)} 只，本次运行会跑完全部，请耐心等待...\n")
            earnings_df = scan_earnings_calendar(universe, EARNINGS_LOOKAHEAD_DAYS)
            print(f"财报日历扫描完成：未来{EARNINGS_LOOKAHEAD_DAYS}天内有财报的公司共 {len(earnings_df)} 只\n")
            if not earnings_df.empty:
                print(earnings_df.head(20).to_string(index=False))
                print()

        elif UNIVERSE_MODE == "sp500" and not RUN_ALL_IN_ONE_GO:
            # 老的分批模式：每次运行只处理一批，需要跑多次，进度自动接续
            batch, batch_idx, total_batches = get_next_batch(universe, BATCH_SIZE, BATCH_STATE_FILE)
            print(f"分批模式：本次跑第 {batch_idx + 1}/{total_batches} 批，共 {len(batch)} 只股票")
            print(f"（进度已保存到 {BATCH_STATE_FILE}，下次运行会自动接着跑下一批）\n")
            new_earnings_df = scan_earnings_calendar(batch, EARNINGS_LOOKAHEAD_DAYS)

            existing_path = "monthly_earnings_candidates.csv"
            if os.path.exists(existing_path):
                try:
                    old_df = pd.read_csv(existing_path)
                except Exception:
                    old_df = pd.DataFrame()
                earnings_df = pd.concat([old_df, new_earnings_df], ignore_index=True)
                if not earnings_df.empty:
                    earnings_df = earnings_df.drop_duplicates(subset="股票代码", keep="last")
                    earnings_df = earnings_df.sort_values("距今天数")
            else:
                earnings_df = new_earnings_df
            earnings_df.to_csv(existing_path, index=False, encoding="utf-8-sig")
            print(f"本批新扫到 {len(new_earnings_df)} 只；累计候选（含之前批次）共 {len(earnings_df)} 只\n")

        else:
            # watchlist 模式股票数量不多，一次跑完
            earnings_df = scan_earnings_calendar(universe, EARNINGS_LOOKAHEAD_DAYS)
            print(f"\n财报日历扫描完成：未来{EARNINGS_LOOKAHEAD_DAYS}天内有财报的公司共 {len(earnings_df)} 只\n")
            if not earnings_df.empty:
                print(earnings_df.head(20).to_string(index=False))
                print()

        if MERGE_EARNINGS_CANDIDATES_INTO_SCORING and not earnings_df.empty:
            merged = set(scoring_tickers) | set(earnings_df["股票代码"].tolist())
            scoring_tickers = sorted(merged)

    # 2. 对最终股票池拉取全部量化因子 + 基本面 + 内部人数据
    if not scoring_tickers:
        print("\n打分股票池为空（INCLUDE_WATCHLIST_IN_SCORING=False 且本次没有扫到符合条件的财报候选），"
              "本次不生成排名结果。")
        return

    print(f"开始筛选打分，候选股票池共 {len(scoring_tickers)} 只...")
    factors_list = fetch_all(scoring_tickers)

    ok_count = sum(1 for f in factors_list if f.error is None)
    fail_count = len(factors_list) - ok_count
    print(f"数据拉取完成：成功 {ok_count} 只，失败 {fail_count} 只")

    # 3. 打分（含基本面硬过滤）
    candidates, excluded = score_all(factors_list)
    df = to_dataframe(candidates)
    excluded_df = to_dataframe(excluded) if excluded else pd.DataFrame()

    # 4. 所有结果汇总写进同一个Excel文件的不同工作表，不再是分散的多个CSV
    output_path = "screener_results.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="综合排名", index=False)
        if not earnings_df.empty:
            earnings_df.to_excel(writer, sheet_name="本月财报候选", index=False)
        if not excluded_df.empty:
            excluded_df.to_excel(writer, sheet_name="基本面过滤剔除", index=False)

    print(f"\n所有结果已汇总保存到 {output_path}（Excel文件，包含多个工作表：综合排名 / 本月财报候选 / 基本面过滤剔除）")

    print("\n" + "=" * 110)
    print("综合评分 Top 15 候选:")
    print("=" * 110)
    display_cols = ["股票代码", "现价", "距52周高点(%)", "空头占流通股(%)",
                     "波动压缩比(20d/120d)", "距财报天数", "营收趋势", "毛利率趋势", "综合评分"]
    if not df.empty:
        print(df[display_cols].head(15).to_string(index=False))
    else:
        print("(无有效数据)")

    print("\n提醒：")
    print("- 这份名单只是结构性筛选结果，不代表'该买'，还需要结合新闻面自己判断方向。")
    print("- 基本面过滤只看营收和毛利率趋势，比较粗糙，仅用于排除明显在恶化的公司，不是完整基本面分析。")
    print("- 内部人交易数据 yfinance 有时会缺失或滞后，建议对评分靠前的标的去 SEC Form 4 原始披露核实。")
    print("- 权重、股票池、扫描窗口都在脚本顶部配置区，可以按自己的偏好调整后重新运行。")
    if UNIVERSE_MODE == "sp500" and RUN_ALL_IN_ONE_GO:
        print("- 本次是一次性跑完全部股票池；如果觉得太慢/经常被限流失败，"
              "可以把 RUN_ALL_IN_ONE_GO 改成 False，切换回分批多次运行模式。")


if __name__ == "__main__":
    main()