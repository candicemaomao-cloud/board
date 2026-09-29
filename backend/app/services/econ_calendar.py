from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CN = ZoneInfo("Asia/Shanghai")
WEEKDAY = "一二三四五六日"

# Official 2026 calendars: BLS CPI / PPI / Employment Situation, BEA Personal Income and Outlays, ISM PMI.
# Plus ADP / 核心PCE 标注；初请失业金在 board_calendar 按周四规则生成。
# time_et is America/New_York local clock on the release date.
RELEASES = [
    # NFP · Employment Situation 8:30 ET
    ("nfp", "非农", "2026-01-09", "08:30", "2025-12"),
    ("nfp", "非农", "2026-02-11", "08:30", "2026-01"),
    ("nfp", "非农", "2026-03-06", "08:30", "2026-02"),
    ("nfp", "非农", "2026-04-03", "08:30", "2026-03"),
    ("nfp", "非农", "2026-05-08", "08:30", "2026-04"),
    ("nfp", "非农", "2026-06-05", "08:30", "2026-05"),
    ("nfp", "非农", "2026-07-02", "08:30", "2026-06"),
    ("nfp", "非农", "2026-08-07", "08:30", "2026-07"),
    ("nfp", "非农", "2026-09-04", "08:30", "2026-08"),
    ("nfp", "非农", "2026-10-02", "08:30", "2026-09"),
    ("nfp", "非农", "2026-11-06", "08:30", "2026-10"),
    ("nfp", "非农", "2026-12-04", "08:30", "2026-11"),
    # ADP「小非农」· 通常非农前一两天 8:15 ET（FRED / ADP 官方）
    ("adp", "ADP", "2026-01-07", "08:15", "2025-12"),
    ("adp", "ADP", "2026-02-04", "08:15", "2026-01"),
    ("adp", "ADP", "2026-03-04", "08:15", "2026-02"),
    ("adp", "ADP", "2026-04-01", "08:15", "2026-03"),
    ("adp", "ADP", "2026-05-06", "08:15", "2026-04"),
    ("adp", "ADP", "2026-06-03", "08:15", "2026-05"),
    ("adp", "ADP", "2026-07-01", "08:15", "2026-06"),
    ("adp", "ADP", "2026-08-05", "08:15", "2026-07"),
    ("adp", "ADP", "2026-09-02", "08:15", "2026-08"),
    ("adp", "ADP", "2026-09-30", "08:15", "2026-09"),
    ("adp", "ADP", "2026-11-04", "08:15", "2026-10"),
    ("adp", "ADP", "2026-12-02", "08:15", "2026-11"),
    # CPI 8:30 ET
    ("cpi", "CPI", "2026-01-13", "08:30", "2025-12"),
    ("cpi", "CPI", "2026-02-13", "08:30", "2026-01"),
    ("cpi", "CPI", "2026-03-11", "08:30", "2026-02"),
    ("cpi", "CPI", "2026-04-10", "08:30", "2026-03"),
    ("cpi", "CPI", "2026-05-12", "08:30", "2026-04"),
    ("cpi", "CPI", "2026-06-10", "08:30", "2026-05"),
    ("cpi", "CPI", "2026-07-14", "08:30", "2026-06"),
    ("cpi", "CPI", "2026-08-12", "08:30", "2026-07"),
    ("cpi", "CPI", "2026-09-11", "08:30", "2026-08"),
    ("cpi", "CPI", "2026-10-14", "08:30", "2026-09"),
    ("cpi", "CPI", "2026-11-10", "08:30", "2026-10"),
    ("cpi", "CPI", "2026-12-10", "08:30", "2026-11"),
    # PPI 8:30 ET
    ("ppi", "PPI", "2026-01-14", "08:30", "2025-11"),
    ("ppi", "PPI", "2026-01-30", "08:30", "2025-12"),
    ("ppi", "PPI", "2026-02-27", "08:30", "2026-01"),
    ("ppi", "PPI", "2026-03-18", "08:30", "2026-02"),
    ("ppi", "PPI", "2026-04-14", "08:30", "2026-03"),
    ("ppi", "PPI", "2026-05-13", "08:30", "2026-04"),
    ("ppi", "PPI", "2026-06-11", "08:30", "2026-05"),
    ("ppi", "PPI", "2026-07-15", "08:30", "2026-06"),
    ("ppi", "PPI", "2026-08-13", "08:30", "2026-07"),
    ("ppi", "PPI", "2026-09-10", "08:30", "2026-08"),
    ("ppi", "PPI", "2026-10-15", "08:30", "2026-09"),
    ("ppi", "PPI", "2026-11-13", "08:30", "2026-10"),
    ("ppi", "PPI", "2026-12-15", "08:30", "2026-11"),
    # 核心 PCE · BEA Personal Income and Outlays 同日发布 headline + core；Fed 盯核心
    ("pce", "核心 PCE", "2026-01-22", "10:00", "2025-10"),
    ("pce", "核心 PCE", "2026-02-20", "08:30", "2025-12"),
    ("pce", "核心 PCE", "2026-03-13", "08:30", "2026-01"),
    ("pce", "核心 PCE", "2026-04-09", "08:30", "2026-02"),
    ("pce", "核心 PCE", "2026-04-30", "08:30", "2026-03"),
    ("pce", "核心 PCE", "2026-05-28", "08:30", "2026-04"),
    ("pce", "核心 PCE", "2026-06-25", "08:30", "2026-05"),
    ("pce", "核心 PCE", "2026-07-30", "08:30", "2026-06"),
    ("pce", "核心 PCE", "2026-08-26", "08:30", "2026-07"),
    ("pce", "核心 PCE", "2026-09-30", "08:30", "2026-08"),
    ("pce", "核心 PCE", "2026-10-29", "08:30", "2026-09"),
    ("pce", "核心 PCE", "2026-11-25", "08:30", "2026-10"),
    ("pce", "核心 PCE", "2026-12-23", "08:30", "2026-11"),
    # ISM PMI 10:00 ET
    ("pmi", "PMI 制造", "2026-01-05", "10:00", "2025-12"),
    ("pmi", "PMI 服务", "2026-01-07", "10:00", "2025-12"),
    ("pmi", "PMI 制造", "2026-02-02", "10:00", "2026-01"),
    ("pmi", "PMI 服务", "2026-02-04", "10:00", "2026-01"),
    ("pmi", "PMI 制造", "2026-03-02", "10:00", "2026-02"),
    ("pmi", "PMI 服务", "2026-03-04", "10:00", "2026-02"),
    ("pmi", "PMI 制造", "2026-04-01", "10:00", "2026-03"),
    ("pmi", "PMI 服务", "2026-04-06", "10:00", "2026-03"),
    ("pmi", "PMI 制造", "2026-05-01", "10:00", "2026-04"),
    ("pmi", "PMI 服务", "2026-05-05", "10:00", "2026-04"),
    ("pmi", "PMI 制造", "2026-06-01", "10:00", "2026-05"),
    ("pmi", "PMI 服务", "2026-06-03", "10:00", "2026-05"),
    ("pmi", "PMI 制造", "2026-07-01", "10:00", "2026-06"),
    ("pmi", "PMI 服务", "2026-07-06", "10:00", "2026-06"),
    ("pmi", "PMI 制造", "2026-08-03", "10:00", "2026-07"),
    ("pmi", "PMI 服务", "2026-08-05", "10:00", "2026-07"),
    ("pmi", "PMI 制造", "2026-09-01", "10:00", "2026-08"),
    ("pmi", "PMI 服务", "2026-09-03", "10:00", "2026-08"),
    ("pmi", "PMI 制造", "2026-10-01", "10:00", "2026-09"),
    ("pmi", "PMI 服务", "2026-10-05", "10:00", "2026-09"),
    ("pmi", "PMI 制造", "2026-11-02", "10:00", "2026-10"),
    ("pmi", "PMI 服务", "2026-11-04", "10:00", "2026-10"),
    ("pmi", "PMI 制造", "2026-12-01", "10:00", "2026-11"),
    ("pmi", "PMI 服务", "2026-12-03", "10:00", "2026-11"),
]

AGENCY = {
    "cpi": "BLS",
    "ppi": "BLS",
    "nfp": "BLS",
    "adp": "ADP",
    "pce": "BEA",
    "pmi": "ISM",
    "claims": "DOL",
}


def _month_label(ym: str) -> str:
    year, month = ym.split("-")
    return f"{year}年{int(month)}月"


def _parse_clock(raw: str) -> time:
    hour, minute = raw.split(":")
    return time(int(hour), int(minute))


def _pack(key: str, name: str, day: date, clock: str, ref: str, today: date) -> dict:
    hour, minute = (int(x) for x in clock.split(":"))
    when = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)
    china = when.astimezone(CN)
    days = (day - today).days
    if days < 0:
        status = "已公布"
    elif days == 0:
        status = "今日"
    else:
        status = "即将"
    return {
        "key": key,
        "name": name,
        "date": day.isoformat(),
        "label": f"{day.month}/{day.day}",
        "weekday": f"周{WEEKDAY[day.weekday()]}",
        "time_et": clock,
        "time_cn": china.strftime("%H:%M"),
        "ref": _month_label(ref),
        "agency": AGENCY.get(key, ""),
        "status": status,
        "days": days,
    }


def build_econ_calendar(today: date | None = None) -> dict:
    today = today or date.today()
    items = [
        _pack(key, name, date.fromisoformat(day), clock, ref, today)
        for key, name, day, clock, ref in RELEASES
    ]
    items.sort(key=lambda row: (row["date"], row["time_et"], row["name"]))
    months: dict[str, list] = {}
    for row in items:
        months.setdefault(row["date"][:7], []).append(row)
    current = today.strftime("%Y-%m")
    if today.month == 12:
        nxt = f"{today.year + 1}-01"
    else:
        nxt = f"{today.year}-{today.month + 1:02d}"
    wanted = {current, nxt}
    month_rows = [
        {
            "key": ym,
            "label": _month_label(ym),
            "current": ym == current,
            "items": rows,
        }
        for ym, rows in months.items()
        if ym in wanted
    ]
    all_upcoming = [row for row in items if row["days"] >= 0]
    upcoming = [row for row in all_upcoming if row["date"][:7] in wanted]
    next_by_key: dict[str, dict] = {}
    for row in all_upcoming:
        next_by_key.setdefault(row["key"], row)
    return {
        "months": month_rows,
        "next": upcoming[:6],
        "next_by_key": next_by_key,
        "source": "BLS / BEA / ISM / ADP 2026 官方日程",
    }
