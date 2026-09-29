"""
期权量化分析工具箱 (Options Quant Toolkit)
============================================
实现内容：
  1. Black-Scholes 定价模型 + Brent 法反推隐含波动率 (IV)
  2. Greeks 计算：Delta / Gamma / Theta / Vega
  3. 隐含振幅 (Implied Move) / 今日预期高低价区间 (含置信度可调)
  4. Max Pain 最大痛点模型 —— 做市商总浮亏最小的价格点
  5. Net Gamma Exposure (GEX) 估算 —— 用于判断做市商对冲方向
  6. bid/ask 中间价定价（比 lastPrice 更贴近真实成交环境）
  7. 波动率微笑扫描 (Volatility Smile) —— 对整条期权链批量反推 IV
  8. 三合一可视化：波动率微笑 / Max Pain 曲线 / Gamma 分布图

依赖：numpy, scipy, matplotlib, yfinance (仅用于抓取真实期权链数据做演示，可选)

⚠️ 声明：本脚本用于研究/教育目的，输出的是基于期权持仓推导出的统计量，
不构成任何投资建议。期权市场存在做市商对冲滞后、数据延迟、流动性差异等
因素，模型结果仅供参考，实际交易盈亏自负。
"""

from __future__ import annotations
import math
import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq
from dataclasses import dataclass
from typing import Literal, Optional


# ----------------------------------------------------------------------
# 1. Black-Scholes 定价 & Greeks
# ----------------------------------------------------------------------

@dataclass
class BSInputs:
    S: float          # 正股现价
    K: float          # 行权价
    T: float          # 到期时间（年化，例如 3天/365）
    r: float          # 无风险利率（年化，例如 0.05）
    sigma: float      # 波动率（年化）
    option_type: Literal["call", "put"] = "call"


def _d1_d2(inp: BSInputs):
    S, K, T, r, sigma = inp.S, inp.K, inp.T, inp.r, inp.sigma
    if T <= 0 or sigma <= 0:
        raise ValueError("T 和 sigma 必须为正数")
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def bs_price(inp: BSInputs) -> float:
    """Black-Scholes 理论期权价格"""
    d1, d2 = _d1_d2(inp)
    S, K, T, r = inp.S, inp.K, inp.T, inp.r
    if inp.option_type == "call":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(inp: BSInputs) -> dict:
    """返回 Delta / Gamma / Vega / Theta（年化 Theta 已换算为每日）"""
    d1, d2 = _d1_d2(inp)
    S, K, T, r, sigma = inp.S, inp.K, inp.T, inp.r, inp.sigma
    pdf_d1 = norm.pdf(d1)

    if inp.option_type == "call":
        delta = norm.cdf(d1)
        theta = (
            -S * pdf_d1 * sigma / (2 * math.sqrt(T))
            - r * K * math.exp(-r * T) * norm.cdf(d2)
        ) / 365
    else:
        delta = norm.cdf(d1) - 1
        theta = (
            -S * pdf_d1 * sigma / (2 * math.sqrt(T))
            + r * K * math.exp(-r * T) * norm.cdf(-d2)
        ) / 365

    gamma = pdf_d1 / (S * sigma * math.sqrt(T))
    vega = S * pdf_d1 * math.sqrt(T) / 100  # 每 1% 波动率变动的价格变化

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


# ----------------------------------------------------------------------
# 2. 隐含波动率反推 (IV Solver) —— Brent 法比牛顿法更稳健，不易发散
# ----------------------------------------------------------------------

def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: Literal["call", "put"] = "call",
    lo: float = 1e-4,
    hi: float = 5.0,
) -> Optional[float]:
    """
    用 Brent 法反推隐含波动率。
    market_price: 用于反推的期权价格——推荐传入 mid_price()，而不是 lastPrice
    返回 None 表示无解（例如报价低于内在价值，或区间内无根）
    """
    def objective(sigma):
        inp = BSInputs(S=S, K=K, T=T, r=r, sigma=sigma, option_type=option_type)
        return bs_price(inp) - market_price

    try:
        # 检查区间端点是否异号，否则 brentq 会报错
        f_lo, f_hi = objective(lo), objective(hi)
        if f_lo * f_hi > 0:
            return None
        return brentq(objective, lo, hi, xtol=1e-6)
    except (ValueError, RuntimeError):
        return None


def mid_price(bid: float, ask: float, last: Optional[float] = None) -> Optional[float]:
    """
    用 (bid+ask)/2 中间价代替 lastPrice 做 IV 反推，避免用到过期的最新成交价。
    - 如果 bid/ask 都有效（>0 且 ask>=bid），返回中间价
    - 如果 bid/ask 缺失或异常（例如无流动性行权价常见的 bid=0），退回用 last
    - 都没有则返回 None，调用方应跳过该行权价
    """
    if bid and ask and ask >= bid > 0:
        return (bid + ask) / 2
    if last and last > 0:
        return last
    return None


# ----------------------------------------------------------------------
# 2b. 波动率微笑扫描 (Volatility Smile) —— 对整条期权链逐个行权价反推 IV
# ----------------------------------------------------------------------

def volatility_smile_scan(
    strikes: np.ndarray,
    bids: np.ndarray,
    asks: np.ndarray,
    lasts: np.ndarray,
    spot: float,
    T: float,
    r: float,
    option_type: Literal["call", "put"] = "call",
) -> dict:
    """
    对给定的一组行权价批量反推 IV，得到「波动率微笑/偏斜」曲线。
    四个数组（strikes/bids/asks/lasts）必须等长、一一对应。

    返回 {"strikes": [...], "ivs": [...]}，跳过反推失败或无有效报价的行权价。
    """
    out_strikes, out_ivs = [], []
    for K, bid, ask, last in zip(strikes, bids, asks, lasts):
        price = mid_price(bid, ask, last)
        if price is None:
            continue
        iv = implied_volatility(market_price=price, S=spot, K=K, T=T, r=r, option_type=option_type)
        if iv is not None:
            out_strikes.append(float(K))
            out_ivs.append(float(iv))
    return {"strikes": out_strikes, "ivs": out_ivs}


# ----------------------------------------------------------------------
# 3. 隐含振幅 (Implied Move)
# ----------------------------------------------------------------------

def implied_move(S: float, iv: float, days: float, trading_days_per_year: int = 252) -> float:
    """
    市场预期在 `days` 天内的 1 个标准差振幅（美元）。
    注意：这是「1 倍标准差」区间，统计上大约对应 ~68% 的概率股价落在这个范围内，
    不是绝对的天花板/地板——大约每 3 次里就有 1 次会突破这个区间。
    trading_days_per_year: 用 252 (交易日) 或 365 (自然日) 均可，需与你的 T 定义保持一致
    """
    return S * iv * math.sqrt(days / trading_days_per_year)


def todays_expected_range(
    spot: float, iv: float, days: float = 1, trading_days_per_year: int = 365,
    std_devs: float = 1.0,
) -> dict:
    """
    直接返回今日（或未来 N 天）预期的高/低价区间。
    std_devs: 想要几倍标准差的区间——
        1.0 -> 约 68% 概率覆盖（最常用，也是"市场隐含振幅"的标准定义）
        1.28 -> 约 80% 概率覆盖
        2.0 -> 约 95% 概率覆盖（区间会更宽）
    """
    move = implied_move(spot, iv, days, trading_days_per_year) * std_devs
    return {
        "expected_high": spot + move,
        "expected_low": spot - move,
        "move": move,
        "confidence": {1.0: "~68%", 1.28: "~80%", 2.0: "~95%"}.get(std_devs, "自定义"),
    }


def todays_expected_range_skewed(
    spot: float, call_iv: float, put_iv: float, days: float = 1,
    trading_days_per_year: int = 365, std_devs: float = 1.0,
) -> dict:
    """
    上下不对称版本的预期区间——用 ATM Call IV 算「向上」的空间，
    用 ATM Put IV 算「向下」的空间。

    这才是真正反映你截图里 Put/Call 报价差异（波动率偏斜/skew）的版本：
    如果 Put IV 明显高于 Call IV（下跌保护需求更强），算出来的下方空间会
    比上方更宽——这是"市场认为下跌风险更大"的定量体现，而不是简单对称振幅。

    注意：这依然是「期权定价隐含」的统计区间，不是方向性预测。IV 偏高
    只代表买 Put 的人愿意付更贵的保险费，不代表股价一定会跌。
    """
    up_move = implied_move(spot, call_iv, days, trading_days_per_year) * std_devs
    down_move = implied_move(spot, put_iv, days, trading_days_per_year) * std_devs
    skew_pct = (put_iv - call_iv) / call_iv if call_iv else 0.0
    return {
        "expected_high": spot + up_move,
        "expected_low": spot - down_move,
        "up_move": up_move,
        "down_move": down_move,
        "call_iv": call_iv,
        "put_iv": put_iv,
        "skew_pct": skew_pct,  # 正值：Put IV 更高，市场更担心下跌；负值：反之
        "confidence": {1.0: "~68%", 1.28: "~80%", 2.0: "~95%"}.get(std_devs, "自定义"),
    }


# ----------------------------------------------------------------------
# 4. Max Pain 最大痛点模型
# ----------------------------------------------------------------------

def max_pain(
    strikes: np.ndarray, call_oi: np.ndarray, put_oi: np.ndarray,
    spot: Optional[float] = None, strike_range_pct: Optional[float] = 0.25,
) -> tuple[float, dict]:
    """
    遍历所有行权价，计算若正股收在该价格时，做市商（期权卖方）需要支付的
    总内在价值，找出使其最小化的价格 —— 即 Max Pain 点。

    strikes, call_oi, put_oi: 等长数组，分别是行权价、看涨未平仓量、看跌未平仓量

    ⚠️ 重要：真实期权链里经常有个别远端行权价（深度实值/虚值、很久以前建仓、
    流动性差）挂着异常巨大的 OI（historical leftover positions）。这类行权价
    对短期股价的"磁铁效应"其实很弱，但如果不过滤，会把整条 Loss 曲线拉成
    单调曲线，导致算出的 Max Pain 跑到行权价范围的最边缘（而不是现价附近），
    完全失去参考意义。

    spot + strike_range_pct: 若提供 spot，则只使用 [spot*(1-pct), spot*(1+pct)]
    范围内的行权价参与计算（默认 ±25%），这是业界常见做法。设 strike_range_pct=None
    可关闭过滤，使用全部行权价。

    返回: (max_pain_price, {strike: total_loss})  —— losses 只包含参与计算的行权价
    """
    strikes = np.asarray(strikes, dtype=float)
    call_oi = np.asarray(call_oi, dtype=float)
    put_oi = np.asarray(put_oi, dtype=float)

    if spot is not None and strike_range_pct is not None:
        mask = (strikes >= spot * (1 - strike_range_pct)) & (strikes <= spot * (1 + strike_range_pct))
        calc_strikes = strikes[mask]
    else:
        calc_strikes = strikes

    if len(calc_strikes) == 0:
        raise ValueError("过滤后没有可用的行权价，请调大 strike_range_pct 或检查 spot 是否正确")

    losses = {}
    for candidate in calc_strikes:
        # 注意：内在价值计算仍用全部行权价的 OI（因为深度实值/虚值期权对 candidate
        # 价格下的浮亏依然是真实存在的负债），只是不把这些远端价位本身当作候选解。
        call_loss = np.sum(np.maximum(0, candidate - strikes) * call_oi)
        put_loss = np.sum(np.maximum(0, strikes - candidate) * put_oi)
        losses[float(candidate)] = float(call_loss + put_loss)

    best_strike = min(losses, key=losses.get)
    return best_strike, losses


def diagnose_max_pain_outliers(
    strikes: np.ndarray, call_oi: np.ndarray, put_oi: np.ndarray,
    spot: float, top_n: int = 5,
) -> dict:
    """
    排查工具：找出哪些行权价的 OI 贡献了最大比例的「总浮亏」，
    帮你判断 Max Pain 结果是否被个别远端异常大单拉偏。

    同时返回 total_call_oi / total_put_oi 这两个汇总值——如果它们都接近 0，
    说明问题不是"某个行权价异常"，而是【整条链的 OI 数据本身没抓到】
    （常见原因：数据源在盘前/盘后返回的 openInterest 是 NaN，被 fillna(0)
    填成了 0；此时 Max Pain 每个候选价的浮亏都并列为 0，算法会退化成"数组里
    第一个行权价"，而不是有意义的结果——遇到这种情况应该先检查数据源，而不是
    怀疑模型逻辑）。

    正常情况下（OI 数据有效），贡献最大的应该是离现价较近、成交/换手都正常
    的行权价。如果贡献榜前几名是离现价很远、且 OI 也是 0 的行权价，基本可以
    确认是上面说的"整链 OI 缺失"问题。

    返回 {"top": [...], "total_call_oi": ..., "total_put_oi": ...}
    """
    strikes = np.asarray(strikes, dtype=float)
    call_oi = np.asarray(call_oi, dtype=float)
    put_oi = np.asarray(put_oi, dtype=float)

    rows = []
    for k, coi, poi in zip(strikes, call_oi, put_oi):
        rows.append({
            "strike": float(k),
            "distance_from_spot_pct": (k - spot) / spot * 100,
            "call_oi": float(coi),
            "put_oi": float(poi),
            "call_weight": float(coi * abs(k - spot)),
            "put_weight": float(poi * abs(k - spot)),
        })
    rows.sort(key=lambda r: max(r["call_weight"], r["put_weight"]), reverse=True)
    return {
        "top": rows[:top_n],
        "total_call_oi": float(np.nansum(call_oi)),
        "total_put_oi": float(np.nansum(put_oi)),
    }


# ----------------------------------------------------------------------
# 5. Net Gamma Exposure (GEX) 估算
# ----------------------------------------------------------------------

def net_gamma_exposure(
    strikes: np.ndarray,
    call_gamma: np.ndarray,
    call_oi: np.ndarray,
    put_gamma: np.ndarray,
    put_oi: np.ndarray,
    spot: float,
    contract_multiplier: int = 100,
) -> float:
    """
    估算做市商净 Gamma 敞口（简化假设：做市商整体持有客户的反向头寸，
    即客户买 Call 做市商卖 Call）。
    正值 GEX -> 做市商倾向于逢高卖出、逢低买入，压制波动（"钉住"股价）
    负值 GEX -> 做市商追涨杀跌，放大波动
    """
    call_gex = np.sum(call_gamma * call_oi) * contract_multiplier * spot
    put_gex = np.sum(put_gamma * put_oi) * contract_multiplier * spot
    # 做市商对 Put 通常是净多头 Gamma 的反向暴露，符号处理视具体假设而定
    return call_gex - put_gex


def find_gamma_flip(
    strikes, call_oi, call_ivs, put_oi, put_ivs, T: float, r: float, spot: float,
    search_pct: float = 0.15, num_points: int = 60, contract_multiplier: int = 100,
) -> dict:
    """
    在 spot 上下 search_pct 范围内，把「净GEX」在一系列假设价位上重新算一遍，
    找出净GEX由正转负（或反之）的那个价格 —— Gamma Flip 点。

    这是 SpotGamma / Glassnode 等机构公开发表的方法（不是我们瞎编的），
    但预测的是「波动率机制」（今天容易被钉住 vs 容易加速），不是涨跌方向。

    strikes/call_oi/call_ivs/put_oi/put_ivs: 等长数组，ivs 用 volatility_smile_scan()
    算出来的每个行权价的IV（简化假设：短时间内IV微笑形状不随价格变化）

    返回 dict: gex_at_spot（现价处的净GEX）、regime（当前机制）、
    flip_points（找到的翻转价格列表，可能不止一个）
    """
    strikes = np.asarray(strikes, dtype=float)
    call_oi = np.asarray(call_oi, dtype=float)
    call_ivs = np.asarray(call_ivs, dtype=float)
    put_oi = np.asarray(put_oi, dtype=float)
    put_ivs = np.asarray(put_ivs, dtype=float)

    def _net_gex_at(hyp_spot):
        total = 0.0
        for K, oi, iv in zip(strikes, call_oi, call_ivs):
            if oi and iv:
                g = bs_greeks(BSInputs(S=hyp_spot, K=K, T=T, r=r, sigma=iv, option_type="call"))["gamma"]
                total += g * oi * contract_multiplier * hyp_spot
        for K, oi, iv in zip(strikes, put_oi, put_ivs):
            if oi and iv:
                g = bs_greeks(BSInputs(S=hyp_spot, K=K, T=T, r=r, sigma=iv, option_type="put"))["gamma"]
                total -= g * oi * contract_multiplier * hyp_spot
        return total

    grid = np.linspace(spot * (1 - search_pct), spot * (1 + search_pct), num_points)
    gex_curve = np.array([_net_gex_at(p) for p in grid])

    flip_points = []
    signs = np.sign(gex_curve)
    for i in range(len(grid) - 1):
        if signs[i] != 0 and signs[i + 1] != 0 and signs[i] != signs[i + 1]:
            x0, x1 = grid[i], grid[i + 1]
            y0, y1 = gex_curve[i], gex_curve[i + 1]
            flip_price = x0 - y0 * (x1 - x0) / (y1 - y0)  # 线性插值找精确零点
            flip_points.append(float(flip_price))

    gex_at_spot = float(_net_gex_at(spot))
    regime = "positive（做市商倾向压制波动，价格更容易被钉住/窄幅震荡）" if gex_at_spot > 0 \
        else "negative（做市商倾向放大波动，更容易出现加速/单边行情）"

    return {
        "gex_at_spot": gex_at_spot, "regime": regime, "flip_points": flip_points,
        "grid": grid, "gex_curve": gex_curve,
    }


def print_gamma_flip(result: dict, spot: float):
    """打印 find_gamma_flip() 的结果，不产生图片"""
    r = result
    print(f"现价 {spot:.2f} 处的净GEX: {r['gex_at_spot']:,.0f}")
    print(f"当前机制: {r['regime']}")
    if r["flip_points"]:
        for fp in r["flip_points"]:
            direction = "上方" if fp > spot else "下方"
            print(f"找到 Gamma Flip 点: {fp:.2f}（在现价{direction}, "
                  f"距现价 {abs(fp-spot)/spot:.1%}）—— 价格如果穿过这个点，"
                  f"做市商的对冲行为会从「压制波动」切换到「放大波动」（或反之）")
    else:
        print(f"在 ±{(r['grid'][-1]/spot-1):.0%} 的搜索范围内没有找到翻转点，"
              f"说明当前机制（{'正' if r['gex_at_spot']>0 else '负'}GEX）在这个价格区间内比较稳定")


# ----------------------------------------------------------------------
# 6. 可视化：波动率微笑 / Max Pain 曲线 / Gamma 分布
# ----------------------------------------------------------------------

def plot_analysis(
    spot: float,
    smile_calls: dict,
    smile_puts: dict,
    max_pain_losses: dict,
    mp_strike: float,
    gamma_by_strike: dict,
    save_path: str = "options_analysis.png",
):
    """
    生成一张三合一分析图：
      左：波动率微笑（Call/Put IV vs 行权价）
      中：Max Pain 曲线（不同行权价对应的做市商总浮亏）
      右：Gamma 分布（每个行权价的总 Gamma 敞口，正负代表方向）

    依赖 matplotlib，需要 pip install matplotlib
    """
    import matplotlib
    matplotlib.use("Agg")  # 无显示环境也能保存图片
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    # 注：图表文字统一用英文——matplotlib 默认字体不含中文字形，
    # 中文标签会在大多数环境下渲染成方块，除非额外安装中文字体。

    # --- 波动率微笑 ---
    ax = axes[0]
    if smile_calls["strikes"]:
        ax.plot(smile_calls["strikes"], [v * 100 for v in smile_calls["ivs"]],
                "o-", color="#2E86AB", label="Call IV")
    if smile_puts["strikes"]:
        ax.plot(smile_puts["strikes"], [v * 100 for v in smile_puts["ivs"]],
                "o-", color="#E63946", label="Put IV")
    ax.axvline(spot, color="gray", linestyle="--", linewidth=1, label=f"Spot {spot:.1f}")
    ax.set_xlabel("Strike")
    ax.set_ylabel("Implied Volatility (%)")
    ax.set_title("Volatility Smile")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # --- Max Pain 曲线 ---
    ax = axes[1]
    strikes_sorted = sorted(max_pain_losses.keys())
    losses_sorted = [max_pain_losses[s] for s in strikes_sorted]
    ax.plot(strikes_sorted, losses_sorted, color="#457B9D", linewidth=1.5)
    ax.axvline(mp_strike, color="#E63946", linestyle="--", label=f"Max Pain = {mp_strike:.1f}")
    ax.axvline(spot, color="gray", linestyle=":", label=f"Spot {spot:.1f}")
    ax.set_xlabel("Strike")
    ax.set_ylabel("Total Dealer Loss (relative)")
    ax.set_title("Max Pain Curve")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # --- Gamma 分布 ---
    ax = axes[2]
    g_strikes = sorted(gamma_by_strike.keys())
    g_values = [gamma_by_strike[s] for s in g_strikes]
    colors = ["#2E86AB" if v >= 0 else "#E63946" for v in g_values]
    ax.bar(g_strikes, g_values, width=(max(g_strikes) - min(g_strikes)) / max(len(g_strikes), 1) * 0.8
           if len(g_strikes) > 1 else 1, color=colors)
    ax.axvline(spot, color="gray", linestyle="--", linewidth=1, label=f"Spot {spot:.1f}")
    ax.set_xlabel("Strike")
    ax.set_ylabel("Gamma Exposure (+ pins, - amplifies)")
    ax.set_title("Gamma Distribution (GEX by Strike)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.close(fig)
    return save_path


# ----------------------------------------------------------------------
# 7. 示例：用 yfinance 抓取真实期权链并跑一遍完整流程
# ----------------------------------------------------------------------

def demo_with_real_data(ticker_symbol: str = "NVDA", risk_free_rate: float = 0.045):
    """
    需要联网环境安装 yfinance: pip install yfinance
    演示：抓取最近到期日的期权链，计算 IV / Greeks / Max Pain / Implied Move
    """
    import yfinance as yf
    from datetime import datetime

    ticker = yf.Ticker(ticker_symbol)
    spot = ticker.history(period="1d")["Close"].iloc[-1]

    expirations = ticker.options
    if not expirations:
        print("未找到可用的期权到期日")
        return

    exp_date = expirations[0]
    chain = ticker.option_chain(exp_date)
    calls, puts = chain.calls, chain.puts

    days_to_exp = (datetime.strptime(exp_date, "%Y-%m-%d") - datetime.now()).days
    days_to_exp = max(days_to_exp, 1)
    T = days_to_exp / 365

    print(f"\n=== {ticker_symbol} 期权分析 | 现价: {spot:.2f} | 到期日: {exp_date} ({days_to_exp} 天) ===\n")

    # --- 用 bid/ask 中间价分别反推平值 Call 和 Put 的 IV，构建不对称隐含区间 ---
    atm_call = calls.iloc[(calls["strike"] - spot).abs().argsort()[:1]]
    atm_put = puts.iloc[(puts["strike"] - spot).abs().argsort()[:1]]

    call_iv = put_iv = None
    if not atm_call.empty:
        row = atm_call.iloc[0]
        price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
        call_iv = implied_volatility(
            market_price=price, S=spot, K=row["strike"], T=T, r=risk_free_rate, option_type="call",
        ) if price else None

    if not atm_put.empty:
        row = atm_put.iloc[0]
        price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
        put_iv = implied_volatility(
            market_price=price, S=spot, K=row["strike"], T=T, r=risk_free_rate, option_type="put",
        ) if price else None

    if call_iv and put_iv:
        rng = todays_expected_range_skewed(spot, call_iv, put_iv, days=days_to_exp, trading_days_per_year=365)
        skew_note = "市场对下跌保护需求更高" if rng["skew_pct"] > 0.02 else (
            "市场对上涨追逐更明显" if rng["skew_pct"] < -0.02 else "上下基本对称"
        )
        print(f"平值 Call IV: {call_iv:.2%}  |  平值 Put IV: {put_iv:.2%}  "
              f"(偏斜 {rng['skew_pct']:+.1%}, {skew_note})")
        print(f"市场隐含 {days_to_exp} 天内预期区间 ({rng['confidence']} 概率覆盖，上下不对称): "
              f"{rng['expected_low']:.2f} ~ {rng['expected_high']:.2f} "
              f"(下方 -{rng['down_move']:.2f} / 上方 +{rng['up_move']:.2f})\n")
    elif call_iv:
        rng = todays_expected_range(spot, call_iv, days=days_to_exp, trading_days_per_year=365)
        print(f"平值期权 (Strike={atm_call.iloc[0]['strike']}) 反推 IV: {call_iv:.2%}")
        print(f"市场隐含 {days_to_exp} 天内预期区间 ({rng['confidence']} 概率覆盖，对称近似): "
              f"{rng['expected_low']:.2f} ~ {rng['expected_high']:.2f} "
              f"(±{rng['move']:.2f} 美元)\n")

    # --- Max Pain ---
    strikes = calls["strike"].values
    call_oi = calls["openInterest"].fillna(0).values
    put_oi_aligned = np.array([
        puts.loc[puts["strike"] == s, "openInterest"].fillna(0).sum() for s in strikes
    ])
    mp_strike, losses = max_pain(strikes, call_oi, put_oi_aligned, spot=spot, strike_range_pct=0.25)
    print(f"Max Pain 最大痛点 (仅现价±25%范围内计算): {mp_strike:.2f}")

    # --- 诊断：看看是不是有远端异常大单在扭曲结果（排查用，可选） ---
    diag = diagnose_max_pain_outliers(strikes, call_oi, put_oi_aligned, spot=spot, top_n=3)
    print(f"[诊断] 全链 Call OI 总和={diag['total_call_oi']:.0f}  Put OI 总和={diag['total_put_oi']:.0f}")
    if diag["total_call_oi"] < 10 and diag["total_put_oi"] < 10:
        print("⚠️ 警告：整条链的 OI 几乎全是 0——数据源大概率没抓到真实持仓量"
              "（yfinance 在盘前/盘后经常返回 NaN openInterest），"
              "上面的 Max Pain 结果不可信，建议在美股正常交易时段重新抓取。")
    print("浮亏权重最高的行权价（用于排查是否有异常大单）:")
    for r in diag["top"]:
        print(f"  Strike={r['strike']:.0f} ({r['distance_from_spot_pct']:+.1f}%)  "
              f"Call OI={r['call_oi']:.0f}  Put OI={r['put_oi']:.0f}")

    # --- 价内/价外 OI 拆分（谁现在赢面更大）---
    if diag["total_call_oi"] >= 10 or diag["total_put_oi"] >= 10:
        print()
        itm_breakdown = itm_oi_breakdown(strikes, call_oi, put_oi_aligned, spot=spot)
        print_itm_oi_breakdown(itm_breakdown, spot=spot)
        itm_detail = itm_oi_by_strike(strikes, call_oi, put_oi_aligned, spot=spot)
        print_itm_oi_extremes(itm_detail)

    # --- 波动率微笑：对整条期权链批量反推 IV ---
    smile_calls = volatility_smile_scan(
        strikes=calls["strike"].values,
        bids=calls["bid"].fillna(0).values,
        asks=calls["ask"].fillna(0).values,
        lasts=calls["lastPrice"].fillna(0).values,
        spot=spot, T=T, r=risk_free_rate, option_type="call",
    )
    smile_puts = volatility_smile_scan(
        strikes=puts["strike"].values,
        bids=puts["bid"].fillna(0).values,
        asks=puts["ask"].fillna(0).values,
        lasts=puts["lastPrice"].fillna(0).values,
        spot=spot, T=T, r=risk_free_rate, option_type="put",
    )

    # --- Gamma 分布：对每个行权价，用其反推出的 IV 算出 Gamma，再乘以 OI ---
    gamma_by_strike = {}
    for K, oi in zip(calls["strike"].values, call_oi):
        iv_k = dict(zip(smile_calls["strikes"], smile_calls["ivs"])).get(float(K))
        if iv_k and oi:
            g = bs_greeks(BSInputs(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv_k, option_type="call"))["gamma"]
            gamma_by_strike[float(K)] = gamma_by_strike.get(float(K), 0) + g * oi
    for K, oi in zip(strikes, put_oi_aligned):
        iv_k = dict(zip(smile_puts["strikes"], smile_puts["ivs"])).get(float(K))
        if iv_k and oi:
            g = bs_greeks(BSInputs(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv_k, option_type="put"))["gamma"]
            gamma_by_strike[float(K)] = gamma_by_strike.get(float(K), 0) - g * oi  # put gamma 记为负向敞口

    # --- Gamma Flip 点：净GEX由正转负的价格（波动率机制分析，不是方向预测）---
    call_iv_map = dict(zip(smile_calls["strikes"], smile_calls["ivs"]))
    put_iv_map = dict(zip(smile_puts["strikes"], smile_puts["ivs"]))
    call_ivs_aligned = np.array([call_iv_map.get(float(k), np.nan) for k in strikes])
    put_ivs_aligned = np.array([put_iv_map.get(float(k), np.nan) for k in strikes])
    valid_mask = ~(np.isnan(call_ivs_aligned) & np.isnan(put_ivs_aligned))
    if valid_mask.sum() >= 5:
        print()
        gex_result = find_gamma_flip(
            strikes[valid_mask],
            np.nan_to_num(call_oi[valid_mask]), np.nan_to_num(call_ivs_aligned[valid_mask]),
            np.nan_to_num(put_oi_aligned[valid_mask]), np.nan_to_num(put_ivs_aligned[valid_mask]),
            T=T, r=risk_free_rate, spot=spot,
        )
        print_gamma_flip(gex_result, spot=spot)

    # --- 波动率微笑/Gamma 已经算完了，但用户要求不再生成图片，这里不画图 ---



# ----------------------------------------------------------------------
# 8. 备用数据源：用 yahooquery 代替 yfinance
#    （yfinance 的 openInterest 字段目前有已知 bug，经常返回全 0——
#     见 https://github.com/ranaroussi/yfinance/issues/2408 ）
# ----------------------------------------------------------------------

def demo_with_real_data_yq(ticker_symbol: str = "NVDA", risk_free_rate: float = 0.045):
    """
    跟 demo_with_real_data() 功能完全一致（IV反推/不对称隐含区间/Max Pain/
    诊断/波动率微笑/可视化），只是把数据源从 yfinance 换成 yahooquery。

    需要先安装：pip install yahooquery pandas

    yahooquery 返回的期权链结构和 yfinance 不一样：不是分开的 .calls/.puts，
    而是一个把所有到期日、Call/Put 都合并在一起的 MultiIndex DataFrame，
    索引层级是 (symbol, expiration, optionType)。这个函数负责把它拆解成
    跟 demo_with_real_data() 里一样的 calls/puts 两张表，后面的分析逻辑
    完全复用，不用改。
    """
    try:
        from yahooquery import Ticker as YQTicker
    except ImportError:
        print("请先安装 yahooquery: pip install yahooquery")
        return
    import pandas as pd
    from datetime import datetime

    yq = YQTicker(ticker_symbol)

    # --- 1. 现价 ---
    price_info = yq.price
    if not isinstance(price_info, dict) or ticker_symbol not in price_info:
        print(f"拿不到 {ticker_symbol} 的现价，返回内容：{price_info}")
        return
    spot_info = price_info[ticker_symbol]
    spot = spot_info.get("regularMarketPrice") or spot_info.get("postMarketPrice")
    if not spot:
        print(f"现价字段为空，原始数据：{spot_info}")
        return

    # --- 2. 期权链（yahooquery 是一次性把所有到期日都拉回来的） ---
    chain = yq.option_chain
    if not isinstance(chain, pd.DataFrame) or chain.empty:
        print(f"未找到 {ticker_symbol} 的期权链数据，返回内容：{chain}")
        return

    chain = chain.reset_index()
    # 不同版本的 yahooquery 列名不完全一致，做个兼容
    exp_col = "expiration" if "expiration" in chain.columns else "expiration_date"
    type_col = "optionType" if "optionType" in chain.columns else "option_type"

    expirations = sorted(chain[exp_col].unique())
    if not expirations:
        print("未找到可用的期权到期日")
        return
    exp_date = expirations[0]

    exp_chain = chain[chain[exp_col] == exp_date]
    calls = exp_chain[exp_chain[type_col] == "calls"].reset_index(drop=True).copy()
    puts = exp_chain[exp_chain[type_col] == "puts"].reset_index(drop=True).copy()

    if calls.empty or puts.empty:
        print(f"最近到期日 {exp_date} 的 Call/Put 数据不完整"
              f"（Call {len(calls)} 条, Put {len(puts)} 条），可能是这个到期日流动性太差。")
        return

    exp_timestamp = pd.Timestamp(exp_date)
    exp_date_str = exp_timestamp.strftime("%Y-%m-%d")
    days_to_exp = max((exp_timestamp - pd.Timestamp(datetime.now())).days, 1)
    T = days_to_exp / 365

    print(f"\n=== {ticker_symbol} 期权分析（数据源: yahooquery） | "
          f"现价: {spot:.2f} | 到期日: {exp_date_str} ({days_to_exp} 天) ===\n")

    # --- 3. 用 bid/ask 中间价分别反推平值 Call/Put IV，构建不对称隐含区间 ---
    atm_call = calls.iloc[(calls["strike"] - spot).abs().argsort()[:1]]
    atm_put = puts.iloc[(puts["strike"] - spot).abs().argsort()[:1]]

    call_iv = put_iv = None
    if not atm_call.empty:
        row = atm_call.iloc[0]
        price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
        call_iv = implied_volatility(
            market_price=price, S=spot, K=row["strike"], T=T, r=risk_free_rate, option_type="call",
        ) if price else None

    if not atm_put.empty:
        row = atm_put.iloc[0]
        price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
        put_iv = implied_volatility(
            market_price=price, S=spot, K=row["strike"], T=T, r=risk_free_rate, option_type="put",
        ) if price else None

    if call_iv and put_iv:
        rng = todays_expected_range_skewed(spot, call_iv, put_iv, days=days_to_exp, trading_days_per_year=365)
        skew_note = "市场对下跌保护需求更高" if rng["skew_pct"] > 0.02 else (
            "市场对上涨追逐更明显" if rng["skew_pct"] < -0.02 else "上下基本对称"
        )
        print(f"平值 Call IV: {call_iv:.2%}  |  平值 Put IV: {put_iv:.2%}  "
              f"(偏斜 {rng['skew_pct']:+.1%}, {skew_note})")
        print(f"市场隐含 {days_to_exp} 天内预期区间 ({rng['confidence']} 概率覆盖，上下不对称): "
              f"{rng['expected_low']:.2f} ~ {rng['expected_high']:.2f} "
              f"(下方 -{rng['down_move']:.2f} / 上方 +{rng['up_move']:.2f})\n")
    elif call_iv:
        rng = todays_expected_range(spot, call_iv, days=days_to_exp, trading_days_per_year=365)
        print(f"平值期权 (Strike={atm_call.iloc[0]['strike']}) 反推 IV: {call_iv:.2%}")
        print(f"市场隐含 {days_to_exp} 天内预期区间 ({rng['confidence']} 概率覆盖，对称近似): "
              f"{rng['expected_low']:.2f} ~ {rng['expected_high']:.2f} "
              f"(±{rng['move']:.2f} 美元)\n")
    else:
        print("反推 IV 失败（可能是 bid/ask/lastPrice 都缺失），跳过隐含区间计算\n")

    # --- 4. Max Pain（现价±25%范围内计算）+ 诊断 ---
    strikes = calls["strike"].values
    call_oi = calls["openInterest"].fillna(0).values
    put_oi_aligned = np.array([
        puts.loc[puts["strike"] == s, "openInterest"].fillna(0).sum() for s in strikes
    ])
    mp_strike, losses = max_pain(strikes, call_oi, put_oi_aligned, spot=spot, strike_range_pct=0.25)
    print(f"Max Pain 最大痛点 (仅现价±25%范围内计算): {mp_strike:.2f}")

    diag = diagnose_max_pain_outliers(strikes, call_oi, put_oi_aligned, spot=spot, top_n=3)
    print(f"[诊断] 全链 Call OI 总和={diag['total_call_oi']:.0f}  Put OI 总和={diag['total_put_oi']:.0f}")
    if diag["total_call_oi"] < 10 and diag["total_put_oi"] < 10:
        print("⚠️ 警告：整条链的 OI 几乎全是 0——数据源可能仍然没抓到真实持仓量，"
              "上面的 Max Pain 结果不可信。")
    print("浮亏权重最高的行权价（用于排查是否有异常大单）:")
    for r in diag["top"]:
        print(f"  Strike={r['strike']:.0f} ({r['distance_from_spot_pct']:+.1f}%)  "
              f"Call OI={r['call_oi']:.0f}  Put OI={r['put_oi']:.0f}")

    # --- 价内/价外 OI 拆分（谁现在赢面更大）---
    if diag["total_call_oi"] >= 10 or diag["total_put_oi"] >= 10:
        print()
        itm_breakdown = itm_oi_breakdown(strikes, call_oi, put_oi_aligned, spot=spot)
        print_itm_oi_breakdown(itm_breakdown, spot=spot)
        itm_detail = itm_oi_by_strike(strikes, call_oi, put_oi_aligned, spot=spot)
        print_itm_oi_extremes(itm_detail)

    # --- 5. 波动率微笑：对整条期权链批量反推 IV ---
    smile_calls = volatility_smile_scan(
        strikes=calls["strike"].values,
        bids=calls["bid"].fillna(0).values,
        asks=calls["ask"].fillna(0).values,
        lasts=calls["lastPrice"].fillna(0).values,
        spot=spot, T=T, r=risk_free_rate, option_type="call",
    )
    smile_puts = volatility_smile_scan(
        strikes=puts["strike"].values,
        bids=puts["bid"].fillna(0).values,
        asks=puts["ask"].fillna(0).values,
        lasts=puts["lastPrice"].fillna(0).values,
        spot=spot, T=T, r=risk_free_rate, option_type="put",
    )

    # --- 6. Gamma 分布 ---
    gamma_by_strike = {}
    for K, oi in zip(calls["strike"].values, call_oi):
        iv_k = dict(zip(smile_calls["strikes"], smile_calls["ivs"])).get(float(K))
        if iv_k and oi:
            g = bs_greeks(BSInputs(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv_k, option_type="call"))["gamma"]
            gamma_by_strike[float(K)] = gamma_by_strike.get(float(K), 0) + g * oi
    for K, oi in zip(strikes, put_oi_aligned):
        iv_k = dict(zip(smile_puts["strikes"], smile_puts["ivs"])).get(float(K))
        if iv_k and oi:
            g = bs_greeks(BSInputs(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv_k, option_type="put"))["gamma"]
            gamma_by_strike[float(K)] = gamma_by_strike.get(float(K), 0) - g * oi

    # --- Gamma Flip 点：净GEX由正转负的价格（波动率机制分析，不是方向预测）---
    call_iv_map = dict(zip(smile_calls["strikes"], smile_calls["ivs"]))
    put_iv_map = dict(zip(smile_puts["strikes"], smile_puts["ivs"]))
    call_ivs_aligned = np.array([call_iv_map.get(float(k), np.nan) for k in strikes])
    put_ivs_aligned = np.array([put_iv_map.get(float(k), np.nan) for k in strikes])
    valid_mask = ~(np.isnan(call_ivs_aligned) & np.isnan(put_ivs_aligned))
    if valid_mask.sum() >= 5:
        print()
        gex_result = find_gamma_flip(
            strikes[valid_mask],
            np.nan_to_num(call_oi[valid_mask]), np.nan_to_num(call_ivs_aligned[valid_mask]),
            np.nan_to_num(put_oi_aligned[valid_mask]), np.nan_to_num(put_ivs_aligned[valid_mask]),
            T=T, r=risk_free_rate, spot=spot,
        )
        print_gamma_flip(gex_result, spot=spot)

    # --- 波动率微笑/Gamma 已经算完了，但用户要求不再生成图片，这里不画图 ---


# ----------------------------------------------------------------------
# 9. 更靠谱的数据源：Tradier API（不依赖 Yahoo 后端，真实券商数据）
#    免费注册即可拿 Sandbox Token（延迟15分钟，够研究用）：
#    1. https://tradier.com 注册账号
#    2. https://web.tradier.com/user/api 生成 Sandbox Access Token
#    文档：https://docs.tradier.com/reference/brokerage-api-markets-get-options-chains
# ----------------------------------------------------------------------

def fetch_tradier_option_chain(
    symbol: str, api_token: str, expiration: Optional[str] = None, sandbox: bool = True,
):
    """
    用 requests 直接调 Tradier 的期权链接口。跟 yfinance/yahooquery 不同，
    这是真实券商数据，Open Interest 是可靠的，而且 Tradier 自带由 ORATS
    算好的 Greeks/IV（不用我们自己反推，但下面的 demo 函数依然会用我们自己
    的 BS 反推做一次交叉验证，方便你对比两边算出来的 IV 是否一致）。

    sandbox=True 用延迟15分钟的免费沙盒数据（个人研究够用）；
    sandbox=False 需要真实出资的 Tradier Brokerage 账户才能拿实时数据。

    返回: (spot, exp_date, calls_df, puts_df)  —— calls/puts 已经把列名统一成
    跟 demo_with_real_data() 系列函数一致的 strike/bid/ask/lastPrice/openInterest
    """
    import requests
    import pandas as pd

    base = "https://sandbox.tradier.com/v1" if sandbox else "https://api.tradier.com/v1"
    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}

    # 1. 现价
    r = requests.get(f"{base}/markets/quotes", params={"symbols": symbol}, headers=headers, timeout=10)
    r.raise_for_status()
    quote = r.json()["quotes"]["quote"]
    spot = quote.get("last") or quote.get("close")
    if not spot:
        raise ValueError(f"拿不到 {symbol} 的现价，原始返回：{quote}")

    # 2. 到期日列表
    r = requests.get(f"{base}/markets/options/expirations", params={"symbol": symbol}, headers=headers, timeout=10)
    r.raise_for_status()
    expirations = r.json()["expirations"]["date"]
    if isinstance(expirations, str):
        expirations = [expirations]
    if not expirations:
        raise ValueError(f"未找到 {symbol} 的期权到期日")
    exp_date = expiration or expirations[0]

    # 3. 期权链（greeks=true 会带上 ORATS 算好的 IV/Delta/Gamma 等）
    r = requests.get(
        f"{base}/markets/options/chains",
        params={"symbol": symbol, "expiration": exp_date, "greeks": "true"},
        headers=headers, timeout=10,
    )
    r.raise_for_status()
    options = r.json()["options"]["option"]
    if isinstance(options, dict):  # 只有一个合约时 Tradier 不会包装成 list
        options = [options]

    df = pd.DataFrame(options)
    if df.empty:
        raise ValueError(f"{symbol} 在 {exp_date} 这个到期日没有期权数据")

    df = df.rename(columns={"open_interest": "openInterest", "last": "lastPrice"})
    # greeks 是嵌套字典，把 mid_iv 拆出来单独一列，方便跟我们自己反推的 IV 对比
    df["tradier_mid_iv"] = df["greeks"].apply(
        lambda g: g.get("mid_iv") if isinstance(g, dict) else None
    )

    calls = df[df["option_type"] == "call"].reset_index(drop=True).copy()
    puts = df[df["option_type"] == "put"].reset_index(drop=True).copy()
    return spot, exp_date, calls, puts


def demo_with_real_data_tradier(
    ticker_symbol: str = "NVDA", api_token: str = "", risk_free_rate: float = 0.045, sandbox: bool = True,
):
    """
    需要先 pip install requests，并且有一个 Tradier 账号的 API Token
    （免费注册，见本节顶部的说明）。

    跟 demo_with_real_data() / demo_with_real_data_yq() 功能一致，
    但数据源换成了 Tradier（真实券商数据，不依赖 Yahoo 后端），
    并且额外打印一行「我们自己反推的 IV vs Tradier/ORATS 算好的 IV」对比，
    方便你交叉验证模型算得对不对。
    """
    if not api_token:
        print("请先去 https://web.tradier.com/user/api 注册免费账号并生成 Sandbox Token，"
              "然后 demo_with_real_data_tradier('NVDA', api_token='你的token')")
        return

    try:
        spot, exp_date_str, calls, puts = fetch_tradier_option_chain(
            ticker_symbol, api_token, sandbox=sandbox,
        )
    except Exception as e:
        print(f"抓取失败: {type(e).__name__}: {e}")
        return

    from datetime import datetime
    days_to_exp = max((datetime.strptime(exp_date_str, "%Y-%m-%d") - datetime.now()).days, 1)
    T = days_to_exp / 365

    print(f"\n=== {ticker_symbol} 期权分析（数据源: Tradier{'-Sandbox' if sandbox else ''}） | "
          f"现价: {spot:.2f} | 到期日: {exp_date_str} ({days_to_exp} 天) ===\n")

    # --- 平值 Call/Put：我们自己反推的 IV vs Tradier/ORATS 给的 IV ---
    atm_call = calls.iloc[(calls["strike"] - spot).abs().argsort()[:1]]
    atm_put = puts.iloc[(puts["strike"] - spot).abs().argsort()[:1]]

    call_iv = put_iv = None
    if not atm_call.empty:
        row = atm_call.iloc[0]
        price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
        call_iv = implied_volatility(
            market_price=price, S=spot, K=row["strike"], T=T, r=risk_free_rate, option_type="call",
        ) if price else None
        print(f"[Call 交叉验证] 我们反推的 IV={call_iv:.2%}" if call_iv else "[Call] 反推失败",
              f" vs Tradier/ORATS IV={row.get('tradier_mid_iv', 0) or 0:.2%}")

    if not atm_put.empty:
        row = atm_put.iloc[0]
        price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
        put_iv = implied_volatility(
            market_price=price, S=spot, K=row["strike"], T=T, r=risk_free_rate, option_type="put",
        ) if price else None
        print(f"[Put  交叉验证] 我们反推的 IV={put_iv:.2%}" if put_iv else "[Put] 反推失败",
              f" vs Tradier/ORATS IV={row.get('tradier_mid_iv', 0) or 0:.2%}")

    if call_iv and put_iv:
        rng = todays_expected_range_skewed(spot, call_iv, put_iv, days=days_to_exp, trading_days_per_year=365)
        skew_note = "市场对下跌保护需求更高" if rng["skew_pct"] > 0.02 else (
            "市场对上涨追逐更明显" if rng["skew_pct"] < -0.02 else "上下基本对称"
        )
        print(f"\n偏斜 {rng['skew_pct']:+.1%} ({skew_note})")
        print(f"市场隐含 {days_to_exp} 天内预期区间 ({rng['confidence']} 概率覆盖，上下不对称): "
              f"{rng['expected_low']:.2f} ~ {rng['expected_high']:.2f} "
              f"(下方 -{rng['down_move']:.2f} / 上方 +{rng['up_move']:.2f})\n")

    # --- Max Pain（真实 OI，不用担心又是全 0）+ 诊断 ---
    strikes = calls["strike"].values
    call_oi = calls["openInterest"].fillna(0).values
    put_oi_aligned = np.array([
        puts.loc[puts["strike"] == s, "openInterest"].fillna(0).sum() for s in strikes
    ])
    mp_strike, losses = max_pain(strikes, call_oi, put_oi_aligned, spot=spot, strike_range_pct=0.25)
    print(f"Max Pain 最大痛点 (仅现价±25%范围内计算): {mp_strike:.2f}")

    diag = diagnose_max_pain_outliers(strikes, call_oi, put_oi_aligned, spot=spot, top_n=3)
    print(f"[诊断] 全链 Call OI 总和={diag['total_call_oi']:.0f}  Put OI 总和={diag['total_put_oi']:.0f}")
    if diag["total_call_oi"] < 10 and diag["total_put_oi"] < 10:
        print("⚠️ 警告：这次 OI 还是接近 0——如果连 Tradier 的真实券商数据都是这样，"
              "大概率是这个到期日/标的本身流动性太差，换一个到期日或更主流的标的试试。")
    else:
        print()
        itm_breakdown = itm_oi_breakdown(strikes, call_oi, put_oi_aligned, spot=spot)
        print_itm_oi_breakdown(itm_breakdown, spot=spot)
        itm_detail = itm_oi_by_strike(strikes, call_oi, put_oi_aligned, spot=spot)
        print_itm_oi_extremes(itm_detail)

    # --- 波动率微笑 + Gamma 分布 + 画图（复用同一套逻辑） ---
    smile_calls = volatility_smile_scan(
        strikes=calls["strike"].values, bids=calls["bid"].fillna(0).values,
        asks=calls["ask"].fillna(0).values, lasts=calls["lastPrice"].fillna(0).values,
        spot=spot, T=T, r=risk_free_rate, option_type="call",
    )
    smile_puts = volatility_smile_scan(
        strikes=puts["strike"].values, bids=puts["bid"].fillna(0).values,
        asks=puts["ask"].fillna(0).values, lasts=puts["lastPrice"].fillna(0).values,
        spot=spot, T=T, r=risk_free_rate, option_type="put",
    )

    gamma_by_strike = {}
    for K, oi in zip(calls["strike"].values, call_oi):
        iv_k = dict(zip(smile_calls["strikes"], smile_calls["ivs"])).get(float(K))
        if iv_k and oi:
            g = bs_greeks(BSInputs(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv_k, option_type="call"))["gamma"]
            gamma_by_strike[float(K)] = gamma_by_strike.get(float(K), 0) + g * oi
    for K, oi in zip(strikes, put_oi_aligned):
        iv_k = dict(zip(smile_puts["strikes"], smile_puts["ivs"])).get(float(K))
        if iv_k and oi:
            g = bs_greeks(BSInputs(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv_k, option_type="put"))["gamma"]
            gamma_by_strike[float(K)] = gamma_by_strike.get(float(K), 0) - g * oi

    # --- Gamma Flip 点：净GEX由正转负的价格（波动率机制分析，不是方向预测）---
    call_iv_map = dict(zip(smile_calls["strikes"], smile_calls["ivs"]))
    put_iv_map = dict(zip(smile_puts["strikes"], smile_puts["ivs"]))
    call_ivs_aligned = np.array([call_iv_map.get(float(k), np.nan) for k in strikes])
    put_ivs_aligned = np.array([put_iv_map.get(float(k), np.nan) for k in strikes])
    valid_mask = ~(np.isnan(call_ivs_aligned) & np.isnan(put_ivs_aligned))
    if valid_mask.sum() >= 5:
        print()
        gex_result = find_gamma_flip(
            strikes[valid_mask],
            np.nan_to_num(call_oi[valid_mask]), np.nan_to_num(call_ivs_aligned[valid_mask]),
            np.nan_to_num(put_oi_aligned[valid_mask]), np.nan_to_num(put_ivs_aligned[valid_mask]),
            T=T, r=risk_free_rate, spot=spot,
        )
        print_gamma_flip(gex_result, spot=spot)

    # --- 波动率微笑/Gamma 已经算完了，但用户要求不再生成图片，这里不画图 ---


# ----------------------------------------------------------------------
# 10. 数据源：Polygon.io（2025年10月起改名叫 Massive.com，但老域名
#     api.polygon.io、老包名 polygon-api-client 和你已有的 API Key
#     依然能正常用，不用改代码）
#     用官方 SDK: pip install polygon-api-client  ->  from polygon import RESTClient
#     免费注册: https://polygon.io/dashboard/signup
#     文档: https://polygon.io/docs/options/get_v3_snapshot_options__underlyingasset
#     ⚠️ 免费档有严格限速（约5次/分钟），期权数据是否需要付费 Options 套餐
#     取决于你注册时选的 Plan，具体以你账号后台显示为准。
# ----------------------------------------------------------------------

def fetch_polygon_option_chain(
    symbol: str, api_key: str, expiration: Optional[str] = None,
):
    """
    用官方 SDK `polygon-api-client`（pip install polygon-api-client，
    import 方式是 `from polygon import RESTClient`）拉期权链。

    比手写 requests 版本更省心：list_snapshot_options_chain() 返回的是一个
    会自动帮你翻页的迭代器，不用自己处理 next_url。

    做法分两步（跟 requests 版本思路一致，省额度）：
    1. 用 list_options_contracts() 这个轻量接口，按到期日升序排列，只要
       第1条，拿到「最近到期日」是哪天。
    2. 用 list_snapshot_options_chain() 加上 expiration_date 过滤，
       只拉这一个到期日的完整链。

    返回: (spot, exp_date, calls_df, puts_df) —— 列名统一成
    strike/bid/ask/lastPrice/openInterest/polygon_iv，
    跟前面几个 demo_with_real_data* 函数保持一致。
    """
    from polygon import RESTClient
    import pandas as pd

    client = RESTClient(api_key=api_key)

    # 1. 找最近到期日
    if not expiration:
        contracts_iter = client.list_options_contracts(
            underlying_ticker=symbol, expired=False, order="asc",
            sort="expiration_date", limit=1,
        )
        first_contract = next(iter(contracts_iter), None)
        if first_contract is None:
            raise ValueError(f"未找到 {symbol} 的可交易期权合约")
        expiration = first_contract.expiration_date

    # 2. 拉这一个到期日的完整链（SDK 内部自动翻页，不用我们操心）
    rows = []
    spot = None
    for o in client.list_snapshot_options_chain(
        symbol, params={"expiration_date": expiration, "limit": 250},
    ):
        details = o.details
        last_quote = o.last_quote
        last_trade = o.last_trade
        rows.append({
            "strike": details.strike_price,
            "option_type": details.contract_type,  # "call" / "put"
            "bid": last_quote.bid if last_quote else None,
            "ask": last_quote.ask if last_quote else None,
            "lastPrice": last_trade.price if last_trade else None,
            "openInterest": o.open_interest,
            "volume": o.day.volume if o.day else None,
            "polygon_iv": o.implied_volatility,
        })
        if spot is None and o.underlying_asset:
            spot = o.underlying_asset.price

    if not rows:
        raise ValueError(f"{symbol} 在 {expiration} 这个到期日没有期权快照数据"
                          f"（可能你的套餐不含期权数据，或这个到期日流动性太差）")
    if not spot:
        raise ValueError("拿到了期权数据，但里面没有现价信息（underlying_asset.price 为空）")

    df = pd.DataFrame(rows)
    calls = df[df["option_type"] == "call"].reset_index(drop=True).copy()
    puts = df[df["option_type"] == "put"].reset_index(drop=True).copy()
    return spot, expiration, calls, puts


def demo_with_real_data_polygon(
    ticker_symbol: str = "NVDA", api_key: str = "", risk_free_rate: float = 0.045,
):
    """
    需要先 pip install polygon-api-client，并有一个 Polygon.io（现在也叫
    Massive.com）的免费 API Key: https://polygon.io/dashboard/signup

    跟前面几个 demo_with_real_data* 函数功能一致（IV反推/不对称隐含区间/
    Max Pain/诊断/波动率微笑/可视化），并额外打印一行「我们反推的 IV vs
    Polygon 自己算好的 implied_volatility」做交叉验证。
    """
    if not api_key:
        print("请先去 https://polygon.io/dashboard/signup 注册免费账号并生成 API Key，"
              "然后 demo_with_real_data_polygon('NVDA', api_key='你的key')")
        return

    try:
        spot, exp_date_str, calls, puts = fetch_polygon_option_chain(ticker_symbol, api_key)
    except Exception as e:
        print(f"抓取失败: {type(e).__name__}: {e}")
        return

    from datetime import datetime
    days_to_exp = max((datetime.strptime(exp_date_str, "%Y-%m-%d") - datetime.now()).days, 1)
    T = days_to_exp / 365

    print(f"\n=== {ticker_symbol} 期权分析（数据源: Polygon.io） | "
          f"现价: {spot:.2f} | 到期日: {exp_date_str} ({days_to_exp} 天) ===\n")

    atm_call = calls.iloc[(calls["strike"] - spot).abs().argsort()[:1]]
    atm_put = puts.iloc[(puts["strike"] - spot).abs().argsort()[:1]]

    call_iv = put_iv = None
    if not atm_call.empty:
        row = atm_call.iloc[0]
        price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
        call_iv = implied_volatility(
            market_price=price, S=spot, K=row["strike"], T=T, r=risk_free_rate, option_type="call",
        ) if price else None
        polygon_iv = row.get("polygon_iv")
        print(f"[Call 交叉验证] 我们反推的 IV={call_iv:.2%}" if call_iv else "[Call] 反推失败",
              f" vs Polygon IV={polygon_iv:.2%}" if polygon_iv else " vs Polygon IV=N/A")

    if not atm_put.empty:
        row = atm_put.iloc[0]
        price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
        put_iv = implied_volatility(
            market_price=price, S=spot, K=row["strike"], T=T, r=risk_free_rate, option_type="put",
        ) if price else None
        polygon_iv = row.get("polygon_iv")
        print(f"[Put  交叉验证] 我们反推的 IV={put_iv:.2%}" if put_iv else "[Put] 反推失败",
              f" vs Polygon IV={polygon_iv:.2%}" if polygon_iv else " vs Polygon IV=N/A")

    if call_iv and put_iv:
        rng = todays_expected_range_skewed(spot, call_iv, put_iv, days=days_to_exp, trading_days_per_year=365)
        skew_note = "市场对下跌保护需求更高" if rng["skew_pct"] > 0.02 else (
            "市场对上涨追逐更明显" if rng["skew_pct"] < -0.02 else "上下基本对称"
        )
        print(f"\n偏斜 {rng['skew_pct']:+.1%} ({skew_note})")
        print(f"市场隐含 {days_to_exp} 天内预期区间 ({rng['confidence']} 概率覆盖，上下不对称): "
              f"{rng['expected_low']:.2f} ~ {rng['expected_high']:.2f} "
              f"(下方 -{rng['down_move']:.2f} / 上方 +{rng['up_move']:.2f})\n")

    # --- Max Pain + 诊断 ---
    strikes = calls["strike"].values
    call_oi = calls["openInterest"].fillna(0).values
    put_oi_aligned = np.array([
        puts.loc[puts["strike"] == s, "openInterest"].fillna(0).sum() for s in strikes
    ])
    mp_strike, losses = max_pain(strikes, call_oi, put_oi_aligned, spot=spot, strike_range_pct=0.25)
    print(f"Max Pain 最大痛点 (仅现价±25%范围内计算): {mp_strike:.2f}")

    diag = diagnose_max_pain_outliers(strikes, call_oi, put_oi_aligned, spot=spot, top_n=3)
    print(f"[诊断] 全链 Call OI 总和={diag['total_call_oi']:.0f}  Put OI 总和={diag['total_put_oi']:.0f}")
    if diag["total_call_oi"] < 10 and diag["total_put_oi"] < 10:
        print("⚠️ 警告：这次 OI 还是接近 0——如果连交易所真实数据都这样，"
              "大概率是这个标的/到期日本身流动性太差，换个更主流的标的试试。")
    else:
        print()
        itm_breakdown = itm_oi_breakdown(strikes, call_oi, put_oi_aligned, spot=spot)
        print_itm_oi_breakdown(itm_breakdown, spot=spot)
        itm_detail = itm_oi_by_strike(strikes, call_oi, put_oi_aligned, spot=spot)
        print_itm_oi_extremes(itm_detail)

    # --- 波动率微笑 + Gamma + 画图 ---
    smile_calls = volatility_smile_scan(
        strikes=calls["strike"].values, bids=calls["bid"].fillna(0).values,
        asks=calls["ask"].fillna(0).values, lasts=calls["lastPrice"].fillna(0).values,
        spot=spot, T=T, r=risk_free_rate, option_type="call",
    )
    smile_puts = volatility_smile_scan(
        strikes=puts["strike"].values, bids=puts["bid"].fillna(0).values,
        asks=puts["ask"].fillna(0).values, lasts=puts["lastPrice"].fillna(0).values,
        spot=spot, T=T, r=risk_free_rate, option_type="put",
    )

    gamma_by_strike = {}
    for K, oi in zip(calls["strike"].values, call_oi):
        iv_k = dict(zip(smile_calls["strikes"], smile_calls["ivs"])).get(float(K))
        if iv_k and oi:
            g = bs_greeks(BSInputs(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv_k, option_type="call"))["gamma"]
            gamma_by_strike[float(K)] = gamma_by_strike.get(float(K), 0) + g * oi
    for K, oi in zip(strikes, put_oi_aligned):
        iv_k = dict(zip(smile_puts["strikes"], smile_puts["ivs"])).get(float(K))
        if iv_k and oi:
            g = bs_greeks(BSInputs(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv_k, option_type="put"))["gamma"]
            gamma_by_strike[float(K)] = gamma_by_strike.get(float(K), 0) - g * oi

    # --- Gamma Flip 点：净GEX由正转负的价格（波动率机制分析，不是方向预测）---
    call_iv_map = dict(zip(smile_calls["strikes"], smile_calls["ivs"]))
    put_iv_map = dict(zip(smile_puts["strikes"], smile_puts["ivs"]))
    call_ivs_aligned = np.array([call_iv_map.get(float(k), np.nan) for k in strikes])
    put_ivs_aligned = np.array([put_iv_map.get(float(k), np.nan) for k in strikes])
    valid_mask = ~(np.isnan(call_ivs_aligned) & np.isnan(put_ivs_aligned))
    if valid_mask.sum() >= 5:
        print()
        gex_result = find_gamma_flip(
            strikes[valid_mask],
            np.nan_to_num(call_oi[valid_mask]), np.nan_to_num(call_ivs_aligned[valid_mask]),
            np.nan_to_num(put_oi_aligned[valid_mask]), np.nan_to_num(put_ivs_aligned[valid_mask]),
            T=T, r=risk_free_rate, spot=spot,
        )
        print_gamma_flip(gex_result, spot=spot)

    # --- 波动率微笑/Gamma 已经算完了，但用户要求不再生成图片，这里不画图 ---


# ----------------------------------------------------------------------
# 11. Breeden-Litzenberger 算法 —— 从期权价格反推市场隐含的概率分布
#     （1978年发表，学术界和做市商公认的方法，不是营销话术）
#
#     核心原理：Call 期权价格对行权价的二阶偏导数，等于标的资产到期时
#     价格落在该行权价的风险中性概率密度。
#         f(K) = e^{rT} · d²C/dK²
#
#     ⚠️ 重要边界条件（必须先读）：
#     1. 这算出来的是「风险中性测度」下的概率分布，不是「真实世界」的
#        涨跌概率——两者之间理论上差着一个风险溢价，学界对这层怎么换算
#        没有统一定论。业界通常直接把它当作真实概率的近似来用，但严格
#        来说这是一个简化。
#     2. 真实报价有买卖价差、噪音、过期未成交的滞后报价（我们在 MU 那份
#        数据里就撞见过：远端行权价用的是几天前的旧成交价，反推出 IV
#        高达 200%+，完全不可信）。对这种数据直接做二阶导数，噪声会被
#        放大到不成形——所以必须先用平滑样条拟合，而不是死板插值。
#     3. 只对流动性好、报价是同一时间段的行权价区间可信；远端稀疏、
#        过期的报价段算出来的概率会是垃圾，必须过滤掉。
# ----------------------------------------------------------------------

def breeden_litzenberger_pdf(
    strikes, call_prices, r: float, T: float,
    num_points: int = 300, smoothing: Optional[float] = None,
):
    """
    从一组 (行权价, Call价格) 反推隐含概率密度函数。

    strikes, call_prices: 等长数组，同一到期日、尽量是同一时间段的报价
    r, T: 无风险利率、到期时间（年化）
    smoothing: 传给 UnivariateSpline 的平滑系数。
        None（默认）会用 len(strikes) 自动估一个比较保守的平滑度；
        真实市场报价噪音大，几乎总是需要平滑，不建议设成 0（死板插值）。

    返回: (K_grid, pdf) —— K_grid 是细分后的行权价网格，pdf 是对应的概率密度
    （已经归一化，对 K_grid 积分等于 1）。
    """
    from scipy.interpolate import UnivariateSpline

    strikes = np.asarray(strikes, dtype=float)
    call_prices = np.asarray(call_prices, dtype=float)

    order = np.argsort(strikes)
    K, C = strikes[order], call_prices[order]
    K, uniq_idx = np.unique(K, return_index=True)
    C = C[uniq_idx]

    if len(K) < 5:
        raise ValueError("有效行权价太少（<5个），没法可靠地拟合曲线做二阶求导")

    if smoothing is None:
        # 保守的自动平滑度：点数越多，允许的平滑度越大，压住噪声
        smoothing = len(K) * (np.std(C) * 0.05) ** 2

    spline = UnivariateSpline(K, C, k=4, s=smoothing)  # k=4 保证二阶导数连续光滑

    K_grid = np.linspace(K.min(), K.max(), num_points)
    d2C = spline.derivative(n=2)(K_grid)

    pdf = np.exp(r * T) * d2C
    pdf = np.clip(pdf, 0, None)  # 概率密度不能为负，噪声导致的负值截断为0

    _trapz = getattr(np, "trapezoid", None) or np.trapz  # numpy 2.x 把 trapz 改名成了 trapezoid
    area = _trapz(pdf, K_grid)
    if area <= 0:
        raise ValueError("算出来的概率密度全是0或负数，数据质量太差，换个更可信的行权价区间试试")
    pdf = pdf / area  # 归一化，确保积分=1

    return K_grid, pdf


def bl_probability_between(K_grid, pdf, low: float, high: float) -> float:
    """在算好的概率密度上，积分算出「到期价格落在 [low, high] 区间」的概率"""
    mask = (K_grid >= low) & (K_grid <= high)
    if mask.sum() < 2:
        return 0.0
    _trapz = getattr(np, "trapezoid", None) or np.trapz
    return float(_trapz(pdf[mask], K_grid[mask]))


def plot_bl_distribution(
    K_grid, pdf, spot: float, save_path: str = "bl_distribution.png",
    mark_prices: Optional[dict] = None,
):
    """
    画出 Breeden-Litzenberger 反推出的概率密度曲线。
    mark_prices: 可选，{"标签": 价格} 的字典，会在图上标出竖线（比如 Max Pain、现价等）
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.fill_between(K_grid, pdf, alpha=0.25, color="#457B9D")
    ax.plot(K_grid, pdf, color="#1D3557", linewidth=1.5, label="Implied PDF (risk-neutral)")

    ax.axvline(spot, color="gray", linestyle="--", linewidth=1, label=f"Spot {spot:.1f}")
    if mark_prices:
        colors = ["#E63946", "#2A9D8F", "#F4A261", "#9B5DE5"]
        for i, (label, price) in enumerate(mark_prices.items()):
            ax.axvline(price, color=colors[i % len(colors)], linestyle=":", linewidth=1.5,
                       label=f"{label} {price:.1f}")

    ax.set_xlabel("Price at Expiration")
    ax.set_ylabel("Probability Density")
    ax.set_title("Breeden-Litzenberger Implied Probability Distribution")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.close(fig)
    return save_path


def itm_oi_breakdown(strikes, call_oi, put_oi, spot: float) -> dict:
    """
    统计『现在处于价内（如果此刻结算就赚钱）』的 Call OI 和 Put OI 各有多少张，
    以及价内浮盈的名义总额——把「赢面多的一方会护盘」这套说法量化成一个
    可以对比的统计量。

    ⚠️ 先说清楚这套统计的局限，免得被数字误导：
    1. 这不是一个有严谨学术/实证支撑的定价模型，只是把你的假设量化出来，
       方便你看数字判断，不代表这套逻辑本身被证明有效。
    2. 大部分期权持有者（尤其散户）没有直接推动正股价格的资金量级，
       「赢的人会去买正股护盘」这套因果链条本身就存疑。
    3. 真正对正股价格有系统性影响力的，是做市商为了保持风险中性做的
       Delta/Gamma 对冲——这个我们在 net_gamma_exposure() 里已经算过，
       是学界和业界更认可的机制，跟这里的「持有人主观护盘」是两回事。
    4. OI 数据本身是「上一次收盘后」的快照，不是实时的，开盘后价格一变，
       原来价内/价外的划分马上就可能反过来。

    strikes/call_oi/put_oi: 等长数组（同一到期日）
    spot: 当前现价

    返回 dict，包含价内/价外 Call/Put OI 张数、价内浮盈名义总额、
    以及 call_win_share（价内 OI 里 Call 占比，>50% 说明价内的是多方 OI 更多）。
    """
    strikes = np.asarray(strikes, dtype=float)
    call_oi = np.asarray(call_oi, dtype=float)
    put_oi = np.asarray(put_oi, dtype=float)

    itm_call_mask = strikes < spot   # Call 价内：行权价 < 现价
    itm_put_mask = strikes > spot    # Put 价内：行权价 > 现价

    itm_call_oi = float(call_oi[itm_call_mask].sum())
    otm_call_oi = float(call_oi[~itm_call_mask].sum())
    itm_put_oi = float(put_oi[itm_put_mask].sum())
    otm_put_oi = float(put_oi[~itm_put_mask].sum())

    # 不只看张数，也算「张数 × 每张的内在价值」——离行权价越远、浮盈越大，
    # 这个数字更能反映「这批人现在赢了多少钱、护盘的动力有多强」
    itm_call_value = float(np.sum(np.maximum(0, spot - strikes[itm_call_mask]) * call_oi[itm_call_mask]))
    itm_put_value = float(np.sum(np.maximum(0, strikes[itm_put_mask] - spot) * put_oi[itm_put_mask]))

    total_itm_oi = itm_call_oi + itm_put_oi
    call_win_share = itm_call_oi / total_itm_oi if total_itm_oi else None

    return {
        "itm_call_oi": itm_call_oi, "otm_call_oi": otm_call_oi,
        "itm_put_oi": itm_put_oi, "otm_put_oi": otm_put_oi,
        "itm_call_value": itm_call_value, "itm_put_value": itm_put_value,
        "call_win_share": call_win_share,
    }


def print_itm_oi_breakdown(breakdown: dict, spot: float):
    """把 itm_oi_breakdown() 的结果打印成人话，不产生任何图片"""
    b = breakdown
    print(f"以现价 {spot:.2f} 为基准，目前价内（如果此刻结算能赚钱）的持仓：")
    print(f"  价内 Call OI（多方赢面）: {b['itm_call_oi']:,.0f} 张   "
          f"名义浮盈总额: {b['itm_call_value']:,.0f}（未乘合约乘数100）")
    print(f"  价内 Put  OI（空方赢面）: {b['itm_put_oi']:,.0f} 张   "
          f"名义浮盈总额: {b['itm_put_value']:,.0f}（未乘合约乘数100）")
    if b["call_win_share"] is not None:
        print(f"  价内 OI 中 Call 占比: {b['call_win_share']:.1%}"
              f"（{'多方赢面 OI 更多' if b['call_win_share'] > 0.5 else '空方赢面 OI 更多'}）")
    print(f"  （对照：价外 Call OI={b['otm_call_oi']:,.0f}张, "
          f"价外 Put OI={b['otm_put_oi']:,.0f}张——这些人现在还没赚钱）")


def itm_oi_by_strike(strikes, call_oi, put_oi, spot: float) -> dict:
    """
    把「价内」的 Call/Put OI 按行权价拆开、排序，方便直接看出
    「赢面 OI 集中在哪个行权价最多、哪个最少」。

    返回 {"itm_calls_by_oi": [(strike, oi), ...], "itm_puts_by_oi": [(strike, oi), ...]}，
    两个列表都按 OI 从高到低排好序（第一个是最多的，最后一个是最少的）。
    """
    strikes = np.asarray(strikes, dtype=float)
    call_oi = np.asarray(call_oi, dtype=float)
    put_oi = np.asarray(put_oi, dtype=float)

    itm_calls = [(float(k), float(oi)) for k, oi in zip(strikes, call_oi) if k < spot and oi > 0]
    itm_puts = [(float(k), float(oi)) for k, oi in zip(strikes, put_oi) if k > spot and oi > 0]

    itm_calls.sort(key=lambda x: x[1], reverse=True)
    itm_puts.sort(key=lambda x: x[1], reverse=True)

    return {"itm_calls_by_oi": itm_calls, "itm_puts_by_oi": itm_puts}


def print_itm_oi_extremes(detail: dict, top_n: int = 3):
    """打印价内 Call/Put 里 OI 最多、最少的几个行权价，不产生图片"""
    calls = detail["itm_calls_by_oi"]
    puts = detail["itm_puts_by_oi"]

    if calls:
        print(f"价内 Call：OI 最多的行权价 = {calls[0][0]:.0f}（{calls[0][1]:,.0f} 张）"
              f"   OI 最少的行权价 = {calls[-1][0]:.0f}（{calls[-1][1]:,.0f} 张）")
        if len(calls) > 2 * top_n:
            print(f"  Call OI 从多到少前 {top_n}: " +
                  ", ".join(f"{k:.0f}({oi:,.0f}张)" for k, oi in calls[:top_n]))
            print(f"  Call OI 从少到多前 {top_n}: " +
                  ", ".join(f"{k:.0f}({oi:,.0f}张)" for k, oi in calls[-top_n:][::-1]))
    else:
        print("价内 Call：没有找到 OI>0 的价内行权价")

    if puts:
        print(f"价内 Put ：OI 最多的行权价 = {puts[0][0]:.0f}（{puts[0][1]:,.0f} 张）"
              f"   OI 最少的行权价 = {puts[-1][0]:.0f}（{puts[-1][1]:,.0f} 张）")
        if len(puts) > 2 * top_n:
            print(f"  Put  OI 从多到少前 {top_n}: " +
                  ", ".join(f"{k:.0f}({oi:,.0f}张)" for k, oi in puts[:top_n]))
            print(f"  Put  OI 从少到多前 {top_n}: " +
                  ", ".join(f"{k:.0f}({oi:,.0f}张)" for k, oi in puts[-top_n:][::-1]))
    else:
        print("价内 Put ：没有找到 OI>0 的价内行权价")


# ----------------------------------------------------------------------
# 11b. 给前端用：把上面这一整套分析（IV/预期区间/Max Pain/诊断/价内OI/
#      波动率微笑/GEX/Gamma Flip/Breeden-Litzenberger）跑一遍，返回结构化
#      dict（不打印文字，数组都转成 JSON 能直接序列化的 list），供 API 调用。
#      数据源固定用 yahooquery（yfinance 的 openInterest 有已知 bug，见第8节）。
# ----------------------------------------------------------------------

def _reject_smile_outliers(smile: dict, low_mult: float = 0.35, high_mult: float = 2.5) -> dict:
    """
    过滤波动率微笑里的离谱毛刺点。以整条链 IV 的中位数为基准（中位数对个别极端
    值不敏感，比均值/标准差稳健），只保留落在 [中位数*low_mult, 中位数*high_mult]
    区间内的点——把旧成交/稀疏流动性导致的假 IV 毛刺跟真实的平滑微笑形状分开。
    样本太少（<5个点）时不过滤，避免把本来就没几个点的稀疏链清空。
    """
    ivs = smile.get("ivs") or []
    if len(ivs) < 5:
        return smile
    sorted_ivs = sorted(ivs)
    n = len(sorted_ivs)
    median = sorted_ivs[n // 2] if n % 2 else (sorted_ivs[n // 2 - 1] + sorted_ivs[n // 2]) / 2
    if median <= 0:
        return smile
    lo, hi = median * low_mult, median * high_mult
    strikes, kept_ivs = [], []
    for k, iv in zip(smile["strikes"], ivs):
        if lo <= iv <= hi:
            strikes.append(k)
            kept_ivs.append(iv)
    return {"strikes": strikes, "ivs": kept_ivs}


def analyze_option_chain(
    symbol: str, expiration: Optional[str] = None, risk_free_rate: float = 0.045,
) -> dict:
    """
    跟 demo_with_real_data_yq() 走的是同一套分析逻辑，区别只是这个函数不打印，
    把所有中间结果收集成一个 JSON-friendly 的 dict 返回，给 /api/options 这个
    路由用，前端页面直接拿这个 dict 渲染表格和图表。

    expiration: 不传就用最近到期日；传了就用这个到期日（必须是 expirations() 里
    返回的合法值之一，否则退回最近到期日并在 warnings 里说明）。
    """
    from yahooquery import Ticker as YQTicker
    import pandas as pd
    from datetime import datetime

    ticker_symbol = (symbol or "").strip().upper()
    if not ticker_symbol:
        raise ValueError("请输入股票代码")

    warnings: list[str] = []
    source = 'yahooquery'
    source_asof = None
    yq = YQTicker(ticker_symbol)

    price_info = yq.price
    if not isinstance(price_info, dict) or ticker_symbol not in price_info:
        raise ValueError(f"拿不到 {ticker_symbol} 的现价，数据源返回：{price_info}")
    spot_info = price_info[ticker_symbol]
    spot = spot_info.get("regularMarketPrice") or spot_info.get("postMarketPrice")
    if not spot:
        raise ValueError(f"{ticker_symbol} 现价字段为空")
    spot = float(spot)

    chain = yq.option_chain
    if not isinstance(chain, pd.DataFrame) or chain.empty:
        raise ValueError(f"未找到 {ticker_symbol} 的期权链数据（可能不是可交易期权的标的）")

    chain = chain.reset_index()
    exp_col = "expiration" if "expiration" in chain.columns else "expiration_date"
    type_col = "optionType" if "optionType" in chain.columns else "option_type"

    all_expirations = sorted(chain[exp_col].unique())
    if not all_expirations:
        raise ValueError(f"{ticker_symbol} 没有可用的期权到期日")
    expirations_str = [pd.Timestamp(e).strftime("%Y-%m-%d") for e in all_expirations]

    exp_date = all_expirations[0]
    if expiration:
        matched = [e for e in all_expirations if pd.Timestamp(e).strftime("%Y-%m-%d") == expiration]
        if matched:
            exp_date = matched[0]
        else:
            warnings.append(f"传入的到期日 {expiration} 不在可选范围内，已改用最近到期日")

    exp_chain = chain[chain[exp_col] == exp_date]
    calls = exp_chain[exp_chain[type_col] == "calls"].reset_index(drop=True).copy()
    puts = exp_chain[exp_chain[type_col] == "puts"].reset_index(drop=True).copy()
    if calls.empty or puts.empty:
        raise ValueError(f"最近到期日的 Call/Put 数据不完整（Call {len(calls)} 条, Put {len(puts)} 条）")

    exp_timestamp = pd.Timestamp(exp_date)
    exp_date_str = exp_timestamp.strftime("%Y-%m-%d")
    # Yahoo may return an otherwise complete chain with zero OI for every contract.
    # Replace the entire selected expiry (including spot) rather than mixing providers.
    yahoo_oi = sum(pd.to_numeric(frame.get('openInterest', pd.Series(dtype=float)), errors='coerce').fillna(0).sum() for frame in (calls, puts))
    if yahoo_oi < 10:
        try:
            from app.services.option_chain_fallback import fetch_cboe_chain, cboe_expiry
            calls, puts, spot, source_asof = cboe_expiry(fetch_cboe_chain(ticker_symbol), ticker_symbol, exp_date_str)
            source = 'Cboe delayed'
            warnings.append('Yahoo 未提供有效持仓，已切换到同一到期日的 Cboe 延迟期权链；报价、持仓与标的现价均来自该快照。')
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning('Cboe fallback failed for %s: %s', ticker_symbol, exc)
            warnings.append('Yahoo 持仓无效，备用源暂不可用或该到期日持仓不足。')
    days_to_exp = max((exp_timestamp.date() - datetime.now().date()).days, 1)
    T = days_to_exp / 365

    # --- 平值 IV + 隐含区间 ---
    atm_call = calls.iloc[(calls["strike"] - spot).abs().argsort()[:1]]
    atm_put = puts.iloc[(puts["strike"] - spot).abs().argsort()[:1]]

    call_iv = put_iv = None
    if not atm_call.empty:
        row = atm_call.iloc[0]
        price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
        call_iv = implied_volatility(
            market_price=price, S=spot, K=row["strike"], T=T, r=risk_free_rate, option_type="call",
        ) if price else None
    if not atm_put.empty:
        row = atm_put.iloc[0]
        price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
        put_iv = implied_volatility(
            market_price=price, S=spot, K=row["strike"], T=T, r=risk_free_rate, option_type="put",
        ) if price else None

    iv_info = None
    expected_range = None
    if call_iv and put_iv:
        rng = todays_expected_range_skewed(spot, call_iv, put_iv, days=days_to_exp, trading_days_per_year=365)
        skew_note = "市场对下跌保护需求更高" if rng["skew_pct"] > 0.02 else (
            "市场对上涨追逐更明显" if rng["skew_pct"] < -0.02 else "上下基本对称"
        )
        iv_info = {"call_iv": call_iv, "put_iv": put_iv, "skew_pct": rng["skew_pct"], "skew_note": skew_note}
        expected_range = rng
    elif call_iv:
        rng = todays_expected_range(spot, call_iv, days=days_to_exp, trading_days_per_year=365)
        iv_info = {"call_iv": call_iv, "put_iv": None, "skew_pct": None, "skew_note": None}
        expected_range = rng
    else:
        warnings.append("平值期权 IV 反推失败（可能是 bid/ask/lastPrice 都缺失），预期区间不可用")

    # --- Max Pain + 诊断 ---
    # Union prevents dropping put-only strikes from totals and calculations.
    strikes = np.union1d(calls["strike"].values, puts["strike"].values)
    call_oi = calls.groupby("strike")["openInterest"].sum().reindex(strikes, fill_value=0).to_numpy(dtype=float)
    put_oi_aligned = puts.groupby("strike")["openInterest"].sum().reindex(strikes, fill_value=0).to_numpy(dtype=float)
    diag = diagnose_max_pain_outliers(strikes, call_oi, put_oi_aligned, spot=spot, top_n=5)
    oi_thin = diag["total_call_oi"] + diag["total_put_oi"] < 10
    mp_strike, max_pain_curve = None, []
    if not oi_thin:
        mp_strike, losses = max_pain(strikes, call_oi, put_oi_aligned, spot=spot, strike_range_pct=0.25)
        max_pain_curve = [{"strike": k, "loss": losses[k]} for k in sorted(losses)]
    else:
        warnings.append("当前到期日未取得有效持仓，Max Pain、持仓分布与 Gamma 不可用；零值不代表实际没有持仓。")

    itm_breakdown = itm_breakdown_extremes = None
    if not oi_thin:
        itm_breakdown = itm_oi_breakdown(strikes, call_oi, put_oi_aligned, spot=spot)
        itm_detail = itm_oi_by_strike(strikes, call_oi, put_oi_aligned, spot=spot)
        itm_breakdown_extremes = {
            "calls": [{"strike": k, "oi": oi} for k, oi in itm_detail["itm_calls_by_oi"][:5]],
            "puts": [{"strike": k, "oi": oi} for k, oi in itm_detail["itm_puts_by_oi"][:5]],
        }

    # --- 波动率微笑 ---
    # yahooquery 给的 bid/ask 经常整条链都是 0（不是个别行权价的问题，是这个数据源
    # 本身盘中大部分时间就不返回实时买卖价），所以不能拿「有没有 bid/ask」来判断
    # 一个行权价可不可信——那样会把整条链全部过滤空。真正的噪声来源有两个：
    # 1) 深度实值/虚值、成交稀疏的行权价，用的是一两笔很久以前的旧成交价；
    # 2) 越临近到期(T越小)，期权价格对行权价的敏感度越高，同样的报价误差反推出
    #    的 IV 误差会被放大很多倍——1天到期的期权，离现价稍微远一点的行权价
    #    IV 就能跳到几百%，不是数据错了，是这个反推方法在这个区间数值上本来
    #    就不稳定。所以：a) 到期天数越少，只看离现价越近的行权价（用 sqrt(T)
    #    缩放跟本文件其它地方对 implied_move 的处理方式一致）；b) 反推完之后
    #    再按中位数做一次统计过滤，把剩下的毛刺点丢掉。
    smile_range_pct = min(0.35, max(0.06, 0.35 * math.sqrt(days_to_exp / 30)))
    smile_calls_src = calls[(calls["strike"] >= spot * (1 - smile_range_pct)) & (calls["strike"] <= spot * (1 + smile_range_pct))]
    smile_puts_src = puts[(puts["strike"] >= spot * (1 - smile_range_pct)) & (puts["strike"] <= spot * (1 + smile_range_pct))]
    if len(smile_calls_src) < len(calls) or len(smile_puts_src) < len(puts):
        warnings.append(
            f"波动率微笑只显示现价 ±{smile_range_pct:.0%} 范围内的行权价"
            f"（到期只剩 {days_to_exp} 天，太远的行权价 IV 反推数值上不稳定，容易失真）"
        )
    smile_calls_raw = volatility_smile_scan(
        strikes=smile_calls_src["strike"].values, bids=smile_calls_src["bid"].fillna(0).values,
        asks=smile_calls_src["ask"].fillna(0).values, lasts=smile_calls_src["lastPrice"].fillna(0).values,
        spot=spot, T=T, r=risk_free_rate, option_type="call",
    )
    smile_puts_raw = volatility_smile_scan(
        strikes=smile_puts_src["strike"].values, bids=smile_puts_src["bid"].fillna(0).values,
        asks=smile_puts_src["ask"].fillna(0).values, lasts=smile_puts_src["lastPrice"].fillna(0).values,
        spot=spot, T=T, r=risk_free_rate, option_type="put",
    )
    smile_calls = _reject_smile_outliers(smile_calls_raw)
    smile_puts = _reject_smile_outliers(smile_puts_raw)
    dropped = (len(smile_calls_raw["strikes"]) - len(smile_calls["strikes"])) + \
        (len(smile_puts_raw["strikes"]) - len(smile_puts["strikes"]))
    if dropped:
        warnings.append(f"波动率微笑已剔除 {dropped} 个偏离中位数太远的毛刺点（多半是旧成交/稀疏流动性导致的假IV）")

    # --- Gamma 分布 ---
    gamma_by_strike: dict[float, float] = {}
    call_iv_map = dict(zip(smile_calls["strikes"], smile_calls["ivs"]))
    put_iv_map = dict(zip(smile_puts["strikes"], smile_puts["ivs"]))
    for K, oi in zip(strikes, call_oi):
        iv_k = call_iv_map.get(float(K))
        if iv_k and oi:
            g = bs_greeks(BSInputs(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv_k, option_type="call"))["gamma"]
            gamma_by_strike[float(K)] = gamma_by_strike.get(float(K), 0) + g * oi
    for K, oi in zip(strikes, put_oi_aligned):
        iv_k = put_iv_map.get(float(K))
        if iv_k and oi:
            g = bs_greeks(BSInputs(S=spot, K=K, T=T, r=risk_free_rate, sigma=iv_k, option_type="put"))["gamma"]
            gamma_by_strike[float(K)] = gamma_by_strike.get(float(K), 0) - g * oi
    gamma_series = [{"strike": k, "gamma_exposure": gamma_by_strike[k]} for k in sorted(gamma_by_strike)]

    # --- Gamma Flip ---
    gamma_flip = None
    call_ivs_aligned = np.array([call_iv_map.get(float(k), np.nan) for k in strikes])
    put_ivs_aligned = np.array([put_iv_map.get(float(k), np.nan) for k in strikes])
    valid_mask = ~(np.isnan(call_ivs_aligned) & np.isnan(put_ivs_aligned))
    if not oi_thin and valid_mask.sum() >= 5:
        gex_result = find_gamma_flip(
            strikes[valid_mask],
            np.nan_to_num(call_oi[valid_mask]), np.nan_to_num(call_ivs_aligned[valid_mask]),
            np.nan_to_num(put_oi_aligned[valid_mask]), np.nan_to_num(put_ivs_aligned[valid_mask]),
            T=T, r=risk_free_rate, spot=spot,
        )
        gamma_flip = {
            "gex_at_spot": gex_result["gex_at_spot"],
            "regime": gex_result["regime"],
            "flip_points": gex_result["flip_points"],
            "curve": [{"price": float(p), "gex": float(g)} for p, g in zip(gex_result["grid"], gex_result["gex_curve"])],
        }
    else:
        warnings.append("有效IV的行权价太少，Gamma Flip 分析跳过")

    # --- Breeden-Litzenberger 隐含概率分布 ---
    bl_result = None
    try:
        bl_strikes, bl_prices = [], []
        for _, row in calls.iterrows():
            price = mid_price(row.get("bid"), row.get("ask"), row.get("lastPrice"))
            if price is not None and price > 0:
                bl_strikes.append(float(row["strike"]))
                bl_prices.append(float(price))
        K_grid, pdf = breeden_litzenberger_pdf(bl_strikes, bl_prices, r=risk_free_rate, T=T)
        prob_below = bl_probability_between(K_grid, pdf, float(K_grid.min()), spot)
        prob_above = bl_probability_between(K_grid, pdf, spot, float(K_grid.max()))
        bl_result = {
            "grid": [float(x) for x in K_grid],
            "pdf": [float(x) for x in pdf],
            "prob_below_spot": prob_below,
            "prob_above_spot": prob_above,
        }
    except Exception as exc:
        warnings.append(f"Breeden-Litzenberger 概率分布反推失败：{exc}")

    return {
        "symbol": ticker_symbol,
        "source": source,
        "source_asof": source_asof,
        "spot": spot,
        "expiration": exp_date_str,
        "expirations": expirations_str,
        "days_to_exp": days_to_exp,
        "risk_free_rate": risk_free_rate,
        "iv": iv_info,
        "expected_range": expected_range,
        "max_pain": {"strike": mp_strike, "curve": max_pain_curve},
        "diagnostics": {
            "total_call_oi": diag["total_call_oi"],
            "total_put_oi": diag["total_put_oi"],
            "thin": oi_thin,
            "top_outliers": diag["top"],
        },
        "itm_breakdown": itm_breakdown,
        "itm_extremes": itm_breakdown_extremes,
        "volatility_smile": {"calls": smile_calls, "puts": smile_puts},
        "gamma_by_strike": gamma_series,
        "gamma_flip": gamma_flip,
        "breeden_litzenberger": bl_result,
        "warnings": warnings,
    }


# ----------------------------------------------------------------------
# 12. 每日快照记录 + 事后验证 —— 把「价内OI能不能预测涨跌」这个假设
#     变成一个可以被数据反驳的、真正可检验的问题，而不是拍脑袋假设它成立
#
#     用法：
#     1. 每天收盘前，调 log_daily_snapshot() 记一笔当天的现价/隐含区间/
#        价内OI分布/Max Pain，存到本地 CSV，天天记、越攒越多。
#     2. 第二天知道了实际收盘价之后，调 update_actual_close() 把那天
#        记录里的「实际结果」补上。
#     3. 攒够至少 20~30 个交易日之后，调 analyze_track_accuracy()，
#        用真实历史检验这套「价内OI多的一方会赢」的说法到底有没有用——
#        不是我们嘴上说有没有用，是拿数据自己说话。
# ----------------------------------------------------------------------

_SNAPSHOT_COLUMNS = [
    "date", "ticker", "spot", "expected_low", "expected_high",
    "itm_call_oi", "itm_put_oi", "call_win_share", "max_pain",
    "actual_close", "actual_direction", "range_hit", "call_side_won",
]


def log_daily_snapshot(
    csv_path: str, date: str, ticker: str, spot: float,
    expected_low: float, expected_high: float,
    itm_call_oi: float, itm_put_oi: float, max_pain: float,
):
    """
    记录当天的一笔快照到本地 CSV（没有就新建，有就追加）。
    date 用 'YYYY-MM-DD' 格式，同一天同一个ticker重复记录会追加新行，
    不会自动去重，去重逻辑留给你自己在分析时处理。
    """
    import csv
    import os

    call_win_share = itm_call_oi / (itm_call_oi + itm_put_oi) if (itm_call_oi + itm_put_oi) else None
    row = {
        "date": date, "ticker": ticker, "spot": spot,
        "expected_low": expected_low, "expected_high": expected_high,
        "itm_call_oi": itm_call_oi, "itm_put_oi": itm_put_oi,
        "call_win_share": call_win_share, "max_pain": max_pain,
        "actual_close": "", "actual_direction": "", "range_hit": "", "call_side_won": "",
    }

    file_exists = os.path.isfile(csv_path)
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_SNAPSHOT_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
    print(f"已记录 {date} {ticker} 的快照到 {csv_path}")


def update_actual_close(csv_path: str, date: str, ticker: str, actual_close: float):
    """
    补上某一天的实际收盘价，自动算出：
    - actual_direction: 相对开盘时现价是涨是跌
    - range_hit: 实际收盘价有没有落在当时预测的隐含区间内
    - call_side_won: 实际收盘价有没有落在「价内Call更多」预示的方向上
      （call_win_share>50% 时预示涨，实际收盘>spot 才算「猜对方向」）
    """
    import csv

    rows = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r["date"] == date and r["ticker"] == ticker and not r["actual_close"]:
                spot = float(r["spot"])
                low, high = float(r["expected_low"]), float(r["expected_high"])
                r["actual_close"] = actual_close
                r["actual_direction"] = "up" if actual_close > spot else "down"
                r["range_hit"] = "yes" if low <= actual_close <= high else "no"
                if r["call_win_share"]:
                    predicted_up = float(r["call_win_share"]) > 0.5
                    actual_up = actual_close > spot
                    r["call_side_won"] = "yes" if predicted_up == actual_up else "no"
            rows.append(r)

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_SNAPSHOT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"已更新 {date} {ticker} 的实际收盘价: {actual_close}")


def analyze_track_accuracy(csv_path: str, ticker: Optional[str] = None):
    """
    读取历史快照，统计两件事的真实命中率（不是理论，是拿数据验证）：
    1. 隐含区间命中率：实际收盘价落在 expected_low~expected_high 的比例
       —— 理论上如果 IV 定价合理，这个比例应该接近区间对应的置信度
          （比如 68% 置信区间，长期命中率应该接近 68%，明显偏离说明
          IV 系统性定价过高或过低）。
    2. 「价内OI多的一方」猜对方向的命中率 —— 如果这套说法真的有效，
       长期命中率应该显著高于 50%（纯随机）；如果长期就在 50% 附近晃，
       说明这个信号没有预测力，只是噪音。
    """
    import csv as csv_module

    with open(csv_path) as f:
        rows = list(csv_module.DictReader(f))

    if ticker:
        rows = [r for r in rows if r["ticker"] == ticker]
    rows = [r for r in rows if r["actual_close"]]  # 只统计已经补上实际结果的记录

    n = len(rows)
    if n == 0:
        print("还没有任何已补全实际收盘价的记录，先用 update_actual_close() 补数据")
        return

    range_hits = sum(1 for r in rows if r["range_hit"] == "yes")
    call_side_results = [r for r in rows if r["call_side_won"]]
    call_side_wins = sum(1 for r in call_side_results if r["call_side_won"] == "yes")

    print(f"样本数: {n} 个交易日" + (f"（标的: {ticker}）" if ticker else "（全部标的合并统计）"))
    print(f"隐含区间命中率: {range_hits}/{n} = {range_hits/n:.1%}"
          f"  （如果 IV 定价合理，应该接近你设定的置信度，比如 68%）")

    if call_side_results:
        m = len(call_side_results)
        print(f"「价内OI多的一方」猜对方向命中率: {call_side_wins}/{m} = {call_side_wins/m:.1%}"
              f"  （50%是纯随机基准，只有显著高于50%才说明这个信号真的有效）")
        if m < 30:
            print(f"  ⚠️ 样本量只有 {m} 天，统计噪音很大，随便一两次运气好坏就能让命中率\n"
                  f"     摆动 ±10~20 个百分点，至少攒够 30~50 个交易日再下结论。")
    else:
        print("没有足够的价内OI数据来统计方向命中率")


if __name__ == "__main__":
    # 单独测试 BS + IV 反推的最小示例
    example = BSInputs(S=220, K=215, T=3 / 365, r=0.045, sigma=0.55, option_type="call")
    theo_price = bs_price(example)
    print(f"理论期权价格: {theo_price:.2f}")
    print("Greeks:", bs_greeks(example))

    iv = implied_volatility(market_price=theo_price, S=220, K=215, T=3 / 365, r=0.045)
    print(f"反推出的 IV (应约等于 0.55): {iv:.4f}")

    print(f"隐含振幅 (3天): ±{implied_move(220, iv, 3, 365):.2f} 美元")

    # 如果联网环境可用，取消下面注释跑真实数据演示
    # demo_with_real_data("NVDA")                                          # yfinance（OI 有已知 bug）
    demo_with_real_data_yq("avgo")                                       # yahooquery（同样依赖 Yahoo 后端）
    # demo_with_real_data_tradier("NVDA", api_token="你的Tradier Token")      # 真实券商数据
    # demo_with_real_data_polygon("NVDA", api_key="你的Polygon API Key")     # 真实交易所数据