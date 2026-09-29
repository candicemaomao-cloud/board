"""
unified_strategy.py
======================
所有因子合并成一个文件：轨道通道 + 宏观(CPI/PCE/PPI/非农/PMI) + 新闻 + 财报 + 总调度。
输入股票代码，自动拉取行情，技术面因子自动计算，宏观/新闻/财报因子可选传入（不传则跳过，
权重自动分给其他已激活的类别），期权因子先占位，等你定好具体指标再补实现。

最简单用法（命令行直接运行，会提示你输入代码）：
    python3 unified_strategy.py

或者在代码里调用：
    from unified_strategy import analyze
    result = analyze("AAPL")
    print(result["evaluation"])

带宏观/新闻/财报数据的完整用法见文件底部 __main__ 部分的示例，或者直接参考各个字段说明。

依赖：numpy, yfinance（拉行情用，若未安装请先 pip install yfinance --break-system-packages）
本文件不构成投资建议，所有阈值/敏感系数都是经验默认值，需要你用自己的历史数据回测校准。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal
import math
import json
import os
from datetime import datetime, timezone
import numpy as np


def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _sigmoid(x: float) -> float:
    """
    数值稳定版sigmoid：1/(1+exp(-x))。
    直接写 math.exp(-x) 在x是很大的负数时会overflow(OverflowError: math range error)，
    这里分段处理，保证exp()的参数永远不会是大正数，不管x多大都不会崩溃。
    """
    if x >= 0:
        z = math.exp(-x)
        return 1 / (1 + z)
    else:
        z = math.exp(x)
        return z / (1 + z)


Category = Literal["beat", "miss", "inline"]
DirectionType = Literal["up", "down", "sideways"]


# ===========================================================================
# 1. 轨道：滚动线性回归通道
# ===========================================================================

class LinearRegressionChannel:
    """滚动窗口线性回归通道：中轴=最小二乘拟合趋势线，上下轨=中轴 ± n_std * 残差标准差"""

    def __init__(self, n_std: float = 2.0):
        self.n_std = n_std
        self.slope: Optional[float] = None
        self.intercept: Optional[float] = None
        self.std: Optional[float] = None
        self.n: int = 0

    def fit(self, prices) -> "LinearRegressionChannel":
        prices = np.asarray(prices, dtype=float)
        self.n = len(prices)
        if self.n < 2:
            raise ValueError("拟合通道至少需要2个价格点")
        x = np.arange(self.n)
        self.slope, self.intercept = np.polyfit(x, prices, 1)
        residuals = prices - (self.slope * x + self.intercept)
        self.std = float(np.std(residuals))
        return self

    def mid_at(self, x: float) -> float:
        if self.slope is None:
            raise RuntimeError("请先调用 fit()")
        return self.slope * x + self.intercept

    def bounds_at(self, x: float) -> Dict[str, float]:
        mid = self.mid_at(x)
        return {"upper": mid + self.n_std * self.std, "mid": mid, "lower": mid - self.n_std * self.std}

    def position_z(self, price: float, x: Optional[float] = None) -> float:
        if x is None:
            x = self.n - 1
        mid = self.mid_at(x)
        return 0.0 if self.std == 0 else (price - mid) / self.std

    def project(self, steps_ahead: int) -> List[Dict[str, float]]:
        start_x = self.n - 1
        return [self.bounds_at(start_x + i) for i in range(1, steps_ahead + 1)]


# ===========================================================================
# 2. 因子参与度引擎 + 综合策略
# ===========================================================================

@dataclass
class Factor:
    name: str
    weight: float
    value: float
    note: str = ""

    @property
    def contribution(self) -> float:
        return self.weight * self.value


class FactorEngine:
    """
    因子参与度引擎。

    重要说明（曾经的设计bug，这里记录下来避免以后重蹈覆辙）：
    早期版本里 direction 的判断标准是"参与度是否接近1"，这是照搬最初"正向因子权重和=1"的构想。
    但实际写每个具体因子（动量/宏观/新闻/财报/期权情绪……）时，全部统一用了
    "0.5=中性，1=极度看多，0=极度看空"这种对称打分设计——这意味着只要不是所有因子
    同时打出接近1.0的满分看多（现实中几乎不会发生），participation天然就会停留在0.5附近，
    而不是1附近。用"是否接近1"去判断"是否有方向"，会导致几乎任何输入都被误判成偏空。

    现在改成和 neutral_baseline（默认0.5，和所有因子的中性打分一致）比较：
        participation 明显高于 neutral_baseline → up
        participation 明显低于 neutral_baseline → down
        participation 接近 neutral_baseline      → sideways
    如果你以后设计的某个因子不是用0.5做中性基准，需要单独调整该因子的打分函数，
    让它也以0.5为中性中点，而不是又反过来改这里的neutral_baseline——保持全局统一的打分基准，
    才不会重新掉进同一个坑。
    """
    def __init__(self, factors: Optional[List[Factor]] = None, sideways_band: float = 0.05,
                 up_threshold: Optional[float] = None, neutral_baseline: float = 0.5):
        self.factors: List[Factor] = factors or []
        self.sideways_band = sideways_band
        self.up_threshold = up_threshold
        self.neutral_baseline = neutral_baseline

    def set_factors(self, factors: List[Factor]) -> None:
        self.factors = factors

    def total_weight(self) -> float:
        return sum(f.weight for f in self.factors)

    def participation(self) -> float:
        return sum(f.contribution for f in self.factors)

    def deficit(self) -> float:
        """neutral_baseline 与当前参与度的差值：正值=偏空驱动，负值=偏多驱动"""
        return self.neutral_baseline - self.participation()

    def signal(self) -> Dict:
        p = self.participation()
        deficit = self.neutral_baseline - p
        if abs(deficit) <= self.sideways_band:
            direction: DirectionType = "sideways"
        elif self.up_threshold is not None and p >= self.up_threshold:
            direction = "up"
        elif deficit > self.sideways_band:
            direction = "down"
        else:
            direction = "up"
        return {"participation": p, "deficit": deficit, "direction": direction,
                "total_weight_check": self.total_weight()}


class TrackFactorStrategy:
    def __init__(self, n_std: float = 2.0, step_k: float = 1.0, sideways_band: float = 0.05,
                 up_threshold: Optional[float] = None, neutral_baseline: float = 0.5):
        self.channel = LinearRegressionChannel(n_std=n_std)
        self.engine = FactorEngine(sideways_band=sideways_band, up_threshold=up_threshold,
                                   neutral_baseline=neutral_baseline)
        self.step_k = step_k
        self._last_price: Optional[float] = None

    def update_channel(self, prices) -> None:
        self.channel.fit(prices)
        self._last_price = float(np.asarray(prices)[-1])

    def update_factors(self, factors: List[Factor]) -> None:
        self.engine.set_factors(factors)

    def step_size(self) -> float:
        if self.channel.std is None:
            raise RuntimeError("请先调用 update_channel()")
        return abs(self.engine.deficit()) * self.step_k * self.channel.std

    def evaluate(self) -> Dict:
        sig = self.engine.signal()
        step = self.step_size()
        z = self.channel.position_z(self._last_price) if self._last_price is not None else None
        bounds = self.channel.bounds_at(self.channel.n - 1)
        return {**sig, "step_size": step, "current_price": self._last_price,
                "channel_position_z": z, "channel_bounds": bounds}

    def project_path(self, steps: int = 5) -> List[Dict]:
        eva = self.evaluate()
        direction = eva["direction"]
        step = eva["step_size"]
        price = self._last_price
        sign = {"up": 1, "down": -1, "sideways": 0}[direction]
        path = []
        channel_proj = self.channel.project(steps)
        for i in range(steps):
            price = price + sign * step
            bounds = channel_proj[i]
            path.append({"step": i + 1, "predicted_price": price, "channel_upper": bounds["upper"],
                         "channel_mid": bounds["mid"], "channel_lower": bounds["lower"]})
        return path


# ===========================================================================
# 3. 宏观因子：CPI / PCE / PPI / 非农 / PMI
# ===========================================================================

@dataclass
class MacroReleaseResult:
    indicator: str
    actual: float
    expected: float
    prior: Optional[float]
    surprise: float
    surprise_z: float
    category: Category
    value: float
    detail: str


class _BaseMacroFactor:
    indicator_name: str = "generic"
    polarity: int = 1
    inline_band: float = 0.1
    typical_surprise_scale: float = 0.2
    beat_sensitivity: float = 1.0
    miss_sensitivity: float = 1.2

    def compute(self, actual: float, expected: float, prior: Optional[float] = None) -> MacroReleaseResult:
        surprise = actual - expected
        z = surprise / self.typical_surprise_scale if self.typical_surprise_scale else 0.0
        eff = z * self.polarity

        if abs(surprise) <= self.inline_band:
            category: Category = "inline"
            value = 0.5 + _clip(eff, -1, 1) * 0.05
            detail = f"|surprise|={abs(surprise):.3f}<=inline_band={self.inline_band}，判定接近预期"
        elif eff > 0:
            category = "beat"
            magnitude = 1 - math.exp(-self.beat_sensitivity * abs(eff))
            value = 0.5 + 0.5 * magnitude
            detail = f"eff={eff:.3f}>0，判定偏多方向，magnitude={magnitude:.3f}"
        else:
            category = "miss"
            magnitude = 1 - math.exp(-self.miss_sensitivity * abs(eff))
            value = 0.5 - 0.5 * magnitude
            detail = f"eff={eff:.3f}<=0，判定偏空方向，magnitude={magnitude:.3f}"

        return MacroReleaseResult(indicator=self.indicator_name, actual=actual, expected=expected, prior=prior,
                                  surprise=surprise, surprise_z=z, category=category, value=_clip(value), detail=detail)


class CPIFactor(_BaseMacroFactor):
    indicator_name = "CPI"; polarity = -1; inline_band = 0.05; typical_surprise_scale = 0.10
    beat_sensitivity = 1.3; miss_sensitivity = 1.5


class PCEFactor(_BaseMacroFactor):
    indicator_name = "PCE"; polarity = -1; inline_band = 0.05; typical_surprise_scale = 0.08
    beat_sensitivity = 1.2; miss_sensitivity = 1.6


class PPIFactor(_BaseMacroFactor):
    indicator_name = "PPI"; polarity = -1; inline_band = 0.08; typical_surprise_scale = 0.15
    beat_sensitivity = 1.0; miss_sensitivity = 1.1


class NFPFactor(_BaseMacroFactor):
    """默认polarity=-1：当前regime下，非农强于预期→加息预期升温→偏空；温和转弱→偏多；断崖式恶化→衰退担忧压过降息利好→重新转偏空"""
    indicator_name = "NFP"; polarity = -1; inline_band = 15; typical_surprise_scale = 60
    beat_sensitivity = 1.1; miss_sensitivity = 1.3

    def compute_regime_aware(self, actual: float, expected: float, prior: Optional[float] = None,
                             recession_shock_threshold: float = -50, shock_value: float = 0.12) -> MacroReleaseResult:
        result = self.compute(actual, expected, prior)
        if actual <= recession_shock_threshold:
            result.category = "miss"
            result.value = _clip(shock_value)
            result.detail += f"；触及衰退冲击阈值{recession_shock_threshold}，强制判定偏空"
        return result


class PMIFactor(_BaseMacroFactor):
    indicator_name = "PMI"; polarity = 1; inline_band = 0.3; typical_surprise_scale = 1.0
    beat_sensitivity = 1.0; miss_sensitivity = 1.1

    def compute_with_level(self, actual: float, expected: float, prior: Optional[float] = None,
                           below_50_penalty: float = 0.1) -> MacroReleaseResult:
        result = self.compute(actual, expected, prior)
        if actual < 50:
            result.value = _clip(result.value - below_50_penalty)
            result.detail += f"；actual={actual}<50收缩区间，额外扣减{below_50_penalty}"
        return result


# ===========================================================================
# 3.5 央行货币政策因子：Fed / BOJ / BOK（可自行扩展其他央行）
# ===========================================================================

class _BaseCentralBankFactor:
    """
    央行利率决议因子基类。和宏观数据因子用同一套surprise框架(actual vs expected)，
    但这里比较的是"决议幅度"，单位是基点(bp)，比如加息25bp传 actual_bp=25。

    spillover_weight：该央行决议对"美股"的外溢强度系数。Fed对美股是直接定价，
    默认给1.0作为基准；其他央行是通过汇率/套息交易/供应链等间接渠道传导到美股，
    影响力通常小于（BOJ的杠杆平仓机制例外，可能更剧烈）或明显弱于Fed，
    这个系数是方向性的经验设定，不是精确校准值，需要你自己用历史事件验证。
    """
    bank_name: str = "generic"
    polarity: int = -1              # 默认：更鹰派(加息超预期) = 对美股偏空
    inline_band_bp: float = 5.0
    typical_surprise_scale_bp: float = 12.5
    beat_sensitivity: float = 1.0
    miss_sensitivity: float = 1.2
    spillover_weight: float = 1.0

    def compute(self, actual_bp: float, expected_bp: float) -> MacroReleaseResult:
        surprise = actual_bp - expected_bp
        z = surprise / self.typical_surprise_scale_bp if self.typical_surprise_scale_bp else 0.0
        eff = z * self.polarity

        if abs(surprise) <= self.inline_band_bp:
            category: Category = "inline"
            value = 0.5 + _clip(eff, -1, 1) * 0.05
            detail = f"{self.bank_name}决议surprise={surprise:.1f}bp<=inline_band={self.inline_band_bp}bp，符合预期"
        elif eff > 0:
            category = "beat"
            magnitude = 1 - math.exp(-self.beat_sensitivity * abs(eff))
            value = 0.5 + 0.5 * magnitude
            detail = f"{self.bank_name}决议eff={eff:.3f}>0，偏多方向，magnitude={magnitude:.3f}"
        else:
            category = "miss"
            magnitude = 1 - math.exp(-self.miss_sensitivity * abs(eff))
            value = 0.5 - 0.5 * magnitude
            detail = f"{self.bank_name}决议eff={eff:.3f}<=0，偏空方向，magnitude={magnitude:.3f}"

        return MacroReleaseResult(indicator=self.bank_name, actual=actual_bp, expected=expected_bp, prior=None,
                                  surprise=surprise, surprise_z=z, category=category, value=_clip(value), detail=detail)


class FedFactor(_BaseCentralBankFactor):
    """美联储。对美股最直接，不需要额外的外溢折算，spillover_weight固定为基准1.0"""
    bank_name = "Fed"; polarity = -1; inline_band_bp = 5.0; typical_surprise_scale_bp = 12.5
    beat_sensitivity = 1.0; miss_sensitivity = 1.1; spillover_weight = 1.0


class BOJFactor(_BaseCentralBankFactor):
    """
    日本央行。核心传导机制是"日元套息交易"(yen carry trade)：海外资金长期借入低成本日元、
    买入美股等高收益资产，一旦BOJ意外收紧(加息超预期)，日元走强会压缩套息交易的息差空间，
    触发杠杆仓位平仓，这个平仓压力会直接冲击美股——2024年8月那次全球risk-off抛售就是典型案例。
    所以对美股来说：BOJ加息超预期=偏空(可能比同等幅度的Fed决议冲击更剧烈，因为是通过
    "强制平仓"这个杠杆机制传导，不是单纯的估值折现逻辑)；BOJ维持宽松/降息=对套息交易友好=偏多。
    spillover_weight给1.2(比Fed更高)，体现杠杆平仓可能有放大效应，这个系数没有精确实证校准，
    是方向性设定，你需要自己用历史事件(比如2024年8月那次)回测验证是否合理。
    """
    bank_name = "BOJ"; polarity = -1; inline_band_bp = 5.0; typical_surprise_scale_bp = 10.0
    beat_sensitivity = 1.0; miss_sensitivity = 1.2; spillover_weight = 1.2


class BOKFactor(_BaseCentralBankFactor):
    """
    韩国央行。对美股的直接外溢效应比Fed/BOJ弱得多，更多是间接影响：韩国是全球半导体/科技
    供应链的重要一环(三星、SK海力士等)，BOK决议更多反映韩国自身通胀/增长状况，对美股的传导
    主要通过"科技股情绪"和"新兴市场风险偏好"，没有像BOJ那样明确的杠杆平仓机制。
    我对这条传导链的把握明显低于Fed和BOJ，spillover_weight只给0.3，这更多是一个方向性占位，
    不建议给它太高权重，除非你自己有更细致的验证。
    """
    bank_name = "BOK"; polarity = -1; inline_band_bp = 12.5; typical_surprise_scale_bp = 25.0
    beat_sensitivity = 1.0; miss_sensitivity = 1.0; spillover_weight = 0.3


# ===========================================================================
# 3.6 新闻标题快速分类辅助（极简关键词版，仅供快速草稿，不可靠，务必人工复核）
# ===========================================================================

_NEWS_KEYWORDS_SEVERE_NEG = ["fraud", "investigation", "recall", "bankruptcy", "plunge", "crash",
                            "lawsuit", "guidance cut", "delisting", "resigns amid", "sec probe",
                            "造假", "退市", "破产", "暴跌", "诉讼", "调查", "下调指引"]
_NEWS_KEYWORDS_VERY_POS = ["beats estimates", "record high", "raises guidance", "acquisition premium",
                          "blowout", "strong beat", "upgraded to buy", "超预期", "创历史新高",
                          "上调指引", "大幅超预期"]
_NEWS_KEYWORDS_MILD_NEG = ["miss", "downgrade", "delay", "concern", "cut", "below expectations",
                          "不及预期", "下调", "延迟", "担忧"]
_NEWS_KEYWORDS_MILD_POS = ["beat", "growth", "partnership", "approval", "expands", "上涨",
                          "增长", "合作", "获批", "扩张"]


def classify_news_headline(headline: str) -> Dict:
    """
    极简关键词打分器，把新闻标题粗略映射到五档(level)+impact_score，仅供快速生成草稿用。

    重要提示：这不是真正的NLP情感分析，只是字符串包含匹配。容易被反讽/否定句式坑，
    比如"公司否认财务造假传闻"会因为包含"造假"被误判成利空(实际是辟谣，可能是利好)，
    "分析师下调目标价但维持买入评级"这种混合信号也无法正确处理。
    只建议用于批量粗筛，重大新闻(可能进入news_inputs推动交易决策的)必须人工复核后再决定
    level和impact_score，不要直接把这个函数的输出当成最终结论使用。
    """
    text = headline.lower()

    if any(k in text for k in _NEWS_KEYWORDS_SEVERE_NEG):
        return {"level": "severe_negative", "impact_score": 0.8, "matched": "severe_negative关键词命中"}
    if any(k in text for k in _NEWS_KEYWORDS_VERY_POS):
        return {"level": "very_positive", "impact_score": 0.8, "matched": "very_positive关键词命中"}
    if any(k in text for k in _NEWS_KEYWORDS_MILD_NEG):
        return {"level": "mild_negative", "impact_score": 0.4, "matched": "mild_negative关键词命中"}
    if any(k in text for k in _NEWS_KEYWORDS_MILD_POS):
        return {"level": "positive", "impact_score": 0.4, "matched": "positive关键词命中"}
    return {"level": "neutral", "impact_score": 0.2, "matched": "无关键词命中，默认中性"}


# ===========================================================================
# 4. 新闻因子 + 财报因子
# ===========================================================================

NewsLevel = Literal["severe_negative", "mild_negative", "neutral", "positive", "very_positive"]
_NEWS_BASE_VALUE = {"severe_negative": 0.05, "mild_negative": 0.30, "neutral": 0.50, "positive": 0.70, "very_positive": 0.95}


@dataclass
class NewsFactorResult:
    level: NewsLevel; impact_score: float; days_since_news: int; value: float; detail: str


class NewsFactor:
    def __init__(self, decay_half_life: float = 3.0):
        self.decay_half_life = decay_half_life

    def compute(self, level: NewsLevel, impact_score: float = 1.0, days_since_news: int = 0) -> NewsFactorResult:
        impact_score = _clip(impact_score)
        base = _NEWS_BASE_VALUE[level]
        raw_value = 0.5 + (base - 0.5) * impact_score
        decay_factor = (0.5 ** (days_since_news / self.decay_half_life)) if self.decay_half_life > 0 else (1.0 if days_since_news == 0 else 0.0)
        value = 0.5 + (raw_value - 0.5) * decay_factor
        detail = f"level={level}(base={base}), impact_score={impact_score}, days_since_news={days_since_news}, decay_factor={decay_factor:.3f}"
        return NewsFactorResult(level=level, impact_score=impact_score, days_since_news=days_since_news,
                                value=_clip(value), detail=detail)


EarningsPhase = Literal["pre_earnings", "post_earnings", "inactive"]


@dataclass
class EarningsFactorResult:
    phase: EarningsPhase; value: Optional[float]; confidence: Optional[float]; detail: str


class EarningsFactor:
    def __init__(self, pre_window_days: int = 10, post_window_days: int = 10, runup_scale: float = 0.15,
                 extension_scale: float = 0.20, extension_threshold_z: float = 1.0):
        self.pre_window_days = pre_window_days
        self.post_window_days = post_window_days
        self.runup_scale = runup_scale
        self.extension_scale = extension_scale
        self.extension_threshold_z = extension_threshold_z

    def compute_pre_earnings(self, days_to_earnings: int, pre_runup_pct: float) -> EarningsFactorResult:
        if not (0 <= days_to_earnings <= self.pre_window_days):
            return EarningsFactorResult("inactive", None, None, f"days_to_earnings={days_to_earnings}不在窗口内，不激活")
        z = pre_runup_pct / self.runup_scale if self.runup_scale else 0.0
        confidence = _clip(math.exp(-abs(z)))
        tilt = -0.15 * math.tanh(z)
        value = _clip(0.5 + tilt)
        detail = f"pre_runup_pct={pre_runup_pct:.3f}, z={z:.2f}, tilt={tilt:.3f}, confidence={confidence:.3f}"
        return EarningsFactorResult("pre_earnings", value, confidence, detail)

    def compute_post_earnings(self, days_since_earnings: int, earnings_day_move_pct: float,
                              cumulative_move_since_pct: float, surprise_category: Optional[Category] = None) -> EarningsFactorResult:
        if not (0 <= days_since_earnings <= self.post_window_days):
            return EarningsFactorResult("inactive", None, None, f"days_since_earnings={days_since_earnings}不在窗口内，不激活")
        z_ext = cumulative_move_since_pct / self.extension_scale if self.extension_scale else 0.0
        direction = 1 if cumulative_move_since_pct >= 0 else -1

        if abs(z_ext) <= self.extension_threshold_z:
            drift_strength = min(abs(z_ext) / self.extension_threshold_z, 1.0) if self.extension_threshold_z else 0.0
            value = _clip(0.5 + 0.4 * direction * drift_strength)
            confidence = 0.8
            detail = f"z_ext={z_ext:.2f}未过度延伸，PEAD延续逻辑，drift_strength={drift_strength:.3f}"
        else:
            shrink = 1 - math.exp(-(abs(z_ext) - self.extension_threshold_z))
            value = _clip(0.5 + 0.4 * direction * (1 - shrink))
            confidence = _clip(math.exp(-(abs(z_ext) - self.extension_threshold_z)))
            detail = f"z_ext={z_ext:.2f}过度延伸，shrink={shrink:.3f}，confidence={confidence:.3f}降低"

        if surprise_category is not None:
            detail += f"；surprise_category={surprise_category}"
        return EarningsFactorResult("post_earnings", value, confidence, detail)


# ===========================================================================
# 4.5 期权因子：情绪方向 + 隐含波动步长
# ===========================================================================

@dataclass
class OptionsFactorResult:
    value: float                              # 0~1，情绪方向打分（仅sentiment有意义，implied_move结果里此项固定0.5）
    implied_move_pct: Optional[float]          # 期权隐含的预期涨跌幅度（百分比，小数）
    implied_move_price: Optional[float]        # 换算成价格的step
    detail: str


class OptionsFactor:
    """
    期权因子，两个相对独立的功能：

    1) compute_sentiment()：用两种P/C比率 + IV skew 综合判断方向偏多/偏空
       - 成交量P/C比率(put_volume/call_volume)：反映"当天"的资金流向，比较敏感、噪音也大，
         适合捕捉短期情绪突变
       - 持仓量P/C比率(put_oi/call_oi)：反映市场参与者的累计持仓结构，是"实际押注"的存量，
         比成交量更稳定，更能代表中期方向判断，默认给的权重比成交量版本更高
       - 两种P/C比率的方向解读本身在业界有分歧：
         * "顺势"解读（默认 sentiment_polarity=-1）：比率越高，说明买跌的人/持仓越多，判定偏空
         * "逆向"解读（sentiment_polarity=+1）：极端悲观（比率极高）反而常常是市场见底信号，
           这种解读在散户占比高、期权持仓集中的品种上更常见
         两种解读都有实证支持，具体用哪种取决于你交易的品种和风格，不是我能替你定的，
         默认给了"顺势"版本，你需要自己判断要不要翻转。
       - IV skew：市场愿意为下跌保护付多少溢价，衡量恐慌/贪婪程度。支持两种口径（skew_mode）：
         * "absolute"（绝对偏斜）：put_iv - call_iv，用百分点衡量
         * "relative"（相对偏斜，默认）：(put_iv - call_iv) / call_iv，用比例衡量，
           这是很多期权分析工具（包括你截图里那个用yahooquery做的分析卡片）常用的口径，
           比如Call IV=58.07%，Put IV=61.88%，相对偏斜=(61.88-58.07)/58.07≈6.56%
         skew越高，说明恐慌情绪越重，判定越偏空；skew收窄甚至倒挂，说明贪婪情绪上升，判定越偏多

    2) compute_implied_move()：不判断方向，只算"期权隐含的预期波动幅度"，可以直接对接
       track_factor_engine的step_size，作为"期权版本的步长"，和轨道通道算出来的step_size做对比或加权融合。
       两种输入方式二选一：
         - atm_straddle_price：平值跨式期权(ATM call + ATM put)价格，乘以经验折扣系数(默认0.85，
           因为跨式两条腿的时间价值会略微高估真实预期波动)
         - atm_iv + days_to_event：用年化隐含波动率按 IV * sqrt(天数/365) 折算成对应期限的预期波动幅度
    """

    def __init__(
        self,
        pc_volume_baseline: float = 0.7,
        pc_volume_scale: float = 0.3,
        pc_oi_baseline: float = 0.7,
        pc_oi_scale: float = 0.3,
        sentiment_polarity: int = -1,
        skew_mode: Literal["absolute", "relative"] = "relative",
        skew_abs_baseline: float = 0.03,
        skew_abs_scale: float = 0.05,
        skew_relative_baseline: float = 0.05,
        skew_relative_scale: float = 0.08,
        pc_volume_weight: float = 0.3,
        pc_oi_weight: float = 0.4,
        skew_weight: float = 0.3,
    ):
        self.pc_volume_baseline = pc_volume_baseline
        self.pc_volume_scale = pc_volume_scale
        self.pc_oi_baseline = pc_oi_baseline
        self.pc_oi_scale = pc_oi_scale
        self.sentiment_polarity = sentiment_polarity
        self.skew_mode = skew_mode
        self.skew_abs_baseline = skew_abs_baseline
        self.skew_abs_scale = skew_abs_scale
        self.skew_relative_baseline = skew_relative_baseline
        self.skew_relative_scale = skew_relative_scale
        self.pc_volume_weight = pc_volume_weight
        self.pc_oi_weight = pc_oi_weight
        self.skew_weight = skew_weight

    def _ratio_value(self, ratio: float, baseline: float, scale: float) -> float:
        z = (ratio - baseline) / scale if scale else 0.0
        eff = -z * self.sentiment_polarity
        return _clip(_sigmoid(eff))

    def _skew_value(self, call_iv: float, put_iv: float) -> float:
        if self.skew_mode == "relative":
            skew = (put_iv - call_iv) / call_iv if call_iv else 0.0
            baseline, scale = self.skew_relative_baseline, self.skew_relative_scale
        else:
            skew = put_iv - call_iv
            baseline, scale = self.skew_abs_baseline, self.skew_abs_scale
        z = (skew - baseline) / scale if scale else 0.0
        eff = -z
        return _clip(_sigmoid(eff)), skew

    def compute_sentiment(
        self,
        put_volume: Optional[float] = None,
        call_volume: Optional[float] = None,
        put_call_volume_ratio: Optional[float] = None,
        put_oi: Optional[float] = None,
        call_oi: Optional[float] = None,
        put_call_oi_ratio: Optional[float] = None,
        call_iv: Optional[float] = None,
        put_iv: Optional[float] = None,
    ) -> OptionsFactorResult:
        if put_call_volume_ratio is None and put_volume is not None and call_volume:
            put_call_volume_ratio = put_volume / call_volume
        if put_call_oi_ratio is None and put_oi is not None and call_oi:
            put_call_oi_ratio = put_oi / call_oi

        components, detail_parts = [], []

        if put_call_volume_ratio is not None:
            v_vol = self._ratio_value(put_call_volume_ratio, self.pc_volume_baseline, self.pc_volume_scale)
            components.append((v_vol, self.pc_volume_weight))
            detail_parts.append(f"成交量P/C比率(当日情绪流)={put_call_volume_ratio:.3f}(polarity={self.sentiment_polarity}) -> value={v_vol:.3f}")

        if put_call_oi_ratio is not None:
            v_oi = self._ratio_value(put_call_oi_ratio, self.pc_oi_baseline, self.pc_oi_scale)
            components.append((v_oi, self.pc_oi_weight))
            detail_parts.append(f"持仓量P/C比率(累计持仓结构)={put_call_oi_ratio:.3f}(polarity={self.sentiment_polarity}) -> value={v_oi:.3f}")

        if call_iv is not None and put_iv is not None:
            v_skew, skew_raw = self._skew_value(call_iv, put_iv)
            components.append((v_skew, self.skew_weight))
            detail_parts.append(f"skew({self.skew_mode})={skew_raw:.4f} -> value={v_skew:.3f}")

        if not components:
            return OptionsFactorResult(value=0.5, implied_move_pct=None, implied_move_price=None,
                                       detail="未提供成交量P/C、持仓量P/C或call_iv/put_iv，返回中性0.5")

        total_w = sum(w for _, w in components)
        value = sum(v * w for v, w in components) / total_w if total_w > 0 else 0.5
        return OptionsFactorResult(value=_clip(value), implied_move_pct=None, implied_move_price=None,
                                   detail="; ".join(detail_parts))

    def compute_implied_move(
        self,
        underlying_price: float,
        atm_straddle_price: Optional[float] = None,
        atm_iv: Optional[float] = None,
        days_to_event: Optional[float] = None,
        straddle_discount: float = 0.85,
    ) -> OptionsFactorResult:
        detail_parts = []
        if atm_straddle_price is not None and underlying_price:
            implied_move_pct = (atm_straddle_price * straddle_discount) / underlying_price
            detail_parts.append(f"用跨式期权价格估算: straddle={atm_straddle_price}, discount={straddle_discount} "
                                f"-> implied_move_pct={implied_move_pct:.4f}")
        elif atm_iv is not None and days_to_event is not None:
            safe_days = max(days_to_event, 0)   # 防御性处理：负数或异常输入不应该让math.sqrt崩掉
            if safe_days != days_to_event:
                detail_parts.append(f"警告: days_to_event={days_to_event}为负数(可能是时区误差导致)，已按0处理")
            implied_move_pct = atm_iv * math.sqrt(safe_days / 365)
            detail_parts.append(f"用年化IV折算: atm_iv={atm_iv}, days_to_event={safe_days} "
                                f"-> implied_move_pct={implied_move_pct:.4f}")
        else:
            return OptionsFactorResult(value=0.5, implied_move_pct=None, implied_move_price=None,
                                       detail="未提供atm_straddle_price或(atm_iv+days_to_event)，无法估算隐含波动步长")

        implied_move_price = implied_move_pct * underlying_price
        return OptionsFactorResult(value=0.5, implied_move_pct=implied_move_pct, implied_move_price=implied_move_price,
                                   detail="; ".join(detail_parts))


# ===========================================================================
# 5. 总调度：类别权重 + confidence自动再分配 + 自动归一化
# ===========================================================================

@dataclass
class FactorContribution:
    name: str
    relative_weight: float
    value: float
    confidence: float = 1.0
    note: str = ""


@dataclass
class FactorCategory:
    name: str
    category_weight: float
    contributions: List[FactorContribution] = field(default_factory=list)

    def total_relative_weight(self) -> float:
        return sum(c.relative_weight for c in self.contributions)

    def weighted_confidence(self) -> float:
        total_w = self.total_relative_weight()
        if total_w == 0:
            return 1.0
        return sum(c.relative_weight * c.confidence for c in self.contributions) / total_w

    def resolve_internal_weights(self) -> Dict[str, float]:
        total_w = self.total_relative_weight()
        if total_w == 0:
            return {}
        discounted = {c.name: c.relative_weight * c.confidence for c in self.contributions}
        freed = total_w - sum(discounted.values())
        if freed <= 1e-9:
            return discounted
        discounted_total = sum(discounted.values())
        result = {}
        for c in self.contributions:
            share = discounted[c.name] / discounted_total if discounted_total > 0 else 1.0 / len(self.contributions)
            result[c.name] = discounted[c.name] + freed * share
        return result


class FactorOrchestrator:
    def __init__(self):
        self.categories: Dict[str, FactorCategory] = {}

    def set_category(self, name: str, weight: float, contributions: List[FactorContribution]) -> None:
        self.categories[name] = FactorCategory(name=name, category_weight=weight, contributions=contributions)

    def remove_category(self, name: str) -> None:
        self.categories.pop(name, None)

    def total_category_weight(self) -> float:
        return sum(c.category_weight for c in self.categories.values())

    def resolve_category_weights(self) -> Dict[str, float]:
        total_w = self.total_category_weight()
        if total_w == 0:
            return {}
        weighted_conf = {name: cat.weighted_confidence() for name, cat in self.categories.items()}
        discounted = {name: cat.category_weight * weighted_conf[name] for name, cat in self.categories.items()}
        freed = total_w - sum(discounted.values())
        if freed > 1e-9:
            discounted_total = sum(discounted.values())
            redistributed = {}
            for name in self.categories:
                share = discounted[name] / discounted_total if discounted_total > 0 else 1.0 / len(self.categories)
                redistributed[name] = discounted[name] + freed * share
        else:
            redistributed = discounted
        redistributed_total = sum(redistributed.values())
        if redistributed_total == 0:
            return {}
        return {name: w / redistributed_total for name, w in redistributed.items()}

    def build_factors(self) -> List[Factor]:
        category_weights = self.resolve_category_weights()
        factors: List[Factor] = []
        for name, cat in self.categories.items():
            cat_w = category_weights.get(name, 0.0)
            internal_total = cat.total_relative_weight()
            if internal_total == 0:
                continue
            internal_weights = cat.resolve_internal_weights()
            for c in cat.contributions:
                abs_weight = cat_w * (internal_weights[c.name] / internal_total)
                factors.append(Factor(name=f"[{name}] {c.name}", weight=abs_weight, value=c.value, note=c.note))
        return factors

    def weight_breakdown(self) -> Dict:
        category_weights = self.resolve_category_weights()
        breakdown = {}
        for name, cat in self.categories.items():
            cat_w = category_weights.get(name, 0.0)
            internal_total = cat.total_relative_weight()
            internal_weights = cat.resolve_internal_weights()
            factor_detail = {}
            if internal_total > 0:
                for c in cat.contributions:
                    factor_detail[c.name] = cat_w * (internal_weights[c.name] / internal_total)
            breakdown[name] = {"category_weight": cat_w, "factors": factor_detail}
        return breakdown

    def run(self, strategy: TrackFactorStrategy, prices) -> Dict:
        strategy.update_channel(prices)
        strategy.update_factors(self.build_factors())
        return strategy.evaluate()


# ===========================================================================
# 6. 行情数据获取 + 技术面因子自动计算
# ===========================================================================

def fetch_price_history(ticker: str, period: str = "3mo", interval: str = "1d"):
    """
    用 yfinance 拉取行情。若未安装：pip install yfinance --break-system-packages
    返回 (closes, volumes) 两个numpy数组。
    注意：这需要你的运行环境能访问外网（Yahoo Finance），公司内网/代理环境可能需要额外配置。
    """
    dates, closes, volumes = fetch_price_history_full(ticker, period=period, interval=interval)
    return closes, volumes


def fetch_price_history_full(ticker: str, period: str = "3mo", interval: str = "1d"):
    """
    和fetch_price_history一样，但额外返回每根K线对应的日期(ISO格式字符串数组)，
    如果你以后想做"按日期自动核对实际价格"这类功能，这个函数能提供日期定位，
    目前 run_daily_batch 默认走的是手动填"复盘价格"的简化流程，不依赖这个日期匹配。
    """
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError("需要先安装 yfinance：pip install yfinance --break-system-packages") from e

    data = yf.download(ticker, period=period, interval=interval, progress=False)
    if data.empty:
        raise RuntimeError(f"未能获取到 {ticker} 的行情数据，请检查代码是否正确，或该代码在该市场是否存在")

    dates = np.asarray([d.strftime("%Y-%m-%d") for d in data.index])
    closes = np.asarray(data["Close"]).flatten()
    volumes = np.asarray(data["Volume"]).flatten()
    return dates, closes, volumes


def fetch_options_data(ticker: str, target_days: int = 21) -> Dict:
    """
    用 yahooquery 自动拉取期权链数据，找到最接近 target_days 天后到期的合约，
    计算平值(ATM) Call/Put IV、跨式期权价格、以及整条期权链的持仓量/成交量P/C比率。

    若未安装：pip install yahooquery --break-system-packages
    需要能访问外网（Yahoo Finance），公司内网/代理环境可能需要额外配置。
    """
    try:
        from yahooquery import Ticker
        import pandas as pd
    except ImportError as e:
        raise RuntimeError("需要先安装 yahooquery：pip install yahooquery --break-system-packages") from e

    t = Ticker(ticker)

    price_info = t.price
    if not isinstance(price_info, dict) or ticker not in price_info or not isinstance(price_info[ticker], dict):
        raise RuntimeError(f"未能获取到 {ticker} 的现价，请检查代码是否正确")
    underlying_price = price_info[ticker].get("regularMarketPrice")
    if underlying_price is None:
        raise RuntimeError(f"未能获取到 {ticker} 的现价字段(regularMarketPrice)")

    oc = t.option_chain
    if not hasattr(oc, "reset_index"):
        raise RuntimeError(f"未能获取到 {ticker} 的期权链数据，该标的可能没有挂牌期权，或数据源暂时不可用")

    oc = oc.reset_index()
    oc["expiration"] = pd.to_datetime(oc["expiration"])
    today = pd.Timestamp.now().normalize()

    expirations = oc["expiration"].unique()
    if len(expirations) == 0:
        raise RuntimeError(f"{ticker} 没有可用的期权到期日")

    # 过滤掉"已经过期"的到期日：本地系统时间和美股交易所时区可能有偏差，
    # 直接用本地today减到期日可能因为时区误差算出负数天数，NVDA这种挂了大量近月/周度合约的
    # 高流动性标的尤其容易碰到——先把明显早于今天的到期日剔除掉，避免选中一个"看起来已过期"的合约
    valid_expirations = [e for e in expirations if (pd.Timestamp(e) - today).days >= 0]
    if not valid_expirations:
        valid_expirations = list(expirations)  # 兜底：万一全被过滤掉了，退回用全部到期日，避免直接报错

    chosen_expiry = min(valid_expirations, key=lambda e: abs((pd.Timestamp(e) - today).days - target_days))
    days_to_event = max(int((pd.Timestamp(chosen_expiry) - today).days), 0)   # 再兜底一次，绝不返回负数

    sub = oc[oc["expiration"] == chosen_expiry]
    calls = sub[sub["optionType"].str.lower() == "calls"]
    puts = sub[sub["optionType"].str.lower() == "puts"]

    if calls.empty or puts.empty:
        raise RuntimeError(f"{ticker} 在所选到期日({chosen_expiry.date()})的期权链数据不完整(缺calls或puts)")

    atm_call = calls.iloc[(calls["strike"] - underlying_price).abs().argsort()[:1]]
    atm_put = puts.iloc[(puts["strike"] - underlying_price).abs().argsort()[:1]]

    call_iv = float(atm_call["impliedVolatility"].values[0])
    put_iv = float(atm_put["impliedVolatility"].values[0])
    atm_iv = (call_iv + put_iv) / 2

    atm_call_price = float(atm_call["lastPrice"].values[0])
    atm_put_price = float(atm_put["lastPrice"].values[0])
    atm_straddle_price = atm_call_price + atm_put_price

    def _safe_sum(series):
        return float(series.fillna(0).sum())

    call_oi = _safe_sum(calls["openInterest"]) if "openInterest" in calls else None
    put_oi = _safe_sum(puts["openInterest"]) if "openInterest" in puts else None
    call_volume = _safe_sum(calls["volume"]) if "volume" in calls else None
    put_volume = _safe_sum(puts["volume"]) if "volume" in puts else None

    skew_relative = (put_iv - call_iv) / call_iv if call_iv else None
    skew_absolute = put_iv - call_iv

    return {
        "ticker": ticker,
        "underlying_price": underlying_price,
        "expiration": str(chosen_expiry.date()),
        "days_to_event": days_to_event,
        "call_iv": call_iv,
        "put_iv": put_iv,
        "atm_iv": atm_iv,
        "atm_straddle_price": atm_straddle_price,
        "skew_relative": skew_relative,
        "skew_absolute": skew_absolute,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_volume": call_volume,
        "put_volume": put_volume,
    }


# ===========================================================================
# 6.5 期权因子自我校准：本地历史读数积累 + 宏观beta（股票对宏观的敏感度）
# ===========================================================================

DEFAULT_OPTIONS_HISTORY_PATH = os.path.expanduser("~/.unified_strategy_options_history.json")


def _load_options_history(path: str = DEFAULT_OPTIONS_HISTORY_PATH) -> Dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_options_history(history: Dict, path: str = DEFAULT_OPTIONS_HISTORY_PATH) -> None:
    try:
        with open(path, "w") as f:
            json.dump(history, f)
    except Exception:
        pass  # 存储失败不应该影响主流程，最多退化成"没有历史数据可用"


def record_options_reading(
    ticker: str,
    put_call_oi_ratio: Optional[float] = None,
    put_call_volume_ratio: Optional[float] = None,
    skew_relative: Optional[float] = None,
    path: str = DEFAULT_OPTIONS_HISTORY_PATH,
    max_records: int = 252,
) -> None:
    """
    把这次拉到的真实期权读数记录到本地JSON文件里，供以后计算"这只股票自己的历史基准"用。
    每次调用analyze(auto_fetch_options=True)时会自动记录，不需要你手动调用。
    max_records=252 大约是一年的交易日数，超过会自动丢弃最旧的记录。
    """
    history = _load_options_history(path)
    records = history.get(ticker, [])
    records.append({
        "date": datetime.now(timezone.utc).date().isoformat(),
        "put_call_oi_ratio": put_call_oi_ratio,
        "put_call_volume_ratio": put_call_volume_ratio,
        "skew_relative": skew_relative,
    })
    records = records[-max_records:]
    history[ticker] = records
    _save_options_history(history, path)


def get_self_calibrated_baseline(
    ticker: str,
    field: str,
    default_baseline: float,
    min_records: int = 10,
    path: str = DEFAULT_OPTIONS_HISTORY_PATH,
) -> Dict:
    """
    用这只股票自己积累的历史读数，算出该字段(put_call_oi_ratio / put_call_volume_ratio / skew_relative)
    的历史中位数作为自适应基准、历史标准差作为自适应scale。
    历史记录不足min_records条时(比如你才刚开始用这个脚本跑这只股票)，退化用default_baseline兜底，
    baseline会随着你反复调用analyze()、record_options_reading()积累数据后自动变得更准。
    """
    history = _load_options_history(path)
    records = [r[field] for r in history.get(ticker, []) if r.get(field) is not None]
    if len(records) >= min_records:
        baseline = float(np.median(records))
        std = float(np.std(records))
        # 下限保护：即使历史读数刚好凑巧非常接近(std趋近于0)，也不让scale小到荒谬的程度，
        # 用default_baseline的5%当地板——不然万一某天读数和历史基准差了一点点，
        # z值会被放大到几百上千，虽然现在sigmoid已经不会因此崩溃了，但这种情况下的打分本身也没有意义
        scale = max(std, default_baseline * 0.05)
        source = f"自我校准(基于该股票{len(records)}条历史记录)"
    else:
        baseline = default_baseline
        scale = default_baseline * 0.4
        source = f"通用默认值(该股票历史记录仅{len(records)}条，不足{min_records}条门槛，暂用经验默认值)"
    return {"baseline": baseline, "scale": scale, "source": source, "n_records": len(records)}


def compute_macro_beta(ticker: str, benchmark: str = "^GSPC", period: str = "1y") -> Dict:
    """
    用股票相对大盘指数(默认标普500)的历史协方差，算出一个粗略的"宏观敏感度"beta：
        beta > 1：这只股票对大盘/宏观面波动比市场平均更敏感（常见于高杠杆成长股）
        beta < 1：这只股票对大盘/宏观面波动比市场平均更不敏感（常见于防御性消费、公用事业股）
        beta ≈ 1：和大盘平均敏感度接近

    这是用CAPM框架里的beta做代理指标，不是针对"这条具体CPI数据"单独测算的敏感度，
    只是一个能立刻用价格数据算出来的粗略近似，用来把宏观因子的影响力按个股特性缩放，
    而不是让所有股票对同一条宏观新闻做出同样力度的反应。
    """
    try:
        stock_closes, _ = fetch_price_history(ticker, period=period)
        bench_closes, _ = fetch_price_history(benchmark, period=period)
    except RuntimeError as e:
        return {"beta": 1.0, "source": f"无法计算(数据获取失败: {e})，使用默认beta=1.0"}

    n = min(len(stock_closes), len(bench_closes))
    if n < 20:
        return {"beta": 1.0, "source": "历史数据不足20个交易日，使用默认beta=1.0"}

    stock_returns = np.diff(stock_closes[-n:]) / stock_closes[-n:-1]
    bench_returns = np.diff(bench_closes[-n:]) / bench_closes[-n:-1]

    cov = float(np.cov(stock_returns, bench_returns)[0][1])
    var = float(np.var(bench_returns))
    beta = cov / var if var > 1e-12 else 1.0

    return {"beta": beta, "source": f"用过去{n}个交易日相对{benchmark}的协方差算出"}


def compute_momentum_factor(prices, lookback: int = 20, scale: Optional[float] = None,
                            vol_lookback: int = 60, vol_multiplier: float = 1.5) -> float:
    """
    动量因子：过去lookback个交易日的涨跌幅，通过logistic函数映射到0~1。

    scale("多大的涨跌幅算显著")如果不传(默认None)，会自动用这只股票自己过去vol_lookback天的
    日收益率标准差，折算成lookback天的预期波动幅度，乘以vol_multiplier，作为自适应的scale。
    这是标准的"用自身历史波动率做标准化"做法：不再用一个所有股票通用的固定数字(比如5%)去卡阈值，
    而是问"相对于这只股票自己的历史波动，这次涨跌算不算大"——高波动成长股需要更大的涨跌才会被
    认为"显著"，低波动蓝筹股一个不大的涨跌就已经算显著，这样才不会用同一把尺子错误地衡量所有股票。
    如果你想固定用一个数字（比如跨股票做统一横向比较），显式传scale即可覆盖自适应逻辑。
    """
    prices = np.asarray(prices, dtype=float)
    lb = min(lookback, len(prices) - 1)
    if lb < 1:
        return 0.5
    ret = (prices[-1] - prices[-lb - 1]) / prices[-lb - 1]

    if scale is None:
        vlb = min(vol_lookback, len(prices) - 1)
        if vlb >= 5:
            daily_returns = np.diff(prices[-vlb - 1:]) / prices[-vlb - 1:-1]
            daily_std = float(np.std(daily_returns))
            scale = max(daily_std * math.sqrt(lb) * vol_multiplier, 1e-6)
        else:
            scale = 0.05  # 历史数据不够长(少于5个交易日的波动率样本)时的兜底默认值

    z = ret / scale if scale else 0.0
    return _clip(_sigmoid(z))


def compute_volume_factor(volumes, prices, lookback: int = 20, recent_window: int = 5) -> float:
    """
    量能因子：近期成交量相对更长周期基准量的放大/缩小程度，结合近期价格方向，
    "价涨量增"偏多，"价跌量增"偏空（放量确认当前方向，而不是判断反转）。
    """
    volumes = np.asarray(volumes, dtype=float)
    prices = np.asarray(prices, dtype=float)
    if len(volumes) < recent_window + 2:
        return 0.5
    lb = min(lookback, len(volumes) - recent_window)
    recent_avg = volumes[-recent_window:].mean()
    baseline_avg = volumes[-recent_window - lb:-recent_window].mean() if lb > 0 else volumes[:-recent_window].mean()
    if baseline_avg <= 0:
        return 0.5
    vol_ratio = recent_avg / baseline_avg
    price_dir = 1 if prices[-1] >= prices[-recent_window - 1] else -1
    z = (vol_ratio - 1) * price_dir
    return _clip(_sigmoid(2 * z))


# ===========================================================================
# 7. 顶层入口：输入股票代码即可运行
# ===========================================================================

def project_price_path(channel: LinearRegressionChannel, last_price: float, direction: str,
                       step: float, steps: int = 5) -> List[Dict]:
    """按给定方向和step size，把价格逐步外推，同时给出对应的通道边界"""
    sign = {"up": 1, "down": -1, "sideways": 0}[direction]
    price = last_price
    path = []
    channel_proj = channel.project(steps)
    for i in range(steps):
        price = price + sign * step
        bounds = channel_proj[i]
        path.append({"step": i + 1, "predicted_price": price, "channel_upper": bounds["upper"],
                     "channel_mid": bounds["mid"], "channel_lower": bounds["lower"]})
    return path


def estimate_price_probabilities(
    current_price: float,
    direction: str,
    step: float,
    channel_std: float,
    options_atm_iv: Optional[float] = None,
    horizons: Optional[List[int]] = None,
    price_targets: Optional[List[float]] = None,
) -> Dict:
    """
    把"方向+步长"和"波动率"转换成具体的概率预测。核心假设（简化模型，不是精确定价）：
        - 未来N天价格 ~ 正态分布，均值 = current_price + drift_per_day * N
          (drift_per_day 的符号由direction决定，sideways时drift=0；大小=step)
        - 标准差 = daily_sigma * sqrt(N)
          daily_sigma 优先用期权ATM隐含波动率折算(更准，反映市场对波动的真实定价)，
          没有期权数据时退化用轨道通道的残差标准差(channel_std)近似

    用正态分布CDF算"涨/跌超过current_price的概率"，以及"涨到/跌到某个具体目标价"的概率
    (默认目标价用轨道上下轨代替，你也可以自己传price_targets)。

    重要提示：这是把方向判断转换成概率语言的简化工具，假设drift和波动率在预测期内保持不变，
    不考虑跳空、突发事件冲击、波动率本身的变化(volatility of volatility)，
    实际结果会明显偏离这个假设，尤其是预测期越长越不可靠。不构成投资建议。
    """
    if horizons is None:
        horizons = [1, 3, 5, 10]

    def norm_cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    sign = {"up": 1, "down": -1, "sideways": 0}[direction]
    drift_per_day = sign * step

    if options_atm_iv is not None:
        daily_sigma = options_atm_iv * current_price / math.sqrt(252)
        sigma_source = "期权ATM隐含波动率折算（更贴近市场真实波动预期）"
    else:
        daily_sigma = channel_std if channel_std else current_price * 0.01
        sigma_source = "轨道通道残差标准差近似（没有期权数据时的退化方案）"

    forecasts = {}
    for h in horizons:
        mean_price = current_price + drift_per_day * h
        std_price = daily_sigma * math.sqrt(h)

        if std_price > 0:
            prob_above_current = 1 - norm_cdf((current_price - mean_price) / std_price)
        else:
            prob_above_current = 1.0 if mean_price > current_price else 0.0

        ci68 = (mean_price - std_price, mean_price + std_price)
        ci95 = (mean_price - 1.96 * std_price, mean_price + 1.96 * std_price)

        target_probs = {}
        for tp in (price_targets or []):
            if std_price > 0:
                p_above = 1 - norm_cdf((tp - mean_price) / std_price)
            else:
                p_above = 1.0 if mean_price >= tp else 0.0
            label = f"涨到{tp:.2f}以上" if tp >= current_price else f"跌到{tp:.2f}以下"
            target_probs[label] = p_above if tp >= current_price else (1 - p_above)

        forecasts[f"{h}日"] = {
            "expected_price": round(mean_price, 4),
            "std": round(std_price, 4),
            "prob_up_from_current": round(prob_above_current, 4),
            "prob_down_from_current": round(1 - prob_above_current, 4),
            "ci_68%": (round(ci68[0], 4), round(ci68[1], 4)),
            "ci_95%": (round(ci95[0], 4), round(ci95[1], 4)),
            "target_probabilities": {k: round(v, 4) for k, v in target_probs.items()},
        }

    return {"sigma_source": sigma_source, "daily_sigma": round(daily_sigma, 6),
            "drift_per_day": round(drift_per_day, 6), "forecasts": forecasts}


def analyze(
    ticker: str,
    macro_inputs: Optional[Dict] = None,
    monetary_inputs: Optional[Dict] = None,
    news_inputs: Optional[List[Dict]] = None,
    earnings_inputs: Optional[Dict] = None,
    options_inputs: Optional[Dict] = None,
    auto_fetch_options: bool = False,   # True时自动用yahooquery拉期权链，手动传入options_inputs里的字段优先级更高
    options_target_days: int = 21,       # 自动拉期权数据时，选择最接近这个天数的到期日
    options_history_path: str = DEFAULT_OPTIONS_HISTORY_PATH,   # 期权历史读数存储路径，用于自我校准
    macro_beta: Optional[float] = None,   # 该股票对宏观数据的敏感度；None时若能联网会自动用相对大盘的beta估算
    period: str = "3mo",
    n_std: float = 2.0,
    step_k: float = 1.0,
    sideways_band: float = 0.05,
    project_steps: int = 5,
    step_blend_weight: float = 0.5,   # 融合"期权隐含步长"时给期权的权重，0=完全用轨道step，1=完全用期权step
    prices_override=None,
    volumes_override=None,
) -> Dict:
    """
    输入股票代码，自动完成：拉行情 → 技术面因子自动计算 → 可选宏观/新闻/财报因子 → 综合评估 + 路径推演。

    macro_inputs 格式（缺的指标直接不传，不影响其他指标）：
        {
            "cpi": {"actual": 0.2, "expected": 0.3},
            "nfp": {"actual": 30, "expected": 80},
            "pmi": {"actual": 51.2, "expected": 50.8},
            "pce": {"actual": ..., "expected": ...},
            "ppi": {"actual": ..., "expected": ...},
        }

    monetary_inputs 格式（缺的央行直接不传；actual_bp/expected_bp都用基点表示，加息为正、降息为负，
    比如加息25bp传25，维持不变传0，降息25bp传-25）：
        {
            "fed": {"actual_bp": 25, "expected_bp": 25},   # 符合预期的加息
            "boj": {"actual_bp": 15, "expected_bp": 0},     # 意外加息(容易触发套息交易平仓，重点关注)
            "bok": {"actual_bp": 0, "expected_bp": 0},
        }
    如果你想加其他央行(比如欧央行ECB、央行PBOC)，可以直接实例化 _BaseCentralBankFactor 并设置
    bank_name/polarity/spillover_weight，或者仿照BOJFactor/BOKFactor的写法定义一个新子类，
    然后在这段代码里的mapping字典里加一行即可，不需要改动其他任何地方。

    news_inputs 格式（可以传多条新闻）：
        [
            {"level": "positive", "impact_score": 0.8, "days_since_news": 1},
            {"level": "mild_negative", "impact_score": 0.4, "days_since_news": 0},
        ]

    earnings_inputs 格式（二选一）：
        财报前: {"phase": "pre", "days_to_earnings": 5, "pre_runup_pct": 0.10}
        财报后: {"phase": "post", "days_since_earnings": 3, "earnings_day_move_pct": 0.09,
                 "cumulative_move_since_pct": 0.32, "surprise_category": "beat"}

    options_inputs 格式（情绪和步长两部分都是可选，各自独立传；auto_fetch_options=True时会自动填充这些字段，
    这里手动传的值优先级更高，用于覆盖自动拉取的结果）：
        {
            # 情绪方向：成交量P/C(put_volume+call_volume 或直接给 put_call_volume_ratio)
            #          持仓量P/C(put_oi+call_oi 或直接给 put_call_oi_ratio)，两种可以同时给
            "put_call_volume_ratio": 0.85,
            "put_call_oi_ratio": 0.95,
            "call_iv": 0.28, "put_iv": 0.33,
            "sentiment_polarity": -1,   # -1顺势解读(默认)，+1逆向解读，具体用哪种取决于你的品种和风格
            "skew_mode": "relative",     # "relative"(默认，(put_iv-call_iv)/call_iv) 或 "absolute"(put_iv-call_iv)

            # 隐含波动步长（atm_straddle_price，或者atm_iv+days_to_event，二选一）
            "atm_straddle_price": 8.5,
            "atm_iv": 0.45, "days_to_event": 3,
        }

    auto_fetch_options=True 时，会用 yahooquery 自动拉取期权链数据（ATM Call/Put IV、跨式期权价格、
    整条链的持仓量/成交量），不需要手动填 options_inputs，用法示例：
        analyze("ADSK", auto_fetch_options=True, options_target_days=21)
    传入 atm_straddle_price 或 atm_iv 后，会先把"期权隐含的累计预期波动"(options_implied_total_move，
    即到days_to_event/project_steps为止的总波动)按天数摊薄成单步步长(options_implied_step_per_period)，
    再和轨道通道算出来的step_size按 step_blend_weight 加权融合成 combined_step_size，用于逐步外推价格路径。

    prices_override / volumes_override：如果你不想用yfinance联网拉数据（比如离线测试、
    或者想接自己的数据源），直接传入价格/成交量的numpy数组或list即可，会跳过 fetch_price_history。
    """
    if prices_override is not None:
        prices = np.asarray(prices_override, dtype=float)
        volumes = np.asarray(volumes_override, dtype=float) if volumes_override is not None else None
    else:
        prices, volumes = fetch_price_history(ticker, period=period)

    orch = FactorOrchestrator()

    # ---- 技术面：自动计算，永远激活 ----
    momentum_v = compute_momentum_factor(prices)
    tech_contributions = [FactorContribution(name="动量", relative_weight=0.6, value=momentum_v,
                                             note=f"20日动量因子 value={momentum_v:.3f}")]
    if volumes is not None:
        volume_v = compute_volume_factor(volumes, prices)
        tech_contributions.append(FactorContribution(name="量能", relative_weight=0.4, value=volume_v,
                                                      note=f"量能配合因子 value={volume_v:.3f}"))
    orch.set_category("技术面", weight=0.35, contributions=tech_contributions)

    # ---- 宏观：可选，按个股beta缩放影响力 ----
    macro_beta_info = None
    if macro_inputs:
        if macro_beta is not None:
            effective_beta = macro_beta
            macro_beta_info = {"beta": macro_beta, "source": "手动传入"}
        elif prices_override is None:
            macro_beta_info = compute_macro_beta(ticker)
            effective_beta = macro_beta_info["beta"]
        else:
            effective_beta = 1.0
            macro_beta_info = {"beta": 1.0, "source": "使用了prices_override(非联网拉取)，无法计算beta，默认1.0"}

        mapping = {"cpi": CPIFactor(), "pce": PCEFactor(), "ppi": PPIFactor(), "nfp": NFPFactor(), "pmi": PMIFactor()}
        contributions = []
        for key, inst in mapping.items():
            if key in macro_inputs:
                d = macro_inputs[key]
                if key == "nfp":
                    r = inst.compute_regime_aware(actual=d["actual"], expected=d["expected"])
                elif key == "pmi":
                    r = inst.compute_with_level(actual=d["actual"], expected=d["expected"])
                else:
                    r = inst.compute(actual=d["actual"], expected=d["expected"])
                # 按beta把该因子的方向性拉力放大/缩小：beta>1放大偏离中性的程度，beta<1缩小
                adjusted_value = _clip(0.5 + (r.value - 0.5) * effective_beta)
                contributions.append(FactorContribution(
                    name=key.upper(), relative_weight=1.0, value=adjusted_value,
                    note=f"{r.detail}；beta={effective_beta:.2f}调整后value={adjusted_value:.3f}(原始value={r.value:.3f})"
                ))
        if contributions:
            orch.set_category("宏观", weight=0.25, contributions=contributions)

    # ---- 货币政策：可选（Fed/BOJ/BOK，可扩展其他央行） ----
    if monetary_inputs:
        mapping = {"fed": FedFactor(), "boj": BOJFactor(), "bok": BOKFactor()}
        contributions = []
        for key, inst in mapping.items():
            if key in monetary_inputs:
                d = monetary_inputs[key]
                r = inst.compute(actual_bp=d["actual_bp"], expected_bp=d["expected_bp"])
                # 按spillover_weight调整该央行决议对美股的实际拉力(逻辑同macro_beta的处理方式)
                adjusted_value = _clip(0.5 + (r.value - 0.5) * inst.spillover_weight)
                contributions.append(FactorContribution(
                    name=inst.bank_name, relative_weight=1.0, value=adjusted_value,
                    note=f"{r.detail}；spillover_weight={inst.spillover_weight}调整后value={adjusted_value:.3f}"
                ))
        if contributions:
            orch.set_category("货币政策", weight=0.20, contributions=contributions)

    # ---- 新闻：可选 ----
    if news_inputs:
        nf = NewsFactor()
        contributions = []
        for i, item in enumerate(news_inputs):
            r = nf.compute(level=item["level"], impact_score=item.get("impact_score", 1.0),
                           days_since_news=item.get("days_since_news", 0))
            contributions.append(FactorContribution(name=f"新闻{i+1}", relative_weight=1.0, value=r.value, note=r.detail))
        orch.set_category("新闻", weight=0.15, contributions=contributions)

    # ---- 财报：可选 ----
    if earnings_inputs:
        ef = EarningsFactor()
        phase = earnings_inputs.get("phase")
        r = None
        if phase == "pre":
            r = ef.compute_pre_earnings(days_to_earnings=earnings_inputs["days_to_earnings"],
                                        pre_runup_pct=earnings_inputs["pre_runup_pct"])
        elif phase == "post":
            r = ef.compute_post_earnings(days_since_earnings=earnings_inputs["days_since_earnings"],
                                         earnings_day_move_pct=earnings_inputs["earnings_day_move_pct"],
                                         cumulative_move_since_pct=earnings_inputs["cumulative_move_since_pct"],
                                         surprise_category=earnings_inputs.get("surprise_category"))
        if r is not None and r.value is not None:
            orch.set_category("财报", weight=0.25, contributions=[
                FactorContribution(name="财报因子", relative_weight=1.0, value=r.value,
                                   confidence=r.confidence or 1.0, note=r.detail)
            ])

    # ---- 期权：情绪方向 + 隐含波动步长 ----
    # 自动拉取模式：yahooquery拉到的字段作为底，手动传入的options_inputs字段优先级更高（可覆盖自动拉取的值）
    if auto_fetch_options:
        fetched = fetch_options_data(ticker, target_days=options_target_days)
        merged = {
            "put_oi": fetched["put_oi"], "call_oi": fetched["call_oi"],
            "put_volume": fetched["put_volume"], "call_volume": fetched["call_volume"],
            "call_iv": fetched["call_iv"], "put_iv": fetched["put_iv"],
            "atm_iv": fetched["atm_iv"], "atm_straddle_price": fetched["atm_straddle_price"],
            "days_to_event": fetched["days_to_event"],
        }
        merged.update(options_inputs or {})   # 手动传入的字段覆盖自动拉取的同名字段
        options_inputs = merged

        # 记录这次真实读数，供以后自我校准用（只在真实拉取时记录，避免手动测试数据污染历史）
        put_call_oi_ratio = (fetched["put_oi"] / fetched["call_oi"]
                             if fetched["put_oi"] and fetched["call_oi"] else None)
        put_call_volume_ratio = (fetched["put_volume"] / fetched["call_volume"]
                                 if fetched["put_volume"] and fetched["call_volume"] else None)
        record_options_reading(ticker, put_call_oi_ratio, put_call_volume_ratio,
                               fetched["skew_relative"], path=options_history_path)

    options_move_result = None
    options_calibration_info = None
    if options_inputs:
        # 用这只股票自己积累的历史读数做自我校准，历史不够时退化用通用默认值
        oi_calib = get_self_calibrated_baseline(ticker, "put_call_oi_ratio", default_baseline=0.7,
                                                path=options_history_path)
        vol_calib = get_self_calibrated_baseline(ticker, "put_call_volume_ratio", default_baseline=0.7,
                                                 path=options_history_path)
        skew_calib = get_self_calibrated_baseline(ticker, "skew_relative", default_baseline=0.05,
                                                   path=options_history_path)
        options_calibration_info = {"put_call_oi": oi_calib, "put_call_volume": vol_calib, "skew": skew_calib}

        opt = OptionsFactor(
            sentiment_polarity=options_inputs.get("sentiment_polarity", -1),
            skew_mode=options_inputs.get("skew_mode", "relative"),
            pc_oi_baseline=oi_calib["baseline"], pc_oi_scale=oi_calib["scale"],
            pc_volume_baseline=vol_calib["baseline"], pc_volume_scale=vol_calib["scale"],
            skew_relative_baseline=skew_calib["baseline"], skew_relative_scale=skew_calib["scale"],
        )

        sentiment_keys = ("put_call_volume_ratio", "put_volume", "put_call_oi_ratio", "put_oi", "call_iv")
        if any(k in options_inputs for k in sentiment_keys):
            sentiment_result = opt.compute_sentiment(
                put_volume=options_inputs.get("put_volume"),
                call_volume=options_inputs.get("call_volume"),
                put_call_volume_ratio=options_inputs.get("put_call_volume_ratio"),
                put_oi=options_inputs.get("put_oi"),
                call_oi=options_inputs.get("call_oi"),
                put_call_oi_ratio=options_inputs.get("put_call_oi_ratio"),
                call_iv=options_inputs.get("call_iv"),
                put_iv=options_inputs.get("put_iv"),
            )
            orch.set_category("期权", weight=0.15, contributions=[
                FactorContribution(name="期权情绪", relative_weight=1.0, value=sentiment_result.value,
                                   note=sentiment_result.detail)
            ])

        move_keys = ("atm_straddle_price", "atm_iv")
        if any(k in options_inputs for k in move_keys):
            options_move_result = opt.compute_implied_move(
                underlying_price=float(prices[-1]),
                atm_straddle_price=options_inputs.get("atm_straddle_price"),
                atm_iv=options_inputs.get("atm_iv"),
                days_to_event=options_inputs.get("days_to_event"),
            )

    strategy = TrackFactorStrategy(n_std=n_std, step_k=step_k, sideways_band=sideways_band)
    evaluation = orch.run(strategy, prices)
    channel_step = evaluation["step_size"]

    # ---- 融合"轨道推算的步长"和"期权隐含的步长" ----
    # 注意：期权隐含步长(implied_move_price)是"到某个事件/到期日为止的累计预期波动"，
    # 不是"每天/每步"的步长，不能直接按步复利，否则会被严重高估。这里先按天数摊薄成日均步长，
    # 再拿去和轨道的单步step_size做融合。
    combined_step = channel_step
    if options_move_result is not None and options_move_result.implied_move_price is not None:
        days_for_spread = options_inputs.get("days_to_event") or project_steps
        days_for_spread = max(days_for_spread, 1)
        options_step_per_period = options_move_result.implied_move_price / days_for_spread

        evaluation["channel_step_size"] = channel_step
        evaluation["options_implied_total_move"] = options_move_result.implied_move_price
        evaluation["options_implied_move_pct"] = options_move_result.implied_move_pct
        evaluation["options_implied_step_per_period"] = options_step_per_period
        combined_step = (1 - step_blend_weight) * channel_step + step_blend_weight * options_step_per_period
        evaluation["combined_step_size"] = combined_step

    projected_path = project_price_path(strategy.channel, strategy._last_price, evaluation["direction"],
                                        combined_step, steps=project_steps)
    breakdown = orch.weight_breakdown()

    # ---- 概率预测：把方向+步长+波动率转换成具体的概率语言 ----
    channel_bounds = evaluation["channel_bounds"]
    options_atm_iv = options_inputs.get("atm_iv") if options_inputs else None
    probability_forecast = estimate_price_probabilities(
        current_price=strategy._last_price,
        direction=evaluation["direction"],
        step=combined_step,
        channel_std=strategy.channel.std,
        options_atm_iv=options_atm_iv,
        horizons=sorted(set([1, 3, 5, project_steps])),
        price_targets=[channel_bounds["upper"], channel_bounds["lower"]],
    )

    return {
        "ticker": ticker,
        "evaluation": evaluation,
        "projected_path": projected_path,
        "weight_breakdown": breakdown,
        "probability_forecast": probability_forecast,
        "macro_beta_info": macro_beta_info,
        "options_calibration_info": options_calibration_info,
    }


import csv

_DIRECTION_CN = {"up": "涨", "down": "跌", "sideways": "横盘"}

BATCH_CSV_FIELDNAMES = ["股票代码", "时间", "当前价格", "预计走向", "涨跌幅区间", "因子分数", "复盘价格"]


def run_daily_batch(
    tickers: List[str],
    output_path: str = "stock_tracking.csv",
    auto_fetch_options: bool = True,
    options_target_days: int = 21,
    macro_inputs: Optional[Dict] = None,
    monetary_inputs: Optional[Dict] = None,
    earnings_inputs_map: Optional[Dict[str, Dict]] = None,
    period: str = "3mo",
    n_std: float = 2.0,
    step_k: float = 1.0,
    sideways_band: float = 0.05,
    verbose: bool = True,
) -> List[Dict]:
    """
    批量轮询一组股票代码，每只调用一次analyze()（技术面自动算 + 期权自动拉取，
    宏观/货币政策/财报可选），把关键结果追加写入本地CSV表格。

    表格只保留7列，直接对应你要看的东西：
        股票代码 / 时间 / 当前价格 / 预计走向(涨/跌/横盘) / 涨跌幅区间(轨道通道的上下轨价格)
        / 因子分数(参与度participation) / 复盘价格(留空，你自己每天回来手动填实际价格)

    "复盘价格"这一列不会被自动写入或覆盖——写完之后你自己打开CSV，隔几天回来
    手动填上当时那只股票实际到了多少钱，再用 summarize_manual_review() 统计命中率，
    看这套算法准不准，该往哪个方向调参数。

    参数
    ----
    tickers            : 股票代码数组，比如 ["AAPL", "MSFT", "NVDA"]
    output_path         : CSV文件路径。文件不存在会自动创建表头；已存在则在末尾追加新记录，
                          不会覆盖旧数据(包括你已经手动填好的"复盘价格"那些行也不会被动到)
    macro_inputs        : 当天全市场统一的宏观数据(CPI/非农/PMI等)，会应用到这次批量里的每一只股票
    monetary_inputs      : 当天全市场统一的央行数据(Fed/BOJ/BOK)，同样应用到每一只股票
    earnings_inputs_map  : 如果批量里有股票正好在财报窗口期，按股票代码单独传入，
                          格式：{"AAPL": {"phase": "post", ...}, ...}，没有财报的股票不用传

    某只股票拉取失败(比如代码错误、没有期权链、网络问题)不会中断整个批量，会打印错误信息并跳过，
    不写入该行(避免"复盘价格"列对着一行没有有效预测的数据，填了也没意义)。

    返回值：本次运行所有股票的完整analyze()结果列表；CSV里只存了这7列的摘要，
    如果你需要更细的字段(比如具体因子明细)，从返回值里自己另存即可。
    """
    file_exists = os.path.exists(output_path)
    results = []
    today_str = datetime.now(timezone.utc).date().isoformat()

    with open(output_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=BATCH_CSV_FIELDNAMES)
        if not file_exists:
            writer.writeheader()

        for ticker in tickers:
            try:
                earnings_inputs = (earnings_inputs_map or {}).get(ticker)
                result = analyze(
                    ticker,
                    macro_inputs=macro_inputs,
                    monetary_inputs=monetary_inputs,
                    earnings_inputs=earnings_inputs,
                    auto_fetch_options=auto_fetch_options,
                    options_target_days=options_target_days,
                    period=period, n_std=n_std, step_k=step_k, sideways_band=sideways_band,
                )
                e = result["evaluation"]
                bounds = e["channel_bounds"]
                direction_cn = _DIRECTION_CN.get(e.get("direction"), e.get("direction"))
                price_range = f"{bounds['lower']:.2f}-{bounds['upper']:.2f}"

                row = {
                    "股票代码": ticker,
                    "时间": today_str,
                    "当前价格": round(e.get("current_price"), 4),
                    "预计走向": direction_cn,
                    "涨跌幅区间": price_range,
                    "因子分数": round(e.get("participation"), 4),
                    "复盘价格": "",
                }
                writer.writerow(row)
                results.append(result)
                if verbose:
                    print(f"[OK] {ticker}: {direction_cn}  现价={row['当前价格']}  区间={price_range}  "
                         f"因子分数={row['因子分数']}")
            except Exception as ex:
                results.append({"ticker": ticker, "error": str(ex)})
                if verbose:
                    print(f"[FAIL] {ticker}: {ex}")

    if verbose:
        print(f"\n共处理{len(tickers)}只股票，结果已追加写入 {output_path}")

    return results


def summarize_manual_review(csv_path: str, sideways_threshold: float = 0.01) -> Dict:
    """
    统计"复盘价格"手动填好之后的命中率。

    做法很简单：直接对比同一行里的"当前价格"和你手动填的"复盘价格"，算出实际涨跌幅，
    实际涨跌幅在±sideways_threshold(默认1%)以内判定为"横盘"，否则按符号判"涨"或"跌"，
    再跟当时"预计走向"那一列对比，算出命中(True)还是没命中(False)。

    不涉及重新拉取行情、不用管"复盘价格"具体对应第几天后——你自己心里清楚就行，
    这个函数只看这两列数字的差值。只统计"复盘价格"已经填了数字的行，没填的行直接跳过。
    """
    if not os.path.exists(csv_path):
        print(f"文件不存在: {csv_path}")
        return {}

    with open(csv_path, "r", newline="") as f:
        rows = list(csv.DictReader(f))

    total, hits = 0, 0
    by_direction = {"涨": [0, 0], "跌": [0, 0], "横盘": [0, 0]}   # {预测方向: [命中数, 总数]}

    for row in rows:
        actual_str = (row.get("复盘价格") or "").strip()
        if not actual_str:
            continue
        try:
            current_price = float(row["当前价格"])
            actual_price = float(actual_str)
        except (ValueError, TypeError):
            continue

        predicted = row.get("预计走向", "")
        actual_return = (actual_price - current_price) / current_price if current_price else 0.0
        if abs(actual_return) <= sideways_threshold:
            actual_direction = "横盘"
        else:
            actual_direction = "涨" if actual_return > 0 else "跌"

        total += 1
        is_hit = actual_direction == predicted
        if is_hit:
            hits += 1
        if predicted in by_direction:
            by_direction[predicted][1] += 1
            if is_hit:
                by_direction[predicted][0] += 1

    overall_rate = round(hits / total, 4) if total else None
    print(f"已回填样本数={total}，总命中率={overall_rate}")
    for d, (h, t) in by_direction.items():
        rate = round(h / t, 4) if t else None
        print(f"  预测'{d}'的样本: {t}个，命中率={rate}")

    return {"total": total, "overall_hit_rate": overall_rate,
            "by_direction": {d: {"样本数": t, "命中率": (round(h/t,4) if t else None)} for d, (h, t) in by_direction.items()}}


def print_result(result: Dict) -> None:
    print(f"\n===== {result['ticker']} 综合评估 =====")
    for k, v in result["evaluation"].items():
        print(f"{k}: {v}")

    print("\n----- 权重分配明细 -----")
    for cat_name, info in result["weight_breakdown"].items():
        print(f"[{cat_name}] 类别权重={info['category_weight']:.4f}")
        for f_name, w in info["factors"].items():
            print(f"   - {f_name}: {w:.4f}")

    if result.get("macro_beta_info"):
        bi = result["macro_beta_info"]
        print(f"\n----- 宏观敏感度(beta) -----")
        print(f"beta={bi['beta']:.3f}  来源: {bi['source']}")

    if result.get("options_calibration_info"):
        print(f"\n----- 期权因子校准情况 -----")
        for field_name, info in result["options_calibration_info"].items():
            print(f"[{field_name}] baseline={info['baseline']:.4f}, scale={info['scale']:.4f}, {info['source']}")

    print("\n----- 未来路径推演(确定性外推，仅供参考) -----")
    for p in result["projected_path"]:
        print(p)

    if "probability_forecast" in result:
        pf = result["probability_forecast"]
        print(f"\n----- 概率预测 (波动率来源: {pf['sigma_source']}) -----")
        print(f"日均漂移 drift_per_day={pf['drift_per_day']}, 日波动率 daily_sigma={pf['daily_sigma']}")
        for horizon, f in pf["forecasts"].items():
            print(f"\n[{horizon}] 期望价={f['expected_price']}, 标准差={f['std']}")
            print(f"   上涨概率={f['prob_up_from_current']*100:.1f}%，下跌概率={f['prob_down_from_current']*100:.1f}%")
            print(f"   68%置信区间: {f['ci_68%']}　95%置信区间: {f['ci_95%']}")
            for label, p in f["target_probabilities"].items():
                print(f"   {label} 的概率: {p*100:.1f}%")

    if "options_note" in result:
        print(f"\n[提示] {result['options_note']}")


# ===========================================================================
# 8. 在这里写死你要跟踪的股票代码，改这里就行，不用每次调用时传参
# ===========================================================================

TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "GOOGL",
    "GOOGC",
    "MU",
    "WDAY",
    "ULTA",
    "ADSK",

]

# 想统一给这批股票加宏观/央行数据(全市场统一，不用按股票分开写)，就填在这里，没有就留 None
MACRO_INPUTS = None
# 例: MACRO_INPUTS = {"cpi": {"actual": 0.2, "expected": 0.3}}

MONETARY_INPUTS = None
# 例: MONETARY_INPUTS = {"fed": {"actual_bp": 25, "expected_bp": 25}, "boj": {"actual_bp": 15, "expected_bp": 0}}

# 结果追加写入的CSV路径
OUTPUT_CSV_PATH = "stock_tracking.csv"


if __name__ == "__main__":
    run_daily_batch(
        TICKERS,
        output_path=OUTPUT_CSV_PATH,
        macro_inputs=MACRO_INPUTS,
        monetary_inputs=MONETARY_INPUTS,
    )