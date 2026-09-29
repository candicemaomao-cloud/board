"""补齐最近约 2 个月交易日的股票日志（admin）。

格式：
  宏观市场
  1. FOMC / CPI / 美联储讲话 …
  【行业分析 · …】…
  （可选）【恐慌指标】…  — 默认关闭，太慢；加 --fear 才拉

已有日期跳过。用法：
  cd backend && PYTHONUNBUFFERED=1 .venv/bin/python scripts/backfill_stock_journals.py
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/backfill_stock_journals.py --fear
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import StockJournal, User
from app.services.daily_watch import list_watches
from app.services.econ_calendar import RELEASES
from app.services.market import FEEDS, FOMC_MEETINGS, _fetch_rss
from app.services.ohlc import OhlcError, fetch_closes_covering
from app.services.stock_journal import create_journal
from app.services.translate import translate_items

ET = ZoneInfo("America/New_York")
USER_NAME = "admin"
MONTHS_BACK = 2


def _bar_dates_closes(symbol: str, start: date) -> list[tuple[date, float]]:
    raw = fetch_closes_covering(symbol, "1d", start=start - timedelta(days=5))
    bars = raw.get("ohlc_bars") or []
    rows: list[tuple[date, float]] = []
    seen: set[date] = set()
    for b in bars:
        d = datetime.fromtimestamp(int(b["ts"]), tz=timezone.utc).date()
        if d in seen or d < start - timedelta(days=10):
            continue
        seen.add(d)
        rows.append((d, float(b["close"])))
    rows.sort(key=lambda r: r[0])
    return rows


def _trading_days(start: date, end: date) -> list[date]:
    rows = _bar_dates_closes("SPY", start)
    return [d for d, _ in rows if start <= d <= end]


def _econ_by_day() -> dict[date, list[str]]:
    by: dict[date, list[str]] = defaultdict(list)
    for _key, name, day_s, clock, ref in RELEASES:
        d = date.fromisoformat(day_s)
        y, m = ref.split("-")
        by[d].append(f"{name}（{int(y)}年{int(m)}月）公布 {clock} ET")
    return by


def _fomc_by_day() -> dict[date, list[str]]:
    by: dict[date, list[str]] = defaultdict(list)
    for start_s, end_s, sep in FOMC_MEETINGS:
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)
        label = "FOMC 议息会议"
        if sep:
            label += "（含点阵图 / 新闻发布会）"
        cur = start
        while cur <= end:
            by[cur].append(f"{label}（{start_s}–{end_s}）")
            cur += timedelta(days=1)
    return by


def _fed_rss_by_day() -> dict[date, list[str]]:
    by: dict[date, list[str]] = defaultdict(list)
    for url, source in (
        (FEEDS["fed_monetary"], "美联储货币政策"),
        (FEEDS["fed_speeches"], "美联储讲话"),
    ):
        try:
            items = translate_items(_fetch_rss(url, source, 100))
        except Exception as exc:
            print(f"[warn] RSS {source} 失败：{exc}", flush=True)
            continue
        for item in items:
            pub = item.get("published")
            if not pub:
                continue
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(ET)
            except ValueError:
                continue
            title = (item.get("title") or "").strip()
            if not title:
                continue
            kind = item.get("kind") or source
            by[dt.date()].append(f"{kind}：{title}")
    return by


def _macro_block(day: date, econ, fomc, fed) -> str:
    lines: list[str] = []
    lines.extend(fomc.get(day, []))
    lines.extend(econ.get(day, []))
    lines.extend(fed.get(day, []))
    seen: set[str] = set()
    uniq: list[str] = []
    for x in lines:
        if x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    if not uniq:
        uniq = ["无重大宏观日程（常规交易日）"]
    body = "\n".join(f"{i}. {t}" for i, t in enumerate(uniq, 1))
    return f"宏观市场\n{body}"


def _preload_closes(symbols: list[str], start: date) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}

    def one(sym: str):
        try:
            return sym, _bar_dates_closes(sym, start)
        except (OhlcError, Exception) as exc:
            print(f"[warn] {sym} 日线失败：{exc}", flush=True)
            return sym, []

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(one, s) for s in symbols]
        for fut in as_completed(futs):
            sym, rows = fut.result()
            out[sym] = rows
            print(f"  日线 {sym}: {len(rows)} 根", flush=True)
    return out


def _day_change(rows: list[tuple[date, float]], day: date) -> dict | None:
    """锚定 day：该日（或之前最近一根）相对前一交易日。"""
    eligible = [r for r in rows if r[0] <= day]
    if len(eligible) < 2:
        return None
    (d0, c0), (d1, c1) = eligible[-2], eligible[-1]
    if c0 == 0:
        return None
    return {
        "prev_date": d0.isoformat(),
        "latest_date": d1.isoformat(),
        "pct_change": (c1 - c0) / c0,
    }


def _sector_block(
    day: date,
    symbol_sector: dict[str, str],
    closes: dict[str, list[tuple[date, float]]],
) -> str | None:
    by_sec: dict[str, list[float]] = defaultdict(list)
    sample = None
    for sym, sector in symbol_sector.items():
        ch = _day_change(closes.get(sym) or [], day)
        if not ch or ch["pct_change"] is None:
            continue
        by_sec[sector].append(ch["pct_change"])
        sample = ch
    if not by_sec or not sample:
        return None
    rows = [
        {"sector": sec, "avg_pct_change": sum(v) / len(v), "count": len(v)}
        for sec, v in by_sec.items()
    ]
    rows.sort(key=lambda r: r["avg_pct_change"], reverse=True)
    label = f"{sample['latest_date']} vs {sample['prev_date']}"
    lines = [f"【行业分析 · {label}】{day.isoformat()}"]
    for r in rows:
        lines.append(f"{r['sector']} {r['avg_pct_change'] * 100:+.2f}%（{r['count']} 支）")
    return "\n".join(lines)


def format_fear(day: date) -> str | None:
    from app.services.stock_sentiment import fear_panel

    try:
        p = fear_panel(asof=day.isoformat())
    except Exception as exc:
        print(f"  [fear] {day} 失败：{exc}", flush=True)
        return None
    resolved = p.get("resolved_date") or day.isoformat()
    comp = p.get("composite")
    lines = [
        f"【恐慌指标】{resolved}",
        f"六项等权 {'—' if comp is None else comp}（{p.get('composite_label') or '—'}）",
    ]
    for card in p.get("cards") or []:
        delta = card.get("delta_display") or ""
        piece = f"{card.get('title')} {card.get('display')} {card.get('label') or ''} {delta}"
        lines.append(" ".join(piece.split()))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fear", action="store_true", help="额外拉恐慌指标（很慢）")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    end = date.today()
    start = end - timedelta(days=MONTHS_BACK * 31)
    print(f"区间：{start} → {end}", flush=True)

    print("拉 SPY 交易日…", flush=True)
    days = _trading_days(start, end)
    print(f"交易日 {len(days)}：{days[0] if days else '—'} … {days[-1] if days else '—'}", flush=True)

    econ = _econ_by_day()
    fomc = _fomc_by_day()
    print("拉美联储 RSS…", flush=True)
    fed = _fed_rss_by_day()
    print(f"RSS 命中 {len(fed)} 天", flush=True)

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == USER_NAME))
        if not user:
            raise SystemExit(f"找不到用户 {USER_NAME}")

        existing = {
            r.log_date
            for r in db.scalars(select(StockJournal).where(StockJournal.user_id == user.id))
        }
        print(f"已有日志 {len(existing)}，将跳过", flush=True)

        watches = list_watches(db)
        sector_watches = [
            w
            for w in watches
            if w.symbol.startswith("XL") or w.symbol in {"SOXX", "SMH", "XBI", "KRE", "XRT"}
        ]
        if not sector_watches:
            sector_watches = watches[:15]
        symbol_sector = {w.symbol: (w.sector or "其他") for w in sector_watches}
        symbols = list(symbol_sector.keys())
        print(f"行业标的：{', '.join(symbols)}", flush=True)

        print("预拉行业日线…", flush=True)
        closes = _preload_closes(symbols, start)

        created = skipped = 0
        for i, day in enumerate(days, 1):
            if day in existing:
                skipped += 1
                print(f"[{i}/{len(days)}] {day} 跳过", flush=True)
                continue
            print(f"[{i}/{len(days)}] {day} …", flush=True)
            parts = [_macro_block(day, econ, fomc, fed)]
            sector = _sector_block(day, symbol_sector, closes)
            if sector:
                parts.append(sector)
            if args.fear:
                fear = format_fear(day)
                if fear:
                    parts.append(fear)
            content = "\n\n".join(parts)
            create_journal(
                db,
                user_id=user.id,
                log_date=day,
                stance="观望",
                content=content,
            )
            created += 1
            print(f"  写入 {len(content)} 字", flush=True)
        print(f"完成：新建 {created}，跳过 {skipped}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
