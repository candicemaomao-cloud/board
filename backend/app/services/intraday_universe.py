"""日内配对：两只腿，对冲要有经济关系。

优先同业股票，或股票 vs 行业 ETF（SOXX / XLF / XLE）。
不对 SPY 当主对冲——那只是剥大盘 β，找不出能交易的配对。
"""

from __future__ import annotations

# 对冲腿上限：主仓 + 最多 1 只对冲 = 两只标的。偶尔允许第 2 只（合计 3 只）。
MAX_HEDGES = 2

ETF_SYMS = {
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "VOO",
    "VTI",
    "IVV",
    "SOXX",
    "SMH",
    "XLK",
    "XLF",
    "XLE",
    "XLI",
    "XLY",
    "XLP",
    "XLB",
    "XLU",
    "XLRE",
    "XLC",
    "TLT",
    "GLD",
    "SLV",
    "HYG",
    "USO",
    "ARKK",
    "KRE",
}


def is_etf(sym: str) -> bool:
    return (sym or "").strip().upper() in ETF_SYMS


# 点「全部」时扫这些。不含 vs 大盘。
DEFAULT_BASKET_KEYS = (
    "semis",
    "software",
    "ads",
    "finance",
    "energy",
    "consumer",
    "industrial",
    "etf",
)

BASKETS: list[dict] = [
    {
        "key": "semis",
        "label": "半导体",
        "kind": "peer",
        "kind_label": "同业 / SOXX",
        "note": "NVDA/AMD、AMAT/LRCX，或个股 vs SOXX/SMH。不是 vs SPY。龙头财报日整板块重定价，不做。",
        "leaders": ["NVDA", "AVGO", "TSM"],
        "pairs": [
            ("NVDA", "AMD"),
            ("NVDA", "AVGO"),
            ("AMD", "MU"),
            ("TSM", "NVDA"),
            ("AMAT", "LRCX"),
            ("LRCX", "KLAC"),
            ("ADI", "TXN"),
            ("QCOM", "AVGO"),
            ("MRVL", "AVGO"),
            ("SOXX", "SMH"),
        ],
        "targets": ["NVDA", "AMD", "AVGO", "TSM", "MU", "INTC", "QCOM", "AMAT", "LRCX", "KLAC", "ADI"],
        "factors": ["SOXX"],
        "factor_map": {
            "MU": ["SMH"],
            "INTC": ["SOXX"],
        },
    },
    {
        "key": "software",
        "label": "软件云",
        "kind": "peer",
        "kind_label": "同业软件",
        "note": "同一软件赛道，两只腿。MSFT/CRM 财报会带动整组，窗口内不做。",
        "leaders": ["MSFT", "CRM", "ORCL"],
        "pairs": [
            ("MSFT", "ORCL"),
            ("CRM", "NOW"),
            ("PANW", "CRWD"),
            ("ADBE", "INTU"),
            ("CRWD", "DDOG"),
            ("SNPS", "CDNS"),
        ],
        "targets": ["CRM", "NOW", "PANW", "CRWD", "ADBE", "INTU", "WDAY", "DDOG"],
        "factors": ["XLK"],
    },
    {
        "key": "ads",
        "label": "广告平台",
        "kind": "peer",
        "kind_label": "同业平台",
        "note": "GOOG/META 同业，不是 vs QQQ。任一只出财报都禁止淡化。",
        "leaders": ["GOOG", "META"],
        "pairs": [
            ("GOOG", "META"),
        ],
        "targets": [],
        "factors": [],
    },
    {
        "key": "finance",
        "label": "金融",
        "kind": "peer",
        "kind_label": "同业 / XLF",
        "note": "JPM/BAC、V/MA，或银行 vs XLF。JPM 财报日银行一起动，不做。",
        "leaders": ["JPM", "GS"],
        "pairs": [
            ("JPM", "BAC"),
            ("GS", "MS"),
            ("V", "MA"),
            ("WFC", "C"),
        ],
        "targets": ["JPM", "BAC", "WFC", "GS", "MS", "V", "MA"],
        "factors": ["XLF"],
    },
    {
        "key": "energy",
        "label": "能源",
        "kind": "peer",
        "kind_label": "同业 / XLE",
        "note": "XOM/CVX，或能源股 vs XLE。XOM 财报日能源重定价，不做。",
        "leaders": ["XOM", "CVX"],
        "pairs": [
            ("XOM", "CVX"),
            ("COP", "EOG"),
            ("MPC", "PSX"),
        ],
        "targets": ["XOM", "CVX", "COP", "SLB", "EOG"],
        "factors": ["XLE"],
    },
    {
        "key": "consumer",
        "label": "消费",
        "kind": "peer",
        "kind_label": "同业消费",
        "note": "HD/LOW、KO/PEP、WMT/COST。两腿财报窗口不做。",
        "leaders": ["WMT", "HD"],
        "pairs": [
            ("HD", "LOW"),
            ("KO", "PEP"),
            ("WMT", "COST"),
            ("MCD", "SBUX"),
        ],
        "targets": [],
        "factors": [],
    },
    {
        "key": "industrial",
        "label": "工业快递",
        "kind": "peer",
        "kind_label": "同业工业",
        "note": "UPS/FDX、CAT/DE。两腿或 CAT 财报窗口不做。",
        "leaders": ["CAT"],
        "pairs": [
            ("FDX", "UPS"),
            ("CAT", "DE"),
            ("UNP", "CSX"),
            ("LMT", "RTX"),
        ],
        "targets": [],
        "factors": [],
    },
    {
        "key": "etf",
        "label": "行业ETF对",
        "kind": "etf_etf",
        "kind_label": "行业ETF↔行业ETF",
        "note": "SOXX/SMH、XLK/QQQ。不对 SPY。NVDA 等龙头财报日这两对也不做。",
        "leaders": ["NVDA", "AVGO", "TSM", "MSFT"],
        "pairs": [
            ("SOXX", "SMH"),
            ("XLK", "QQQ"),
        ],
        "targets": [],
        "factors": [],
    },
    {
        "key": "highbeta",
        "label": "对照：vs 大盘",
        "kind": "high_beta",
        "kind_label": "个股 vs SPY（对照）",
        "note": "只剥大盘 β，不是配对。默认不扫。",
        "leaders": [],
        "pairs": [],
        "targets": ["MU", "NVDA", "TSLA", "AMD", "SMCI", "COIN"],
        "factors": ["SPY"],
    },
]


def clip_hedges(factors, target: str | None = None) -> list[str]:
    """最多 2 个对冲（合计最多 3 只标的）。"""
    t = (target or "").strip().upper()
    out: list[str] = []
    for raw in factors or []:
        s = str(raw or "").strip().upper()
        if not s or s == t or s in out:
            continue
        out.append(s)
        if len(out) >= MAX_HEDGES:
            break
    return out


def _emit(basket: dict, target: str, factors: list[str], relation: str) -> dict | None:
    hedges = clip_hedges(factors, target)
    if not hedges:
        return None
    return {
        "target": target,
        "factors": hedges,
        "basket": basket["key"],
        "basket_label": basket["label"],
        "kind": basket["kind"],
        "kind_label": basket["kind_label"],
        "note": basket.get("note") or "",
        "relation": relation,
        "leaders": [s for s in (basket.get("leaders") or []) if s and s != target],
    }


def iter_specs(baskets: list[str] | None = None) -> list[dict]:
    want = {k.strip() for k in (baskets or []) if k and k.strip() and k != "all"}
    if not want:
        want = set(DEFAULT_BASKET_KEYS)
    rows = []
    seen: set[tuple[str, str]] = set()
    for b in BASKETS:
        if b["key"] not in want:
            continue
        for a, hedge in b.get("pairs") or []:
            row = _emit(b, a, [hedge], "同业两腿")
            if not row:
                continue
            key = (row["target"], row["factors"][0])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        for target in b.get("targets") or []:
            mapped = (b.get("factor_map") or {}).get(target)
            factors = list(mapped or b.get("factors") or [])
            row = _emit(b, target, factors, "股票↔行业ETF")
            if not row:
                continue
            key = (row["target"], row["factors"][0])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def list_baskets() -> list[dict]:
    out = []
    for b in BASKETS:
        n = sum(1 for row in iter_specs([b["key"]]))
        out.append(
            {
                "key": b["key"],
                "label": b["label"],
                "kind": b["kind"],
                "kind_label": b["kind_label"],
                "note": b["note"],
                "n": n,
                "default": b["key"] in DEFAULT_BASKET_KEYS,
            }
        )
    return out


def all_symbols(specs: list[dict], extra: list[str] | None = None) -> list[str]:
    seen: list[str] = []
    for spec in specs:
        for sym in [spec["target"], *spec["factors"]]:
            if sym not in seen:
                seen.append(sym)
    for sym in extra or []:
        if sym not in seen:
            seen.append(sym)
    return seen
