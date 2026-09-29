"""配对候选：先经济关系，再统计。不是全市场两两相关。"""

from __future__ import annotations

# 每组内互配。大组只取前若干只做全组合，避免爆炸。
GROUPS: list[dict] = [
    {
        "key": "payments",
        "label": "支付",
        "relation": "同一商业模式",
        "symbols": ["V", "MA", "AXP"],
    },
    {
        "key": "soda",
        "label": "饮料",
        "relation": "同一商业模式",
        "symbols": ["KO", "PEP"],
    },
    {
        "key": "oil",
        "label": "石油",
        "relation": "同一行业",
        "symbols": ["XOM", "CVX", "COP"],
    },
    {
        "key": "home",
        "label": "家居零售",
        "relation": "同一商业模式",
        "symbols": ["HD", "LOW"],
    },
    {
        "key": "banks",
        "label": "银行",
        "relation": "同一行业",
        "symbols": ["JPM", "BAC", "WFC", "C"],
    },
    {
        "key": "parcel",
        "label": "快递",
        "relation": "同一商业模式",
        "symbols": ["FDX", "UPS"],
    },
    {
        "key": "semis",
        "label": "半导体",
        "relation": "同一行业",
        "symbols": ["NVDA", "AMD", "AVGO", "TSM", "MU", "INTC", "QCOM", "AMAT"],
    },
    {
        "key": "ads",
        "label": "广告平台",
        "relation": "同一商业模式",
        "symbols": ["GOOG", "META"],
    },
    {
        "key": "cloud",
        "label": "云与软件",
        "relation": "同一行业",
        "symbols": ["MSFT", "ORCL", "ADBE", "CRM"],
    },
    {
        "key": "mega",
        "label": "大型科技",
        "relation": "同一资产驱动",
        "symbols": ["AAPL", "MSFT", "GOOG", "AMZN", "META"],
    },
    {
        "key": "pharma",
        "label": "制药",
        "relation": "同一行业",
        "symbols": ["JNJ", "PFE", "MRK", "LLY", "ABBV"],
    },
    {
        "key": "retail",
        "label": "零售",
        "relation": "同一行业",
        "symbols": ["WMT", "COST", "TGT"],
    },
    {
        "key": "auto",
        "label": "汽车",
        "relation": "同一行业",
        "symbols": ["TSLA", "F", "GM"],
    },
    {
        "key": "credit",
        "label": "信用利差",
        "relation": "同一资产驱动",
        "symbols": ["HYG", "LQD"],
    },
    {
        "key": "gold",
        "label": "黄金",
        "relation": "同一资产驱动",
        "symbols": ["GLD", "NEM", "GOLD"],
    },
]

# 股票 vs 行业 / 大盘：经济关系更松，只作明确候选，不当默认同业。
CROSSES: list[dict] = [
    {"a": "NVDA", "b": "SOXX", "relation": "ETF与成分股", "group": "semis_etf", "label": "半导体ETF"},
    {"a": "AMD", "b": "SOXX", "relation": "ETF与成分股", "group": "semis_etf", "label": "半导体ETF"},
    {"a": "MU", "b": "SOXX", "relation": "ETF与成分股", "group": "semis_etf", "label": "半导体ETF"},
    {"a": "XOM", "b": "XLE", "relation": "ETF与成分股", "group": "energy_etf", "label": "能源ETF"},
    {"a": "QQQ", "b": "SPY", "relation": "同一资产驱动", "group": "index", "label": "指数"},
    {"a": "QQQ", "b": "TLT", "relation": "同一资产驱动", "group": "rates", "label": "利率–成长"},
]

# 反例：看起来相关，经济关系太松。
COUNTERS: list[dict] = [
    {
        "a": "MU",
        "b": "SPY",
        "relation": "大盘与个股（反例）",
        "group": "counter",
        "label": "反例",
        "counter": True,
    },
    {
        "a": "MU",
        "b": "GOOG",
        "relation": "都叫科技股（反例）",
        "group": "counter",
        "label": "反例",
        "counter": True,
    },
]


def _pair_key(a: str, b: str) -> tuple[str, str]:
    x, y = a.upper(), b.upper()
    return (x, y) if x < y else (y, x)


def list_groups() -> list[dict]:
    out = []
    for g in GROUPS:
        out.append({**g, "n": len(g["symbols"])})
    out.append({"key": "cross", "label": "对ETF/基准", "relation": "ETF与成分股", "symbols": [], "n": len(CROSSES)})
    out.append({"key": "counter", "label": "反例", "relation": "太松", "symbols": ["MU", "SPY", "GOOG"], "n": len(COUNTERS)})
    return out


def iter_candidates(*, include_cross: bool = True, include_counter: bool = True, groups: list[str] | None = None):
    """只产出有经济故事的一对，不在全市场两两相关。"""
    seen: set[tuple[str, str]] = set()
    allow = set(groups) if groups else None

    def _emit(a, b, relation, group, label, counter=False):
        if a == b:
            return
        key = _pair_key(a, b)
        if key in seen:
            return
        seen.add(key)
        yield {
            "leg_a": a.upper(),
            "leg_b": b.upper(),
            "relation": relation,
            "group": group,
            "group_label": label,
            "counter": bool(counter),
        }

    for g in GROUPS:
        if allow and g["key"] not in allow and "all" not in allow:
            continue
        syms = [s.upper() for s in g["symbols"]]
        for i, a in enumerate(syms):
            for b in syms[i + 1 :]:
                yield from _emit(a, b, g["relation"], g["key"], g["label"])

    if include_cross and (not allow or "cross" in allow or "all" in allow):
        for row in CROSSES:
            yield from _emit(row["a"], row["b"], row["relation"], row["group"], row["label"])

    if include_counter and (not allow or "counter" in allow or "all" in allow):
        for row in COUNTERS:
            yield from _emit(row["a"], row["b"], row["relation"], row["group"], row["label"], True)


def all_symbols(candidates: list[dict]) -> list[str]:
    names: set[str] = set()
    for row in candidates:
        names.add(row["leg_a"])
        names.add(row["leg_b"])
    return sorted(names)
