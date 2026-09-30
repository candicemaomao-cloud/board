from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import pstdev
from time import time

import httpx

from app.services.earnings_stocks import _fetch_calendar, _map_sector
from app.services.fundamentals import NASDAQ_HEADERS
from app.user_stocks.stock_screener import SP500_TICKERS

BASE_TTL = 6 * 60 * 60
SCAN_TTL = 20 * 60
EARNINGS_FIELDS = ("earnings_date", "earnings_time", "days_to_earnings")
MAX_SCAN = 1000


class ScreenTooLarge(ValueError):
    pass

_all_cache: tuple[float, list[dict]] | None = None
_base_cache: tuple[float, list[dict]] | None = None
_metric_cache: dict[str, tuple[float, dict]] = {}

NUMERIC_FIELDS = {
    "market_cap", "price", "avg_dollar_volume_20d", "revenue_growth_yoy",
    "net_margin", "pe", "weekly_return", "monthly_return", "regression_deviation",
}

ADVANCED_NUMERIC_FIELDS = {
    "revenue_growth_streak", "revenue_acceleration_streak", "eps_growth_yoy",
    "gross_margin_change_yoy", "operating_margin_change_yoy", "fcf_positive_years",
    "fcf_margin", "debt_to_assets", "net_debt_ebitda", "interest_coverage",
    "sector_pe_percentile", "relative_spy_1m", "relative_spy_3m", "relative_spy_6m",
    "days_above_ma50", "days_above_ma200", "days_since_ma50_breakout",
    "days_since_ma200_breakout", "distance_52w_high", "distance_52w_low",
    "volume_ratio_20d", "volatility_60d", "max_drawdown_1y", "days_to_earnings",
}


def _number(value):
    if isinstance(value, dict):
        value = value.get("raw")
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "").replace("%", "")
    mult = 1
    if text[-1:].upper() in {"K", "M", "B", "T"}:
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1].upper()]
        text = text[:-1]
    try:
        result = float(text) * mult
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _nasdaq_all() -> list[dict]:
    """Nasdaq 全美股列表（含基础字段），失败时返回空列表且不缓存。"""
    global _all_cache
    now = time()
    if _all_cache and now - _all_cache[0] < BASE_TTL:
        return _all_cache[1]
    rows = []
    try:
        with httpx.Client(timeout=25, headers=NASDAQ_HEADERS, follow_redirects=True) as client:
            response = client.get(
                "https://api.nasdaq.com/api/screener/stocks",
                params={"tableonly": "true", "limit": 10000, "download": "true"},
            )
            response.raise_for_status()
            raw = (((response.json().get("data") or {}).get("rows")) or [])
        for item in raw:
            symbol = str(item.get("symbol") or "").strip().upper().replace(".", "-")
            if not symbol:
                continue
            sector = str(item.get("sector") or "").strip()
            rows.append({
                "symbol": symbol,
                "name": str(item.get("name") or symbol).strip(),
                "exchange": str(item.get("exchange") or "").strip(),
                "sector": sector,
                "sector_zh": _map_sector(sector),
                "industry": str(item.get("industry") or "").strip(),
                "ipo_year": int(item["ipoyear"]) if str(item.get("ipoyear") or "").isdigit() else None,
                "market_cap": _number(item.get("marketCap")),
                "price": _number(item.get("lastsale")),
                "volume": _number(item.get("volume")),
            })
    except Exception:
        rows = []
    if rows:
        _all_cache = (now, rows)
    return rows


def _earnings_universe(days: int, pool: str) -> list[dict]:
    """股票池内未来 N 天有财报的股票，基础字段来自 Nasdaq 股票列表，缺失时用财报日历自带的名称/市值。"""
    by_symbol = {row["symbol"]: row for row in _nasdaq_all()}
    allowed = set(SP500_TICKERS) if pool == "sp500" else None
    rows = []
    for item in _fetch_calendar(days).get("items") or []:
        symbol = item["symbol"].replace(".", "-")
        if allowed is not None and symbol not in allowed:
            continue
        base = by_symbol.get(symbol) or {
            "symbol": symbol, "name": item.get("name") or symbol, "exchange": None,
            "sector": None, "sector_zh": "其他", "industry": None, "ipo_year": None,
            "market_cap": item.get("market_cap"), "price": None, "volume": None,
        }
        row = dict(base)
        if row.get("market_cap") is None:
            row["market_cap"] = item.get("market_cap")
        row["earnings_date"] = item.get("earnings_date")
        row["earnings_time"] = item.get("time")
        row["days_to_earnings"] = item.get("days_until")
        rows.append(row)
    rows.sort(key=lambda row: (-(row.get("market_cap") or 0), row["symbol"]))
    return rows


def _base_universe() -> list[dict]:
    global _base_cache
    now = time()
    if _base_cache and now - _base_cache[0] < BASE_TTL:
        return _base_cache[1]
    allowed = set(SP500_TICKERS)
    rows = [dict(row) for row in _nasdaq_all() if row["symbol"] in allowed]
    if not rows:
        rows = [{"symbol": s, "name": s, "exchange": None, "sector": None,
                 "sector_zh": "其他", "industry": None, "ipo_year": None,
                 "market_cap": None, "price": None, "volume": None} for s in SP500_TICKERS]
    rows.sort(key=lambda row: (-(row.get("market_cap") or 0), row["symbol"]))
    _base_cache = (now, rows)
    return rows


def _all_universe() -> list[dict]:
    rows = [dict(row) for row in _nasdaq_all()]
    if not rows:
        raise RuntimeError("Nasdaq 全美股列表获取失败")
    rows.sort(key=lambda row: (-(row.get("market_cap") or 0), row["symbol"]))
    return rows


def options() -> dict:
    rows = _base_universe()
    return {
        "universe": "S&P 500",
        "count": len(rows),
        "sectors": sorted({r["sector_zh"] for r in rows if r.get("sector_zh")}),
        "exchanges": sorted({r["exchange"] for r in rows if r.get("exchange")}),
        "asof": datetime.now(timezone.utc).isoformat(),
    }


def _module_map(ticker, attr) -> dict:
    try:
        value = getattr(ticker, attr)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _history_map(symbols: list[str]) -> dict[str, list[dict]]:
    if not symbols:
        return {}
    try:
        import yfinance as yf
        frame = yf.download(
            tickers=" ".join(s.replace("-", ".") for s in symbols), period="1y", interval="1d",
            auto_adjust=False, progress=False, group_by="ticker", threads=True, timeout=25,
        )
    except Exception:
        return {}
    out = {}
    many = len(symbols) > 1
    for symbol in symbols:
        yf_symbol = symbol.replace("-", ".")
        try:
            part = frame[yf_symbol] if many else frame
            records = []
            for idx, row in part.iterrows():
                close = _number(row.get("Close"))
                if close is None:
                    continue
                records.append({"date": idx.date(), "close": close, "volume": _number(row.get("Volume"))})
            if records:
                out[symbol] = records
        except Exception:
            continue
    return out


def _trend_metrics(rows: list[dict], benchmark_rows: list[dict] | None = None) -> dict:
    closes = [r["close"] for r in rows]
    latest = closes[-1]
    def change(days):
        return (latest / closes[-days - 1] - 1) * 100 if len(closes) > days else None
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
    avg_dollar = None
    liquid = [r["close"] * r["volume"] for r in rows[-20:] if r.get("volume") is not None]
    if len(liquid) >= 15:
        avg_dollar = sum(liquid) / len(liquid)
    reg_dev = None
    if len(closes) >= 60:
        from app.services.screener import _theil_sen
        sample = closes[-252:]
        slope, intercept = _theil_sen(list(range(len(sample))), sample)
        predicted = intercept + slope * (len(sample) - 1)
        reg_dev = (latest / predicted - 1) * 100 if predicted else None
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    volatility = pstdev(returns[-60:]) * math.sqrt(252) * 100 if len(returns) >= 60 else None
    sample60 = closes[-60:]
    peak = sample60[0] if sample60 else None
    max_drawdown = 0.0 if sample60 else None
    for value in sample60:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, (value / peak - 1) * 100)
    high52, low52 = max(closes), min(closes)
    peak1y = closes[0]
    max_drawdown_1y = 0.0
    for value in closes:
        peak1y = max(peak1y, value)
        max_drawdown_1y = min(max_drawdown_1y, (value / peak1y - 1) * 100)
    def days_above(window):
        if len(closes) < window:
            return None
        count = 0
        for i in range(len(closes) - 1, window - 2, -1):
            avg = sum(closes[i - window + 1:i + 1]) / window
            if closes[i] <= avg:
                break
            count += 1
        return count
    def breakout_age(window):
        if len(closes) <= window:
            return None
        for age in range(min(20, len(closes) - window)):
            i = len(closes) - 1 - age
            now_ma = sum(closes[i - window + 1:i + 1]) / window
            prev_ma = sum(closes[i - window:i]) / window
            if closes[i] > now_ma and closes[i - 1] <= prev_ma:
                return age
        return None
    volumes = [r.get("volume") for r in rows]
    prior_volumes = [v for v in volumes[-21:-1] if v is not None]
    volume_ratio = (volumes[-1] / (sum(prior_volumes) / len(prior_volumes))
                    if volumes[-1] is not None and len(prior_volumes) >= 15 and sum(prior_volumes) else None)
    relative = {}
    benchmark_closes = [r["close"] for r in (benchmark_rows or [])]
    for days, key in ((21, "relative_spy_1m"), (63, "relative_spy_3m"), (126, "relative_spy_6m")):
        stock_change = change(days)
        spy_change = ((benchmark_closes[-1] / benchmark_closes[-days - 1] - 1) * 100
                      if len(benchmark_closes) > days else None)
        relative[key] = stock_change - spy_change if stock_change is not None and spy_change is not None else None
    return {
        "price": latest, "avg_dollar_volume_20d": avg_dollar,
        "weekly_return": change(5), "monthly_return": change(21),
        "ma50": ma50, "ma200": ma200, "above_ma50": latest > ma50 if ma50 is not None else None,
        "above_ma200": latest > ma200 if ma200 is not None else None,
        "regression_deviation": reg_dev, "volatility_60d": volatility,
        "max_drawdown_60d": max_drawdown, "max_drawdown_1y": max_drawdown_1y,
        "distance_52w_high": (latest / high52 - 1) * 100 if high52 else None,
        "distance_52w_low": (latest / low52 - 1) * 100 if low52 else None,
        "days_above_ma50": days_above(50), "days_above_ma200": days_above(200),
        "days_since_ma50_breakout": breakout_age(50), "days_since_ma200_breakout": breakout_age(200),
        "volume_ratio_20d": volume_ratio, **relative,
    }


def _frame_rows(frame, symbol: str) -> list[dict]:
    try:
        part = frame.loc[symbol]
        if getattr(part, "ndim", 0) == 1:
            part = part.to_frame().T
        part = part.sort_values("asOfDate")
        return part.to_dict("records")
    except Exception:
        return []


def _finite(row: dict, key: str):
    value = _number(row.get(key))
    return value


def _advanced_financial_metrics(income_rows: list[dict], cash_quarters: list[dict], cash_years: list[dict], balance_rows: list[dict]) -> dict:
    income = [r for r in income_rows if r.get("periodType") == "3M"]
    cash_q = [r for r in cash_quarters if r.get("periodType") == "3M"]
    cash_y = [r for r in cash_years if r.get("periodType") == "12M"]
    out = {}
    if len(income) >= 5:
        latest, year_ago = income[-1], income[-5]
        def growth(key):
            now, prev = _finite(latest, key), _finite(year_ago, key)
            return (now / abs(prev) - 1) * 100 if now is not None and prev not in (None, 0) else None
        out["eps_growth_yoy"] = growth("DilutedEPS")
        for source, key in (("GrossProfit", "gross_margin_change_yoy"), ("OperatingIncome", "operating_margin_change_yoy")):
            now_rev, old_rev = _finite(latest, "TotalRevenue"), _finite(year_ago, "TotalRevenue")
            now_val, old_val = _finite(latest, source), _finite(year_ago, source)
            out[key] = ((now_val / now_rev) - (old_val / old_rev)) * 100 if None not in (now_rev, old_rev, now_val, old_val) and now_rev and old_rev else None
        now_net, old_net = _finite(latest, "NetIncome"), _finite(year_ago, "NetIncome")
        out["turned_profitable"] = now_net is not None and old_net is not None and now_net > 0 >= old_net
        yoy_growths = []
        for i in range(4, len(income)):
            now, old = _finite(income[i], "TotalRevenue"), _finite(income[i - 4], "TotalRevenue")
            yoy_growths.append((now / abs(old) - 1) * 100 if now is not None and old not in (None, 0) else None)
        streak = 0
        for value in reversed(yoy_growths):
            if value is None or value <= 0: break
            streak += 1
        acceleration = 0
        for i in range(len(yoy_growths) - 1, 0, -1):
            if yoy_growths[i] is None or yoy_growths[i - 1] is None or yoy_growths[i] <= yoy_growths[i - 1]: break
            acceleration += 1
        out["revenue_growth_streak"] = streak
        out["revenue_acceleration_streak"] = acceleration
    if cash_y:
        positive_years = 0
        for row in reversed(cash_y):
            value = _finite(row, "FreeCashFlow")
            if value is None or value <= 0: break
            positive_years += 1
        out["fcf_positive_years"] = positive_years
    recent_revenue = [_finite(r, "TotalRevenue") for r in income[-4:]]
    recent_fcf = [_finite(r, "FreeCashFlow") for r in cash_q[-4:]]
    if len(recent_revenue) == 4 and len(recent_fcf) == 4 and None not in recent_revenue + recent_fcf and sum(recent_revenue):
        out["fcf_margin"] = sum(recent_fcf) / sum(recent_revenue) * 100
    if balance_rows:
        latest_balance = balance_rows[-1]
        assets, debt = _finite(latest_balance, "TotalAssets"), _finite(latest_balance, "TotalDebt")
        out["debt_to_assets"] = debt / assets * 100 if debt is not None and assets else None
    if len(income) >= 4:
        ebit = [_finite(r, "EBIT") for r in income[-4:]]
        interest = [_finite(r, "InterestExpense") or _finite(r, "InterestExpenseNonOperating") for r in income[-4:]]
        if None not in ebit + interest and sum(abs(v) for v in interest):
            out["interest_coverage"] = sum(ebit) / sum(abs(v) for v in interest)
    return out


def _enrich(base_rows: list[dict], advanced: bool = False) -> list[dict]:
    now = time()
    symbols = [r["symbol"] for r in base_rows]
    needed = [s for s in symbols if s not in _metric_cache or now - _metric_cache[s][0] >= SCAN_TTL or (advanced and not _metric_cache[s][1].get("_advanced_ready"))]
    if needed:
        try:
            from yahooquery import Ticker
            ticker = Ticker(needed, asynchronous=True, max_workers=12, timeout=18)
            financial = _module_map(ticker, "financial_data")
            key_stats = _module_map(ticker, "key_stats")
            summary_detail = _module_map(ticker, "summary_detail")
            price_data = _module_map(ticker, "price")
        except Exception:
            financial, key_stats, summary_detail, price_data = {}, {}, {}, {}
        histories = _history_map(list(dict.fromkeys(needed + ["SPY"])))
        income_frame = cash_q_frame = cash_y_frame = balance_frame = None
        calendar = {}
        if advanced:
            try:
                income_frame = ticker.income_statement(frequency="q", trailing=False)
                cash_q_frame = ticker.cash_flow(frequency="q", trailing=False)
                cash_y_frame = ticker.cash_flow(frequency="a", trailing=False)
                balance_frame = ticker.balance_sheet(frequency="q")
                calendar = _module_map(ticker, "calendar_events")
            except Exception:
                pass
        for symbol in needed:
            f = financial.get(symbol, {}) if isinstance(financial.get(symbol), dict) else {}
            k = key_stats.get(symbol, {}) if isinstance(key_stats.get(symbol), dict) else {}
            d = summary_detail.get(symbol, {}) if isinstance(summary_detail.get(symbol), dict) else {}
            p = price_data.get(symbol, {}) if isinstance(price_data.get(symbol), dict) else {}
            pe = _number(d.get("trailingPE") or k.get("trailingPE") or f.get("trailingPE"))
            if pe is not None and pe <= 0:
                pe = None
            eps = _number(k.get("trailingEps"))
            margin = _number(f.get("profitMargins"))
            metrics = {
                "market_cap": _number(p.get("marketCap")),
                "revenue_growth_yoy": _number(f.get("revenueGrowth")),
                "net_margin": margin,
                "free_cash_flow": _number(f.get("freeCashflow")),
                "fcf_margin": (_number(f.get("freeCashflow")) / _number(f.get("totalRevenue")) * 100
                               if _number(f.get("freeCashflow")) is not None and _number(f.get("totalRevenue")) else None),
                "pe": pe,
                "is_profitable": (eps > 0) if eps is not None else (margin > 0 if margin is not None else None),
            }
            if metrics["revenue_growth_yoy"] is not None:
                metrics["revenue_growth_yoy"] *= 100
            if metrics["net_margin"] is not None:
                metrics["net_margin"] *= 100
            metrics.update(_trend_metrics(histories[symbol], histories.get("SPY")) if histories.get(symbol) else {})
            if advanced:
                metrics.update(_advanced_financial_metrics(
                    _frame_rows(income_frame, symbol), _frame_rows(cash_q_frame, symbol),
                    _frame_rows(cash_y_frame, symbol), _frame_rows(balance_frame, symbol),
                ))
                total_debt, total_cash, ebitda = _number(f.get("totalDebt")), _number(f.get("totalCash")), _number(f.get("ebitda"))
                metrics["net_debt_ebitda"] = ((total_debt - (total_cash or 0)) / ebitda
                                               if total_debt is not None and ebitda else None)
                event = calendar.get(symbol, {}) if isinstance(calendar.get(symbol), dict) else {}
                dates = ((event.get("earnings") or {}).get("earningsDate") or [])
                try:
                    target = datetime.fromisoformat(str(dates[0])[:10]).date() if dates else None
                    metrics["days_to_earnings"] = (target - datetime.now(timezone.utc).date()).days if target else None
                except (TypeError, ValueError):
                    metrics["days_to_earnings"] = None
                metrics["_advanced_ready"] = True
            _metric_cache[symbol] = (now, metrics)
    out = []
    for base in base_rows:
        row = dict(base)
        row.update((_metric_cache.get(base["symbol"]) or (0, {}))[1])
        row["free_cash_flow_positive"] = row.get("free_cash_flow") > 0 if row.get("free_cash_flow") is not None else None
        row["missing_fields"] = [key for key in NUMERIC_FIELDS | (ADVANCED_NUMERIC_FIELDS if advanced else set()) if row.get(key) is None]
        out.append(row)
    return out


def _in_range(value, spec) -> bool:
    if value is None:
        return False
    low, high = spec.get("min"), spec.get("max")
    return (low is None or value >= low) and (high is None or value <= high)


def _conditions(row, filters: dict) -> list[bool]:
    checks = []
    ranges = filters.get("ranges") or {}
    for key, spec in ranges.items():
        if key == "days_to_earnings" and filters.get("earnings_mode") == "exclude":
            continue
        if isinstance(spec, dict) and (spec.get("min") is not None or spec.get("max") is not None):
            checks.append(_in_range(row.get(key), spec))
    sectors = filters.get("sectors") or []
    if sectors:
        checks.append(row.get("sector_zh") in sectors)
    ma50 = filters.get("above_ma50")
    if ma50 in (True, False):
        checks.append(row.get("above_ma50") is ma50)
    fcf = filters.get("free_cash_flow_positive")
    if fcf is True:
        checks.append(row.get("free_cash_flow_positive") is True)
    if filters.get("profitable_only"):
        checks.append(row.get("is_profitable") is True)
    if filters.get("turned_profitable"):
        checks.append(row.get("turned_profitable") is True)
    if filters.get("earnings_mode") == "exclude":
        spec = ranges.get("days_to_earnings") or {}
        value = row.get("days_to_earnings")
        checks.append(value is not None and not _in_range(value, spec))
    return checks


def run(config: dict) -> dict:
    filters = config.get("filters") or {}
    logic = config.get("logic") if config.get("logic") in {"all", "any"} else "all"
    pool = "all" if config.get("universe") == "all" else "sp500"
    try:
        earnings_days = int(filters.get("earnings_within_days") or 0)
    except (TypeError, ValueError):
        earnings_days = 0
    earnings_days = min(earnings_days, 45) if earnings_days > 0 else 0
    if earnings_days:
        base = _earnings_universe(earnings_days, pool)
    else:
        base = _all_universe() if pool == "all" else _base_universe()
    universe_label = "全美股" if pool == "all" else "S&P 500"
    if earnings_days:
        universe_label += f" · 未来 {earnings_days} 天发布财报"
    # AND can safely narrow by base fields; OR must keep the full set for later metrics.
    base_ranges = (filters.get("ranges") or {})
    prefiltered = []
    for row in base:
        if logic == "any":
            prefiltered.append(row)
            continue
        checks = []
        for key in ("market_cap", "price"):
            spec = base_ranges.get(key) or {}
            if spec.get("min") is not None or spec.get("max") is not None:
                checks.append(_in_range(row.get(key), spec))
        if filters.get("sectors"):
            checks.append(row.get("sector_zh") in filters["sectors"])
        if not checks or (all(checks) if logic == "all" else any(checks)):
            prefiltered.append(row)
    if len(prefiltered) > MAX_SCAN:
        raise ScreenTooLarge(
            f"{universe_label} 中符合基础条件的有 {len(prefiltered)} 只，超过单次精算上限 {MAX_SCAN} 只，"
            "请加上市值/股价/行业等条件缩小范围（“任一满足”模式不会预先缩小范围）"
        )
    candidates = prefiltered
    advanced = bool(filters.get("advanced_enabled"))
    enriched = _enrich(candidates, advanced=advanced)
    if earnings_days:
        by_symbol = {row["symbol"]: row for row in candidates}
        for row in enriched:
            row.update({key: by_symbol[row["symbol"]].get(key) for key in EARNINGS_FIELDS})
    if advanced:
        by_sector = {}
        for row in enriched:
            if row.get("pe") is not None:
                by_sector.setdefault(row.get("sector_zh"), []).append(row["pe"])
        for row in enriched:
            peers = sorted(by_sector.get(row.get("sector_zh"), []))
            row["sector_pe_percentile"] = (sum(1 for value in peers if value <= row["pe"]) / len(peers) * 100
                                            if row.get("pe") is not None and len(peers) >= 3 else None)
    results = []
    for row in enriched:
        checks = _conditions(row, filters)
        if not checks or (all(checks) if logic == "all" else any(checks)):
            results.append(row)
    sort_key = config.get("sort") or "market_cap"
    reverse = config.get("sort_dir", "desc") != "asc"
    results.sort(key=lambda row: (row.get(sort_key) is not None, row.get(sort_key) or 0), reverse=reverse)
    return {
        "items": results, "count": len(results), "universe_count": len(base),
        "universe": universe_label, "pool": pool, "earnings_within_days": earnings_days or None,
        "prefilter_count": len(prefiltered), "scanned_count": len(candidates),
        "advanced_pass_count": len(results),
        "missing_count": sum(1 for row in enriched if row.get("missing_fields")),
        "truncated": len(prefiltered) > len(candidates), "logic": logic,
        "asof": datetime.now(timezone.utc).isoformat(),
        "price_period": "最近一年日线；周=5个交易日，月=21个交易日，回归=最多252根日线",
        "financial_period": "Yahoo 当前可得 TTM/最近期口径",
        "advanced_period": ("季度连续性=最近可得单季同比；年度FCF=最近可得年度；相对强弱=SPY；"
                            "波动率=60日年化；最大回撤/高低点=近一年" if advanced else None),
        "source": ("Nasdaq 财报日历 + Nasdaq 股票池 + Yahoo 行情/财务" if earnings_days
                   else "Nasdaq 股票池 + Yahoo 行情/财务"),
    }
