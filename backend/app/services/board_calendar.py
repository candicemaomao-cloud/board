"""看板日程日历：宏观数据、FOMC、三巫日等，按日聚合，方便后续继续加事件源。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.econ_calendar import AGENCY, RELEASES, WEEKDAY, _month_label

ET = ZoneInfo("America/New_York")
CN = ZoneInfo("Asia/Shanghai")

# 事件 kind → 前端着色 / 图例
KIND_META = [
    {"key": "macro", "label": "宏观数据", "tone": "macro"},
    {"key": "fed", "label": "美联储", "tone": "fed"},
    {"key": "treasury", "label": "美债标售", "tone": "treasury"},
    {"key": "witching", "label": "三巫日", "tone": "witching"},
    {"key": "earnings", "label": "列表财报", "tone": "earnings"},
]

# FOMC 纪要：议息约三周后周三 14:00 ET（Fed 官网）
FOMC_MINUTES = [
    ("2026-02-18", "2026-01-27/28"),
    ("2026-04-08", "2026-03-17/18"),
    ("2026-05-20", "2026-04-28/29"),
    ("2026-07-08", "2026-06-16/17"),
    ("2026-08-19", "2026-07-28/29"),
    ("2026-10-07", "2026-09-15/16"),
    ("2026-11-18", "2026-10-27/28"),
    ("2026-12-30", "2026-12-08/09"),
]

# 美债标售周：财政部暂定日程 · 拍卖日（非公告日）
# 每月中旬前后 3Y / 10Y / 30Y 通常连着拍，分开标注、不当成单日
TREASURY_COUPON_AUCTIONS = [
    # tenor, date, note
    ("3Y", "2026-01-13", "新发/重开"),
    ("10Y", "2026-01-14", "重开"),
    ("30Y", "2026-01-15", "重开"),
    ("3Y", "2026-02-10", "新发"),
    ("10Y", "2026-02-11", "新发"),
    ("30Y", "2026-02-12", "新发"),
    ("3Y", "2026-03-10", "重开"),
    ("10Y", "2026-03-11", "重开"),
    ("30Y", "2026-03-12", "重开"),
    ("3Y", "2026-04-07", "重开"),
    ("10Y", "2026-04-08", "重开"),
    ("30Y", "2026-04-09", "重开"),
    ("3Y", "2026-05-11", "新发"),
    ("10Y", "2026-05-12", "新发"),
    ("30Y", "2026-05-13", "新发"),
    ("3Y", "2026-06-09", "重开"),
    ("10Y", "2026-06-10", "重开"),
    ("30Y", "2026-06-11", "重开"),
    ("3Y", "2026-07-07", "重开"),
    ("10Y", "2026-07-08", "重开"),
    ("30Y", "2026-07-09", "重开"),
    ("3Y", "2026-08-11", "新发"),
    ("10Y", "2026-08-12", "新发"),
    ("30Y", "2026-08-13", "新发"),
    ("3Y", "2026-09-08", "重开"),
    ("10Y", "2026-09-09", "重开"),
    ("30Y", "2026-09-10", "重开"),
    ("3Y", "2026-10-06", "重开"),
    ("10Y", "2026-10-07", "重开"),
    ("30Y", "2026-10-08", "重开"),
    ("3Y", "2026-11-10", "新发"),
    ("10Y", "2026-11-12", "新发"),
    ("30Y", "2026-11-13", "新发"),
    ("3Y", "2026-12-08", "重开"),
    ("10Y", "2026-12-09", "重开"),
    ("30Y", "2026-12-10", "重开"),
]

# 初请失业金：惯例周四 8:30 ET；遇联邦假日顺延（精简处理）
_CLAIMS_HOLIDAY_SHIFT = {
    "2026-01-01": "2026-01-02",  # 元旦周四 → 周五
    "2026-07-02": "2026-07-02",  # 独立日前一周仍周四（7/3 放假）
    "2026-11-26": "2026-11-25",  # 感恩节当周常提前至周三
    "2026-12-24": "2026-12-24",  # 圣诞前一周仍周四
}


def third_friday(year: int, month: int) -> date:
    """当月第三个周五（股指期权/个股期权/股指期货三巫交割日）。"""
    first = date(year, month, 1)
    delta = (4 - first.weekday()) % 7  # Friday = 4
    return first + timedelta(days=delta + 14)


def triple_witching_dates(year_from: int, year_to: int) -> list[date]:
    out: list[date] = []
    for y in range(year_from, year_to + 1):
        for m in (3, 6, 9, 12):
            out.append(third_friday(y, m))
    return out


def _status(day: date, today: date) -> str:
    delta = (day - today).days
    if delta < 0:
        return "已过"
    if delta == 0:
        return "今日"
    return "即将"


def _clock_pair(day: date, clock: str) -> tuple[str, str]:
    hour, minute = (int(x) for x in clock.split(":"))
    when = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)
    return clock, when.astimezone(CN).strftime("%H:%M")


def _base(day: date, today: date, *, kind: str, key: str, name: str, **extra) -> dict:
    return {
        "date": day.isoformat(),
        "label": f"{day.month}/{day.day}",
        "weekday": f"周{WEEKDAY[day.weekday()]}",
        "kind": kind,
        "key": key,
        "name": name,
        "status": _status(day, today),
        "days": (day - today).days,
        **extra,
    }


def _macro_events(today: date) -> list[dict]:
    items: list[dict] = []
    for key, name, day_s, clock, ref in RELEASES:
        day = date.fromisoformat(day_s)
        time_et, time_cn = _clock_pair(day, clock)
        detail = f"{time_et} ET（北京 {time_cn}）· {_month_label(ref)}"
        if key == "pce":
            detail += " · BEA 同日含 headline+核心，日历按 Fed 盯的核心标"
        elif key == "adp":
            detail += " · 小非农，与非农配对"
        items.append(
            _base(
                day,
                today,
                kind="macro",
                key=key,
                name=name,
                time_et=time_et,
                time_cn=time_cn,
                ref=_month_label(ref),
                agency=AGENCY.get(key, ""),
                open=key if key in {"nfp", "cpi", "ppi", "pce", "pmi"} else None,
                detail=detail,
            )
        )
    return items


def _fomc_events(today: date) -> list[dict]:
    # 延迟导入，避免与 market.build_market 循环依赖
    from app.services.market import FOMC_MEETINGS

    items: list[dict] = []
    for start_s, end_s, sep in FOMC_MEETINGS:
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)
        name = "FOMC 议息"
        if sep:
            name += "（点阵图）"
        cur = start
        while cur <= end:
            detail = f"{start_s}–{end_s}"
            if sep:
                detail += " · 含经济预测 / 新闻发布会"
            if start != end:
                detail += f" · 第 {(cur - start).days + 1} 天"
            items.append(
                _base(
                    cur,
                    today,
                    kind="fed",
                    key="fomc",
                    name=name,
                    open="fedRisk",
                    detail=detail,
                    sep=sep,
                    meeting_start=start_s,
                    meeting_end=end_s,
                )
            )
            cur += timedelta(days=1)
    return items


def _witching_events(today: date) -> list[dict]:
    """当年 + 明年的四次三巫日。"""
    items: list[dict] = []
    for day in triple_witching_dates(today.year, today.year + 1):
        items.append(
            _base(
                day,
                today,
                kind="witching",
                key="triple_witching",
                name="三巫日",
                detail="股指期权、个股期权与股指期货同日到期（季度第三个周五）",
                open=None,
            )
        )
    return items


def _claims_events(today: date) -> list[dict]:
    """初请失业金：当年 + 明年每周四（假日微调）。"""
    items: list[dict] = []
    start = date(today.year, 1, 1)
    end = date(today.year + 1, 12, 31)
    # 找到第一个周四
    cur = start
    while cur.weekday() != 3:
        cur += timedelta(days=1)
    while cur <= end:
        day_s = cur.isoformat()
        release = date.fromisoformat(_CLAIMS_HOLIDAY_SHIFT.get(day_s, day_s))
        time_et, time_cn = _clock_pair(release, "08:30")
        items.append(
            _base(
                release,
                today,
                kind="macro",
                key="claims",
                name="初请失业金",
                time_et=time_et,
                time_cn=time_cn,
                agency="DOL",
                open=None,
                detail=f"{time_et} ET（北京 {time_cn}）· 周度 · 惯例周四",
            )
        )
        cur += timedelta(days=7)
    return items


def _fomc_minutes_events(today: date) -> list[dict]:
    items: list[dict] = []
    for day_s, meeting in FOMC_MINUTES:
        day = date.fromisoformat(day_s)
        time_et, time_cn = _clock_pair(day, "14:00")
        items.append(
            _base(
                day,
                today,
                kind="fed",
                key="fomc_minutes",
                name="FOMC 纪要",
                time_et=time_et,
                time_cn=time_cn,
                open="fedRisk",
                detail=f"{time_et} ET（北京 {time_cn}）· 对应议息 {meeting} · 约三周后发布",
                meeting_ref=meeting,
            )
        )
    return items


def _treasury_auction_events(today: date) -> list[dict]:
    """美债标售：3Y/10Y/30Y 分日标注（同周供应压力，不当成单日复合）。"""
    items: list[dict] = []
    by_week: dict[tuple[int, int], list[str]] = defaultdict(list)
    parsed: list[tuple[str, date, str]] = []
    for tenor, day_s, note in TREASURY_COUPON_AUCTIONS:
        day = date.fromisoformat(day_s)
        parsed.append((tenor, day, note))
        iso = day.isocalendar()
        by_week[(iso[0], iso[1])].append(tenor)

    for tenor, day, note in parsed:
        iso = day.isocalendar()
        week_tenors = by_week[(iso[0], iso[1])]
        week_label = "/".join(week_tenors)
        items.append(
            _base(
                day,
                today,
                kind="treasury",
                key=f"ust_{tenor.lower()}",
                name=f"美债·{tenor}",
                open=None,
                detail=f"标售周 {week_label} · {note} · 财政部暂定拍卖日（非公告日）",
                tenor=tenor,
                week_label=week_label,
                auction_note=note,
            )
        )
    return items


def _collect_extra_events(today: date) -> list[dict]:
    """后续其它「每天会发生什么」在这里追加，保持统一字段。"""
    return (
        _claims_events(today)
        + _fomc_minutes_events(today)
        + _treasury_auction_events(today)
    )


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def _watchlist_equity_symbols(db) -> list[str]:
    from app.services.daily_watch import list_watches
    from app.services.macro_event_study import _ETF_LIKE

    out: list[str] = []
    seen: set[str] = set()
    for row in list_watches(db):
        sym = (row.symbol or "").strip().upper()
        if not sym or sym in seen:
            continue
        # 板块 ETF / 宽基：无个股财报
        if (
            sym in _ETF_LIKE
            or sym.startswith("XL")
            or sym in {"SOXX", "SMH", "KRE", "XBI", "XRT", "SPY", "QQQ", "IWM", "DIA"}
        ):
            continue
        seen.add(sym)
        out.append(sym)
    return out


_earn_month_cache: dict[str, tuple[float, dict]] = {}
_EARN_MONTH_TTL = 30 * 60


def watchlist_earnings_month(db, *, year: int, month: int, today: date | None = None) -> dict:
    """只算指定月份：Nasdaq 日历按日拉取，再筛股票列表里的个股（ETF 跳过）。

    看板默认只请求当月；切月时前端再请求对应 ym，减轻压力。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from time import time

    from app.services.earnings_stocks import _calendar_day, _trading_days

    today = today or date.today()
    if year < 2000 or year > 2100 or month < 1 or month > 12:
        return {"ym": "", "by_date": {}, "items": [], "symbols": [], "note": "月份无效"}

    ym = f"{year:04d}-{month:02d}"
    hit = _earn_month_cache.get(ym)
    now = time()
    if hit and now - hit[0] < _EARN_MONTH_TTL:
        return hit[1]

    symbols = _watchlist_equity_symbols(db)
    wanted = set(symbols)
    start, end = _month_bounds(year, month)
    day_list = _trading_days(start, end)

    by_date: dict[str, list[dict]] = defaultdict(list)
    flat: list[dict] = []

    if wanted and day_list:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(_calendar_day, d): d for d in day_list}
            for fut in as_completed(futs):
                day = futs[fut]
                try:
                    rows = fut.result()
                except Exception:
                    continue
                for row in rows:
                    sym = (row.get("symbol") or "").upper()
                    if sym not in wanted:
                        continue
                    timing = row.get("time") or "—"
                    ev = _base(
                        day,
                        today,
                        kind="earnings",
                        key=f"earn_{sym}",
                        name=f"{sym} 财报",
                        detail=f"{timing} · {row.get('name') or sym}",
                        open="dailyWatch",
                        symbol=sym,
                        time_label=timing,
                    )
                    by_date[day.isoformat()].append(ev)
                    flat.append(ev)

    for day_s in by_date:
        by_date[day_s].sort(key=lambda e: e.get("name") or "")

    flat.sort(key=lambda e: (e["date"], e.get("name") or ""))
    out = {
        "ym": ym,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "symbols": symbols,
        "count": len(flat),
        "by_date": dict(by_date),
        "items": flat,
        "kinds": [{"key": "earnings", "label": "列表财报", "tone": "earnings"}],
        "note": f"股票列表财报 · {ym}（Nasdaq 日历，仅个股）",
    }
    _earn_month_cache[ym] = (now, out)
    return out


def build_board_calendar(today: date | None = None) -> dict:
    today = today or date.today()
    events = _macro_events(today) + _fomc_events(today) + _witching_events(today) + _collect_extra_events(today)
    events.sort(key=lambda e: (e["date"], e.get("time_et") or "99:99", e["name"]))

    by_date: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_date[e["date"]].append(e)

    # 接下来快捷条：跳过周度初请（格子里仍有），避免刷屏挤掉更重要的
    upcoming = [e for e in events if e["days"] >= 0 and e.get("key") != "claims"][:8]

    next_witching = next(
        (e for e in events if e["kind"] == "witching" and e["days"] >= 0),
        None,
    )

    return {
        "today": today.isoformat(),
        "by_date": dict(by_date),
        "upcoming": upcoming,
        "kinds": KIND_META,
        "next_witching": next_witching,
        "note": "宏观（含初请/ADP/核心PCE）· FOMC议息+纪要 · 美债标售周 · 三巫日；列表财报按月懒加载",
    }
