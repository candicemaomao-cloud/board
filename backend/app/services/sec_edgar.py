"""
SEC EDGAR 财报数据源
====================
Nasdaq 那个非官方接口经常拉不到最新一期的数字（尤其是刚发完财报的头几天）。
SEC EDGAR 是上市公司自己报送给监管机构的原始数据，比 Nasdaq 转手的数字更权威、
更新更及时（公司报表一提交，XBRL 结构化数据基本当天就能查到）。

用到两个官方接口，都不需要注册/付费，但 SEC 要求请求带一个能识别身份的
User-Agent（不遵守会被限流/封禁），不是拿来收集用户信息的：
  1. company_tickers.json —— 股票代码到 CIK（SEC 内部公司编号）的映射表
  2. companyfacts API —— 这家公司历史上报送过的所有 XBRL 结构化财务数据
  3. submissions API —— 这家公司历史上报送过的所有文件列表（10-K/10-Q 等），
     用来找到最新一期年报/季报，再去抓文件正文里的「Item 1A 风险因素」章节
     （这部分是自由文本，SEC 的结构化 XBRL 接口里没有，只能从文件原文里解析）。

参考: https://www.sec.gov/os/webmaster-faq#developers
"""
from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser
from time import time

import httpx

SEC_BASE = "https://www.sec.gov"
DATA_BASE = "https://data.sec.gov"
# SEC 要求带上可识别身份的 User-Agent（应用名 + 联系方式），不遵守容易被限流。
USER_AGENT = "PersonalTradeBoard/1.0 (research tool; contact: admin@tradeboard.local)"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

TICKER_TTL = 24 * 3600
FACTS_TTL = 6 * 3600
SUBMISSIONS_TTL = 6 * 3600
RISK_TTL = 24 * 3600

_ticker_cache: tuple[float, dict] | None = None
_facts_cache: dict[int, tuple[float, dict]] = {}
_submissions_cache: dict[int, tuple[float, dict]] = {}
_risk_cache: dict[int, tuple[float, dict]] = {}


class SecError(Exception):
    pass


def _client() -> httpx.Client:
    return httpx.Client(timeout=20.0, headers=HEADERS, follow_redirects=True)


def _ticker_map() -> dict[str, int]:
    global _ticker_cache
    if _ticker_cache and time() - _ticker_cache[0] < TICKER_TTL:
        return _ticker_cache[1]
    with _client() as client:
        res = client.get(f"{SEC_BASE}/files/company_tickers.json")
        res.raise_for_status()
        raw = res.json()
    mapping: dict[str, int] = {}
    for row in raw.values():
        ticker = str(row.get("ticker") or "").upper()
        cik = row.get("cik_str")
        if ticker and cik:
            mapping[ticker] = int(cik)
    _ticker_cache = (time(), mapping)
    return mapping


def cik_for(symbol: str) -> int:
    cik = _ticker_map().get((symbol or "").strip().upper())
    if not cik:
        raise SecError(f"SEC 找不到 {symbol} 对应的 CIK（可能不是美股上市公司，或者代码不对）")
    return cik


def _company_facts(cik: int) -> dict:
    hit = _facts_cache.get(cik)
    if hit and time() - hit[0] < FACTS_TTL:
        return hit[1]
    with _client() as client:
        res = client.get(f"{DATA_BASE}/api/xbrl/companyfacts/CIK{cik:010d}.json")
        if res.status_code == 404:
            raise SecError("SEC 没有这家公司的 XBRL 结构化财报数据")
        res.raise_for_status()
        data = res.json()
    _facts_cache[cik] = (time(), data)
    return data


def _submissions(cik: int) -> dict:
    hit = _submissions_cache.get(cik)
    if hit and time() - hit[0] < SUBMISSIONS_TTL:
        return hit[1]
    with _client() as client:
        res = client.get(f"{DATA_BASE}/submissions/CIK{cik:010d}.json")
        res.raise_for_status()
        data = res.json()
    _submissions_cache[cik] = (time(), data)
    return data


# ---------------------------------------------------------------------------
# XBRL 财务数据：从 companyfacts 里挑出我们要展示的那几个科目，对齐成跟
# app/services/fundamentals.py（Nasdaq 版）一样的 {periods, rows} 结构，
# 行名特意用跟 Nasdaq 版一样的英文名（'Total Revenue' 等），这样前端
# FundamentalsPage.vue 现成的 rowName()/cell() 中文映射和格式化不用改。
# ---------------------------------------------------------------------------

def _pick_concept(facts: dict, candidates: list[str]) -> list[dict]:
    us_gaap = ((facts.get("facts") or {}).get("us-gaap") or {})
    for tag in candidates:
        node = us_gaap.get(tag)
        entries = ((node or {}).get("units") or {}).get("USD")
        if entries:
            return entries
    return []


def _period_days(entry: dict) -> int | None:
    start, end = entry.get("start"), entry.get("end")
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None


def _dedup_series(entries: list[dict], *, forms: set[str], quarterly: bool) -> list[dict]:
    """同一个 end 日期可能被好几次提交重复报送（比如后续季报里带上上一期数字做
    对比），保留 filed 日期最新的一条；quarterly=True 只要报告期跨度约一个季度
    (70~100天) 的记录，年度只要跨度约一年 (350~380天) 的，避免把累计数/半年数
    误当成单季度数字。"""
    by_end: dict[str, dict] = {}
    for e in entries:
        if e.get("form") not in forms:
            continue
        end = e.get("end")
        if not end:
            continue
        days = _period_days(e)
        if days is not None:
            if quarterly and not (70 <= days <= 100):
                continue
            if not quarterly and not (350 <= days <= 380):
                continue
        prev = by_end.get(end)
        if not prev or (e.get("filed") or "") >= (prev.get("filed") or ""):
            by_end[end] = e
    return sorted(by_end.values(), key=lambda x: x["end"], reverse=True)


CONCEPTS = {
    "Total Revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "Gross Profit": ["GrossProfit"],
    "Operating Income": ["OperatingIncomeLoss"],
    "Net Income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "Cash and Cash Equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "Total Current Assets": ["AssetsCurrent"],
    "Total Assets": ["Assets"],
    "Total Current Liabilities": ["LiabilitiesCurrent"],
    "Long-Term Debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "Total Liabilities": ["Liabilities"],
    "Total Equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "Net Cash Flow-Operating": ["NetCashProvidedByUsedInOperatingActivities"],
    "Capital Expenditures": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "Net Cash Flow": [
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
        "CashAndCashEquivalentsPeriodIncreaseDecrease",
    ],
}

# 资产负债表科目是「时点」余额（instant），不是「区间」流量（duration），
# companyfacts 里这些标签的记录没有 start，只有 end——判断季度/年度不能靠
# start/end 跨度，直接按 fp 字段（Q1/Q2/Q3/FY）分组即可。
BALANCE_SHEET_ROWS = {
    "Cash and Cash Equivalents", "Total Current Assets", "Total Assets",
    "Total Current Liabilities", "Long-Term Debt", "Total Liabilities", "Total Equity",
}
SIGN_FLIP = {"Capital Expenditures"}


def _dedup_instant_series(entries: list[dict], *, forms: set[str], quarterly: bool) -> list[dict]:
    by_end: dict[str, dict] = {}
    want_fp = {"Q1", "Q2", "Q3"} if quarterly else {"FY"}
    for e in entries:
        if e.get("form") not in forms:
            continue
        if quarterly and e.get("fp") not in want_fp:
            continue
        end = e.get("end")
        if not end:
            continue
        prev = by_end.get(end)
        if not prev or (e.get("filed") or "") >= (prev.get("filed") or ""):
            by_end[end] = e
    return sorted(by_end.values(), key=lambda x: x["end"], reverse=True)


def _build_statement(facts: dict, row_names: list[str], *, quarterly: bool, limit: int = 6) -> dict:
    forms = {"10-Q"} if quarterly else {"10-K"}
    # periods 以 Total Revenue（流量科目里报送最稳定的一个）对齐；如果这行本身
    # 没数据（罕见），退回用第一个能取到数据的科目对齐。
    anchor_candidates = [n for n in ["Total Revenue", "Net Income"] if n in row_names] or row_names[:1]
    periods: list[str] = []
    for name in anchor_candidates:
        raw = _pick_concept(facts, CONCEPTS[name])
        series = _dedup_series(raw, forms=forms, quarterly=quarterly)[:limit]
        if series:
            periods = [e["end"] for e in series]
            break

    rows, by_name = [], {}
    for name in row_names:
        raw = _pick_concept(facts, CONCEPTS[name])
        if name in BALANCE_SHEET_ROWS:
            series = _dedup_instant_series(raw, forms=forms, quarterly=quarterly)
        else:
            series = _dedup_series(raw, forms=forms, quarterly=quarterly)
        if not periods and series:
            periods = [e["end"] for e in series[:limit]]
        by_end = {e["end"]: e["val"] for e in series}
        values = [by_end.get(p) for p in periods]
        if all(v is None for v in values):
            continue
        if name in SIGN_FLIP:
            # XBRL 里 PaymentsToAcquirePropertyPlantAndEquipment 这类"Payments"
            # 科目按惯例报的是正数（花了多少钱），但 fundamentals.py 里下游算
            # 自由现金流(_fcf_row)用的是 Nasdaq 的口径——资本开支存成负数（现金
            # 流出），公式是 cfo + capex。这里翻个符号对齐 Nasdaq 的口径，这样
            # fundamentals.py 现成的 _fcf_row()/_cash_rows() 就能直接复用，不用
            # 为两个数据源分别维护一套符号不同的公式。
            values = [None if v is None else -v for v in values]
        row = {"name": name, "values": values}
        rows.append(row)
        by_name[name.lower()] = row
    return {"periods": periods, "rows": rows, "by_name": by_name}


def _ratio_row(name: str, num_row: dict | None, den_row: dict | None, *, pct100: bool = False) -> dict | None:
    """pct100=True 用于 Current Ratio：前端 cell() 对这一行的格式化是
    `(value / 100).toFixed(2) + 'x'`，也就是存的时候要是「比值 * 100」
    （比如 1.05x 存成 105），不是百分比。其余（Margin 类）前端直接当百分比数字
    显示（比如 45.2 表示 45.2%），存「比值 * 100」正好也是百分比数值，公式一样，
    只是含义不同——所以这两种其实是同一个公式，pct100 参数留着只是为了在调用处
    读起来清楚这一行是「倍数」还是「百分比」。"""
    if not num_row or not den_row:
        return None
    n = min(len(num_row["values"]), len(den_row["values"]))
    values = []
    for i in range(n):
        a, b = num_row["values"][i], den_row["values"][i]
        values.append(None if a is None or b in (None, 0) else a / b * 100)
    return {"name": name, "values": values}


def _rows_to_table(built: dict) -> dict:
    """把 _build_statement() 的 {periods, rows, by_name} 结果原样传出去——
    跟 fundamentals.py 里 Nasdaq 版 _parse_table() 的输出结构一致，
    这样 fundamentals.py 现成的 _notes()/_pick_rows()/_row() 都能直接复用，
    不用为 SEC 数据源另外写一套算同比/环比的逻辑。"""
    return {"periods": built["periods"], "rows": built["rows"], "by_name": built["by_name"]}


def build_financials(cik: int) -> dict:
    """返回 {"annual": {...}, "quarter": {...}}，每个里面是
    {"income":table, "balance":table, "cash":table, "ratios":table}，
    table 形状跟 fundamentals.py 的 _parse_table() 输出一致（periods/rows/by_name），
    可以直接喂给 fundamentals.py 现有的 _notes()/_pick_rows()/_cash_rows()。"""
    facts = _company_facts(cik)
    income_names = ["Total Revenue", "Gross Profit", "Operating Income", "Net Income"]
    balance_names = [
        "Cash and Cash Equivalents", "Total Current Assets", "Total Assets",
        "Total Current Liabilities", "Long-Term Debt", "Total Liabilities", "Total Equity",
    ]
    cash_names = ["Net Cash Flow-Operating", "Capital Expenditures", "Net Cash Flow"]

    def one(quarterly: bool) -> dict:
        income = _build_statement(facts, income_names, quarterly=quarterly)
        balance = _build_statement(facts, balance_names, quarterly=quarterly)
        cash = _build_statement(facts, cash_names, quarterly=quarterly)

        by_income = {r["name"]: r for r in income["rows"]}
        rev, gp = by_income.get("Total Revenue"), by_income.get("Gross Profit")
        op, ni = by_income.get("Operating Income"), by_income.get("Net Income")
        by_balance = {r["name"]: r for r in balance["rows"]}
        cur_a, cur_l = by_balance.get("Total Current Assets"), by_balance.get("Total Current Liabilities")

        ratio_rows = []
        for r in (
            _ratio_row("Current Ratio", cur_a, cur_l, pct100=True),
            _ratio_row("Gross Margin", gp, rev),
            _ratio_row("Operating Margin", op, rev),
            _ratio_row("Profit Margin", ni, rev),
        ):
            if r:
                ratio_rows.append(r)
        ratios = {
            "periods": income["periods"], "rows": ratio_rows,
            "by_name": {r["name"].lower(): r for r in ratio_rows},
        }

        return {
            "income": _rows_to_table(income),
            "balance": _rows_to_table(balance),
            "cash": _rows_to_table(cash),
            "ratios": ratios,
        }

    return {"quarter": one(True), "annual": one(False)}


# ---------------------------------------------------------------------------
# 风险因素 (Item 1A)：结构化 XBRL 接口里没有这部分，得去最新一期 10-K 的
# filing 原文里解析。SEC 的 filing 是普通 HTML 文件，这里用标准库
# html.parser 抽取纯文本（不引入 bs4/lxml 依赖），再用章节标题的正则定位
# 「Item 1A. Risk Factors」到下一个 Item 之间的内容。
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "tr", "br", "li"):
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.chunks)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_ITEM_1A_RE = re.compile(r"item\s*1a\.?\s*risk\s*factors", re.IGNORECASE)
_NEXT_ITEM_RE = re.compile(
    r"item\s*(1b|2)\.?\s*(unresolved\s*staff\s*comments|properties)?", re.IGNORECASE
)


def _extract_risk_factors_text(html: str) -> str | None:
    text = _html_to_text(html)
    matches = list(_ITEM_1A_RE.finditer(text))
    if not matches:
        return None
    # 一份文件里"Item 1A"经常在目录里也出现一次，真正的正文一般是最后一次匹配、
    # 且后面跟着的内容明显更长；用「匹配到的位置里，后面剩余文本最长的那个当作
    # 目录项，取其余里最早出现、且离结尾还有实质内容的一个」这个启发式规则太复杂，
    # 简化成：从最后一个匹配开始找，最后一次出现的通常就是正文开头。
    start = matches[-1].end()
    rest = text[start:]
    end_match = _NEXT_ITEM_RE.search(rest)
    body = rest[: end_match.start()] if end_match else rest
    body = body.strip()
    if len(body) < 200:
        # 正文太短，说明启发式规则没抓对（比如最后一次匹配其实还是目录/引用），
        # 退回用第一次出现的位置试一次。
        start = matches[0].end()
        rest = text[start:]
        end_match = _NEXT_ITEM_RE.search(rest)
        body = (rest[: end_match.start()] if end_match else rest).strip()
    return body or None


def get_risk_factors(cik: int) -> dict | None:
    hit = _risk_cache.get(cik)
    if hit and time() - hit[0] < RISK_TTL:
        return hit[1]

    submissions = _submissions(cik)
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    dates = recent.get("filingDate") or []

    idx = next((i for i, f in enumerate(forms) if f == "10-K"), None)
    if idx is None:
        return None

    accession = accessions[idx].replace("-", "")
    primary_doc = docs[idx]
    filed = dates[idx] if idx < len(dates) else None
    url = f"{SEC_BASE}/Archives/edgar/data/{cik}/{accession}/{primary_doc}"

    with _client() as client:
        res = client.get(url)
        res.raise_for_status()
        html = res.text

    body = _extract_risk_factors_text(html)
    result = None
    if body:
        # 太长的话前端一屏根本看不完，截到一个足够读完整体轮廓的长度，并按空行
        # 切成段落，方便前端渲染。
        truncated = len(body) > 24000
        body = body[:24000]
        paragraphs = [p.strip() for p in body.split("\n") if p.strip() and len(p.strip()) > 2]
        result = {
            "form": "10-K", "filed": filed, "url": url,
            "paragraphs": paragraphs, "truncated": truncated,
        }
    _risk_cache[cik] = (time(), result)
    return result
