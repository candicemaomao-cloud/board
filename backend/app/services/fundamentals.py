from __future__ import annotations

from datetime import datetime, timezone
from time import time
from urllib.parse import quote

import httpx

from app.services import sec_edgar
from app.services.quotes import HEADERS

NASDAQ = "https://api.nasdaq.com"
TTL = 30 * 60
_cache: dict[str, tuple[float, dict]] = {}

NASDAQ_HEADERS = {
    **HEADERS,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


class FundamentalsError(Exception):
    pass


def _cached(symbol: str) -> dict:
    key = symbol.upper()
    hit = _cache.get(key)
    if hit and time() - hit[0] < TTL:
        return hit[1]
    value = _build(key)
    _cache[key] = (time(), value)
    return value


def _get(client: httpx.Client, path: str) -> dict:
    res = client.get(f"{NASDAQ}{path}")
    res.raise_for_status()
    payload = res.json()
    if not isinstance(payload, dict):
        return {}
    return payload.get("data") or {}


def _num(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text or text in {"--", "N/A", "NA", "n/a", "—"}:
        return None
    neg = text.startswith("(") and text.endswith(")")
    text = text.replace("$", "").replace(",", "").replace("%", "")
    text = text.replace("(", "").replace(")", "").replace("+", "").strip()
    if text in {"", "--", "—"}:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return -abs(value) if neg else value


def _money(raw) -> float | None:
    value = _num(raw)
    if value is None:
        return None
    return value * 1000


def _pct_points(raw) -> float | None:
    return _num(raw)


def _chg(now, prev) -> float | None:
    if now is None or prev in (None, 0):
        return None
    return (now - prev) / abs(prev)


def _label(item: dict | None, key: str, fallback: str = "") -> str:
    row = (item or {}).get(key) or {}
    if isinstance(row, dict):
        return str(row.get("value") or fallback).strip()
    return str(row or fallback).strip()


def _parse_table(table: dict | None, money: bool = True) -> dict:
    table = table or {}
    headers = table.get("headers") or {}
    periods = [headers.get(f"value{i}") for i in range(2, 6)]
    periods = [p for p in periods if p]
    rows = []
    by_name = {}
    for item in table.get("rows") or []:
        name = str(item.get("value1") or "").strip()
        if not name:
            continue
        values = []
        for i in range(2, 2 + len(periods)):
            raw = item.get(f"value{i}")
            values.append(_money(raw) if money else _pct_points(raw))
        row = {"name": name, "values": values}
        rows.append(row)
        by_name[name.lower()] = row
    return {"periods": periods, "rows": rows, "by_name": by_name, "as_of": table.get("asOf") or ""}


def _row(table: dict, *names: str) -> list:
    by_name = table.get("by_name") or {}
    for name in names:
        hit = by_name.get(name.lower())
        if hit:
            return hit.get("values") or []
    return []


def _first(values: list):
    return values[0] if values else None


def _second(values: list):
    return values[1] if len(values) > 1 else None


def _pick_rows(table: dict, names: list[str]) -> list[dict]:
    by_name = table.get("by_name") or {}
    out = []
    for name in names:
        hit = by_name.get(name.lower())
        if hit:
            out.append(hit)
    return out


def _tone(value, *, invert: bool = False) -> str:
    if value is None:
        return ""
    good = value > 0
    if invert:
        good = value < 0
    if value == 0:
        return ""
    return "up" if good else "down"


def _pct_text(value) -> str | None:
    if value is None:
        return None
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def _usd(n) -> str:
    if n is None:
        return "—"
    sign = "-" if n < 0 else ""
    absn = abs(n)
    if absn >= 1_000_000_000:
        return f"{sign}${absn / 1_000_000_000:.1f}B"
    if absn >= 1_000_000:
        return f"{sign}${absn / 1_000_000:.1f}M"
    return f"{sign}${absn:,.0f}"


def _fcf_row(cash_table: dict) -> dict | None:
    cfo = _row(cash_table, "Net Cash Flow-Operating")
    capex = _row(cash_table, "Capital Expenditures")
    if not cfo or not capex:
        return None
    n = min(len(cfo), len(capex))
    if not n:
        return None
    values = []
    for i in range(n):
        a, b = cfo[i], capex[i]
        values.append(None if a is None or b is None else a + b)
    return {"name": "Free Cash Flow", "values": values}


def _cash_rows(cash_table: dict, names: list[str]) -> list[dict]:
    rows = _pick_rows(cash_table, names)
    fcf = _fcf_row(cash_table)
    if not fcf:
        return rows
    out = []
    inserted = False
    for row in rows:
        out.append(row)
        if row.get("name") == "Capital Expenditures":
            out.append(fcf)
            inserted = True
    if not inserted:
        out.append(fcf)
    return out


def _notes(profile: dict, annual: dict, quarter: dict, surprise: list, target) -> list[dict]:
    notes = []
    a_rev = _row(annual["income"], "Total Revenue")
    a_ni = _row(annual["income"], "Net Income", "Net Income Applicable to Common Shareholders")
    q_rev = _row(quarter["income"], "Total Revenue")
    q_ni = _row(quarter["income"], "Net Income", "Net Income Applicable to Common Shareholders")
    q_gm = _row(quarter["ratios"], "Gross Margin")
    q_om = _row(quarter["ratios"], "Operating Margin")
    q_cfo = _row(quarter["cash"], "Net Cash Flow-Operating")
    q_capex = _row(quarter["cash"], "Capital Expenditures")
    q_ncf = _row(quarter["cash"], "Net Cash Flow")
    q_cash = _row(quarter["balance"], "Cash and Cash Equivalents")
    q_debt = _row(quarter["balance"], "Long-Term Debt")
    q_equity = _row(quarter["balance"], "Total Equity")
    q_period = ((quarter.get("income") or {}).get("periods") or [None])[0] or "最近一季"

    yoy_rev = _chg(_first(a_rev), _second(a_rev))
    yoy_ni = _chg(_first(a_ni), _second(a_ni))
    qoq_rev = _chg(_first(q_rev), _second(q_rev))
    qoq_ni = _chg(_first(q_ni), _second(q_ni))
    gm_now, gm_prev = _first(q_gm), _second(q_gm)
    om_now = _first(q_om)
    cfo, capex = _first(q_cfo), _first(q_capex)
    ncf, cash_bal = _first(q_ncf), _first(q_cash)
    debt, equity = _first(q_debt), _first(q_equity)

    if yoy_rev is not None:
        notes.append({
            "title": "年度营收",
            "text": f"最新财年营收同比 {_pct_text(yoy_rev)}。",
            "tone": _tone(yoy_rev),
        })
    if yoy_ni is not None:
        notes.append({
            "title": "年度净利",
            "text": f"最新财年净利润同比 {_pct_text(yoy_ni)}。",
            "tone": _tone(yoy_ni),
        })
    if qoq_rev is not None:
        notes.append({
            "title": "季度营收",
            "text": f"最近一季营收环比 {_pct_text(qoq_rev)}。",
            "tone": _tone(qoq_rev),
        })
    if qoq_ni is not None:
        notes.append({
            "title": "季度净利",
            "text": f"最近一季净利润环比 {_pct_text(qoq_ni)}。",
            "tone": _tone(qoq_ni),
        })
    if gm_now is not None:
        delta = None if gm_prev is None else gm_now - gm_prev
        extra = f"，较上季 {delta:+.1f} 个百分点" if delta is not None else ""
        notes.append({
            "title": "毛利率",
            "text": f"最近一季毛利率 {gm_now:.1f}%{extra}。",
            "tone": _tone(delta),
        })
    if om_now is not None:
        notes.append({
            "title": "经营利润率",
            "text": f"最近一季经营利润率 {om_now:.1f}%。{'偏低，费用或成本压力大。' if om_now < 5 else '还过得去。' if om_now < 15 else '利润率不错。'}",
            "tone": "down" if om_now < 5 else "up" if om_now >= 15 else "",
        })
    if cfo is not None and capex is not None:
        fcf = cfo + capex
        extra = ""
        if ncf is not None and cash_bal is not None:
            extra = f" 期末现金 {_usd(cash_bal)}，现金净变动 {_usd(ncf)}（卖投资/融资仍可能让现金增加）。"
        notes.append({
            "title": "自由现金流",
            "text": (
                f"{q_period} 自由现金流 {_usd(fcf)}（经营现金流 {_usd(cfo)}，资本开支 {_usd(capex)}）。"
                + ("资本开支大于经营现金流。" if fcf < 0 else "经营现金流覆盖资本开支。")
                + extra
            ),
            "tone": "up" if fcf >= 0 else "down",
        })
    if debt is not None and equity not in (None, 0):
        ratio = debt / equity
        notes.append({
            "title": "长期负债",
            "text": f"长期负债 / 股东权益 = {ratio:.2f}。{'杠杆不高。' if ratio < 0.5 else '杠杆中等。' if ratio < 1.2 else '负债偏重。'}",
            "tone": "up" if ratio < 0.5 else "down" if ratio >= 1.2 else "",
        })
    if surprise:
        last = surprise[0]
        surprise_pct = _num(last.get("surprise"))
        notes.append({
            "title": "EPS 预期",
            "text": f"{last.get('period') or '最近一季'} 实际 EPS {last.get('eps')}，一致预期 {last.get('estimate')}，意外 {last.get('surprise_text') or '—'}。",
            "tone": _tone(surprise_pct),
        })
    price = profile.get("price")
    if price and target:
        gap = (target - price) / price
        notes.append({
            "title": "分析师目标",
            "text": f"一年目标价相对现价 {_pct_text(gap)}。",
            "tone": _tone(gap),
        })
    if not notes:
        notes.append({"title": "提示", "text": "财报数字已拉到，但还不够算同比，先看下面的表。", "tone": ""})
    return notes


def _surprise_rows(raw: dict) -> list[dict]:
    table = (raw or {}).get("earningsSurpriseTable") or {}
    rows = []
    for item in table.get("rows") or []:
        rows.append({
            "period": item.get("fiscalQtrEnd") or "",
            "reported": item.get("dateReported") or "",
            "eps": _num(item.get("eps")),
            "estimate": _num(item.get("consensusForecast")),
            "surprise": _num(item.get("percentageSurprise")),
            "surprise_text": None if item.get("percentageSurprise") in (None, "") else f"{item.get('percentageSurprise')}%",
        })
    return rows


def _nasdaq_financials(client: httpx.Client, code: str) -> tuple[dict, dict]:
    annual_raw = _get(client, f"/api/company/{quote(code, safe='')}/financials?frequency=1")
    quarter_raw = _get(client, f"/api/company/{quote(code, safe='')}/financials?frequency=2")
    annual = {
        "income": _parse_table(annual_raw.get("incomeStatementTable"), True),
        "balance": _parse_table(annual_raw.get("balanceSheetTable"), True),
        "cash": _parse_table(annual_raw.get("cashFlowTable"), True),
        "ratios": _parse_table(annual_raw.get("financialRatiosTable"), False),
    }
    quarter = {
        "income": _parse_table(quarter_raw.get("incomeStatementTable"), True),
        "balance": _parse_table(quarter_raw.get("balanceSheetTable"), True),
        "cash": _parse_table(quarter_raw.get("cashFlowTable"), True),
        "ratios": _parse_table(quarter_raw.get("financialRatiosTable"), False),
    }
    return annual, quarter


def _yahoo_ttm(code: str) -> dict:
    """Best-effort valuation and trailing-twelve-month metrics."""
    try:
        from yahooquery import Ticker
        ticker = Ticker(code, timeout=18)
        modules = ticker.get_modules(['summaryDetail', 'financialData', 'defaultKeyStatistics'])
        payload = modules.get(code, {}) if isinstance(modules, dict) else {}
        if not isinstance(payload, dict):
            return {}
        detail = payload.get('summaryDetail') or {}
        financial = payload.get('financialData') or {}
        stats = payload.get('defaultKeyStatistics') or {}
        def raw(value):
            return _num(value.get('raw')) if isinstance(value, dict) else _num(value)
        def percent(value):
            number = raw(value)
            return number * 100 if number is not None else None
        def date(value):
            timestamp = raw(value)
            if timestamp is None:
                return None
            try:
                return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
            except (ValueError, OverflowError, OSError):
                return None
        trailing_pe = raw(detail.get('trailingPE'))
        if trailing_pe is not None and trailing_pe <= 0:
            trailing_pe = None
        return {
            'trailing_pe': trailing_pe,
            'forward_pe': raw(detail.get('forwardPE')),
            'eps_ttm': raw(stats.get('trailingEps')),
            'revenue_ttm': raw(financial.get('totalRevenue')),
            'free_cash_flow_ttm': raw(financial.get('freeCashflow')),
            'net_margin_ttm': (raw(financial.get('profitMargins')) * 100
                               if raw(financial.get('profitMargins')) is not None else None),
            'annual_dividend_rate': raw(detail.get('dividendRate')) or raw(detail.get('trailingAnnualDividendRate')),
            'dividend_yield': percent(detail.get('dividendYield')),
            'payout_ratio': percent(detail.get('payoutRatio')),
            'ex_dividend_date': date(detail.get('exDividendDate')),
            'five_year_avg_dividend_yield': raw(detail.get('fiveYearAvgDividendYield')),
            'as_of': 'Yahoo Finance · TTM/当前估值',
        }
    except Exception:
        return {}


def _build(symbol: str) -> dict:
    code = (symbol or "").strip().upper()
    if not code or code.endswith(("USDT", "USDC")):
        raise FundamentalsError("请输入美股代码，例如 TSLA / AAPL")
    with httpx.Client(timeout=25.0, headers=NASDAQ_HEADERS, follow_redirects=True) as client:
        try:
            info = _get(client, f"/api/quote/{quote(code, safe='')}/info?assetclass=stocks")
            summary = _get(client, f"/api/quote/{quote(code, safe='')}/summary?assetclass=stocks")
            profile = _get(client, f"/api/company/{quote(code, safe='')}/company-profile")
            surprise_raw = _get(client, f"/api/company/{quote(code, safe='')}/earnings-surprise")
        except httpx.HTTPError as exc:
            raise FundamentalsError(f"拉不到 {code} 的财报") from exc

        # 财务报表数字优先用 SEC EDGAR（公司自己报给监管机构的原始数据，比 Nasdaq
        # 转手的接口更权威、更新更及时）。SEC 找不到这家公司（比如非美股上市主体、
        # ADR 等不直接向 SEC 报送 US-GAAP 数据的情况）或者数据取不完整时，退回用
        # Nasdaq 的数字，不让用户看到空表。
        cik = None
        source = "Nasdaq"
        annual = quarter = None
        try:
            cik = sec_edgar.cik_for(code)
            sec_financials = sec_edgar.build_financials(cik)
            if sec_financials["annual"]["income"]["rows"] or sec_financials["quarter"]["income"]["rows"]:
                annual, quarter = sec_financials["annual"], sec_financials["quarter"]
                source = "SEC EDGAR"
        except Exception:
            pass

        if annual is None or quarter is None:
            try:
                annual, quarter = _nasdaq_financials(client, code)
            except httpx.HTTPError as exc:
                if source != "SEC EDGAR":
                    raise FundamentalsError(f"拉不到 {code} 的财报") from exc
                annual = annual or {"income": {"periods": [], "rows": [], "by_name": {}},
                                     "balance": {"periods": [], "rows": [], "by_name": {}},
                                     "cash": {"periods": [], "rows": [], "by_name": {}},
                                     "ratios": {"periods": [], "rows": [], "by_name": {}}}
                quarter = quarter or annual

    if not info and not annual["income"]["rows"] and not quarter["income"]["rows"]:
        raise FundamentalsError(f"找不到 {code} 的财报，换个美股代码试试")

    risk_factors = None
    if cik:
        try:
            risk_factors = sec_edgar.get_risk_factors(cik)
        except Exception:
            risk_factors = None

    summary_data = (summary or {}).get("summaryData") or {}
    primary = (info or {}).get("primaryData") or {}
    secondary = (info or {}).get("secondaryData") or {}
    price = _num((primary.get("lastSalePrice") if (info or {}).get("marketStatus") == "Pre-Market" else None) or secondary.get("lastSalePrice") or primary.get("lastSalePrice"))
    target = _num(_label(summary_data, "OneYrTarget"))
    surprise = _surprise_rows(surprise_raw)
    company = {
        "symbol": code,
        "name": _label(profile, "CompanyName") or (info or {}).get("companyName") or code,
        "exchange": _label(summary_data, "Exchange") or (info or {}).get("exchange") or "",
        "sector": _label(profile, "Sector") or _label(summary_data, "Sector"),
        "industry": _label(profile, "Industry") or _label(summary_data, "Industry"),
        "region": _label(profile, "Region"),
        "about": _label(profile, "CompanyDescription"),
        "price": price,
        "change_pct": _num((secondary.get("percentageChange") if secondary else None) or primary.get("percentageChange")),
        "market_cap": _num(_label(summary_data, "MarketCap")),
        "avg_volume": _num(_label(summary_data, "AverageVolume")),
        "high_52w": None,
        "low_52w": None,
        "target": target,
        "yield": _label(summary_data, "Yield"),
        "range_52w": _label(summary_data, "FiftTwoWeekHighLow"),
        "valuation": _yahoo_ttm(code),
    }
    hi_lo = company["range_52w"].replace("$", "").split("/") if company["range_52w"] else []
    if len(hi_lo) == 2:
        company["high_52w"] = _num(hi_lo[0])
        company["low_52w"] = _num(hi_lo[1])

    income_names = ["Total Revenue", "Gross Profit", "Operating Income", "Net Income"]
    balance_names = [
        "Cash and Cash Equivalents",
        "Total Current Assets",
        "Total Assets",
        "Total Current Liabilities",
        "Long-Term Debt",
        "Total Liabilities",
        "Total Equity",
    ]
    cash_names = ["Net Cash Flow-Operating", "Capital Expenditures", "Net Cash Flow"]
    ratio_names = ["Current Ratio", "Gross Margin", "Operating Margin", "Profit Margin"]

    return {
        "company": company,
        "notes": _notes(company, annual, quarter, surprise, target),
        "quarter": {
            "periods": quarter["income"]["periods"],
            "income": _pick_rows(quarter["income"], income_names),
            "balance": _pick_rows(quarter["balance"], balance_names),
            "cash": _cash_rows(quarter["cash"], cash_names),
            "ratios": _pick_rows(quarter["ratios"], ratio_names),
        },
        "annual": {
            "periods": annual["income"]["periods"],
            "income": _pick_rows(annual["income"], income_names),
            "balance": _pick_rows(annual["balance"], balance_names),
            "cash": _cash_rows(annual["cash"], cash_names),
        },
        "surprise": surprise,
        "risk_factors": risk_factors,
        "source": source,
    }


def analyze(symbol: str) -> dict:
    return _cached(symbol)
