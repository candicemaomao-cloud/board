from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Side, TradeLog
from app.services.tags import get_or_create_tags

STOCK_ASSETS = {
    "stocks",
    "stock",
    "equity",
    "equities",
    "etf",
    "etfs",
    "股票",
    "美股",
    "港股",
    "证券",
}
SKIP_ASSETS = {
    "forex",
    "cash",
    "fx",
    "bond",
    "bonds",
    "option",
    "options",
    "future",
    "futures",
    "crypto",
    "cryptocurrency",
    "外汇",
    "期货",
    "期权",
    "加密",
}
SKIP_DISCRIMINATOR = {"subtotal", "total", "header", "summary"}

DATE_KEYS = {
    "date",
    "datetime",
    "tradedate",
    "settledate",
    "closedate",
    "日期",
    "成交时间",
    "成交日期",
    "平仓日期",
    "平仓时间",
    "交易日期",
}
SYMBOL_KEYS = {"symbol", "ticker", "代码", "股票代码", "证券代码", "标的", "underlying"}
SIDE_KEYS = {"side", "buysell", "buy/sell", "action", "方向", "买卖", "买卖方向", "操作"}
PNL_KEYS = {
    "pnl",
    "pnlamount",
    "realizedpl",
    "realizedpnl",
    "fifopnlrealized",
    "fifop/l",
    "已实现盈亏",
    "实现盈亏",
    "平仓盈亏",
    "盈亏",
    "盈亏金额",
}
QTY_KEYS = {"quantity", "qty", "shares", "数量", "成交数量", "股数"}
PRICE_KEYS = {"price", "tprice", "成交价", "成交价格", "价格"}
NOTES_KEYS = {"notes", "note", "description", "备注", "说明"}
ASSET_KEYS = {"assetcategory", "assetclass", "资产类别", "品种"}
DISC_KEYS = {"datadiscriminator"}
CODE_KEYS = {"code"}
TIME_KEYS = {"time", "时间"}


class ImportError_(Exception):
    pass


def _norm_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", raw.lower().strip())


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            if "�" not in text[:400]:
                return text
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _sniff(text: str) -> tuple[list[list[str]], int]:
    sample = text[:4000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
        if sample.count("\t") > sample.count(","):
            dialect.delimiter = "\t"
    rows = list(csv.reader(io.StringIO(text), dialect))
    header_idx = 0
    best = -1
    for i, row in enumerate(rows[:40]):
        keys = {_norm_key(c) for c in row if c and c.strip()}
        score = 0
        if keys & {_norm_key(k) for k in SYMBOL_KEYS}:
            score += 3
        if keys & {_norm_key(k) for k in DATE_KEYS}:
            score += 3
        if keys & {_norm_key(k) for k in PNL_KEYS}:
            score += 4
        if keys & {_norm_key(k) for k in SIDE_KEYS | QTY_KEYS}:
            score += 2
        if score > best:
            best = score
            header_idx = i
    if best < 3:
        raise ImportError_("找不到表头。请用模板列：date,symbol,side,pnl_amount，或导出含代码/日期/盈亏的成交明细。")
    return rows, header_idx


def _map_cols(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    groups = {
        "date": DATE_KEYS,
        "symbol": SYMBOL_KEYS,
        "side": SIDE_KEYS,
        "pnl": PNL_KEYS,
        "qty": QTY_KEYS,
        "price": PRICE_KEYS,
        "notes": NOTES_KEYS,
        "asset": ASSET_KEYS,
        "disc": DISC_KEYS,
        "code": CODE_KEYS,
        "time": TIME_KEYS,
    }
    for idx, col in enumerate(header):
        key = _norm_key(col)
        if not key:
            continue
        for name, aliases in groups.items():
            if name in mapping:
                continue
            if key in {_norm_key(a) for a in aliases} or any(key.endswith(_norm_key(a)) and len(key) <= len(_norm_key(a)) + 8 for a in aliases):
                mapping[name] = idx
                break
    if "symbol" not in mapping:
        raise ImportError_("表头里没有股票代码列（symbol / 代码）")
    if "date" not in mapping:
        raise ImportError_("表头里没有日期列（date / 成交时间）")
    return mapping


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _parse_number(raw: str) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text in {"-", "--", "—", "N/A", "n/a"}:
        return None
    neg = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").replace("¥", "").replace("￥", "").replace(" ", "")
    text = text.replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None
    return -abs(value) if neg else value


def _parse_date(raw: str) -> date | None:
    text = (raw or "").strip().strip('"')
    if not text:
        return None
    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    if "," in text:
        text = text.split(",", 1)[0].strip()
    text = re.split(r"[ T]", text, maxsplit=1)[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_side(raw: str, qty: float | None) -> Side:
    text = (raw or "").strip().lower()
    if text in {"long", "buy", "bot", "b", "买入", "做多", "多"}:
        return Side.LONG
    if text in {"short", "sell", "sld", "s", "卖出", "做空", "空"}:
        return Side.SHORT
    if qty is not None:
        return Side.LONG if qty >= 0 else Side.SHORT
    return Side.LONG


def _is_buy(side: Side, qty: float | None, raw_side: str) -> bool:
    text = (raw_side or "").strip().lower()
    if text in {"buy", "bot", "b", "买入", "做多"}:
        return True
    if text in {"sell", "sld", "s", "卖出", "做空"}:
        return False
    if qty is not None:
        return qty >= 0
    return side == Side.LONG


def _clean_symbol(raw: str) -> str:
    text = (raw or "").strip().upper()
    text = text.replace(" US", "").replace(".US", "")
    text = re.sub(r"\s+", "", text)
    return text[:16]


def _skip_row(asset: str, disc: str) -> bool:
    a = _norm_key(asset)
    d = _norm_key(disc)
    if d in SKIP_DISCRIMINATOR:
        return True
    if a in SKIP_ASSETS:
        return True
    if a and a not in STOCK_ASSETS and a not in {"", "usdstocks"}:
        # Keep unknown-but-stock-like; skip obvious non-equity if classified
        if any(x in a for x in ("option", "future", "forex", "crypto", "bond", "期权", "期货", "外汇")):
            return True
    return False


def _ext_id(trade_date: date, symbol: str, side: Side, pnl: float, extra: str) -> str:
    raw = f"{trade_date.isoformat()}|{symbol}|{side.value}|{pnl:.4f}|{extra}"
    return "imp:" + hashlib.sha1(raw.encode()).hexdigest()[:20]


def _fifo(fills: list[dict]) -> list[dict]:
    lots: dict[str, list[list[float]]] = {}
    realized: list[dict] = []
    for fill in fills:
        symbol = fill["symbol"]
        qty = abs(float(fill["qty"]))
        price = float(fill["price"])
        buy = fill["is_buy"]
        book = lots.setdefault(symbol, [])
        if buy:
            book.append([qty, price])
            continue
        remain = qty
        pnl = 0.0
        while remain > 1e-12 and book:
            lot_qty, lot_price = book[0]
            take = min(remain, lot_qty)
            pnl += (price - lot_price) * take
            lot_qty -= take
            remain -= take
            if lot_qty <= 1e-12:
                book.pop(0)
            else:
                book[0][0] = lot_qty
        if abs(pnl) < 1e-8:
            continue
        realized.append(
            {
                "date": fill["date"],
                "symbol": symbol,
                "side": Side.LONG if pnl >= 0 else Side.SHORT,
                "pnl": pnl,
                "extra": f"{fill['extra']}|fifo",
                "notes": fill.get("notes") or "券商成交 FIFO 估算",
            }
        )
    return realized


def parse_csv(text: str) -> tuple[list[dict], dict[str, int]]:
    rows, header_idx = _sniff(text)
    mapping = _map_cols(rows[header_idx])
    parsed: list[dict] = []
    skipped = 0
    for i, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        if not any(c.strip() for c in row):
            continue
        if _skip_row(_cell(row, mapping.get("asset")), _cell(row, mapping.get("disc"))):
            skipped += 1
            continue
        symbol = _clean_symbol(_cell(row, mapping.get("symbol")))
        trade_date = _parse_date(_cell(row, mapping.get("date")))
        if not symbol or not trade_date:
            skipped += 1
            continue
        if symbol.lower() in {"symbol", "ticker", "代码", "total", "合计"}:
            skipped += 1
            continue
        qty = _parse_number(_cell(row, mapping.get("qty"))) if "qty" in mapping else None
        price = _parse_number(_cell(row, mapping.get("price"))) if "price" in mapping else None
        pnl = _parse_number(_cell(row, mapping.get("pnl"))) if "pnl" in mapping else None
        raw_side = _cell(row, mapping.get("side"))
        side = _parse_side(raw_side, qty)
        extra = "|".join(
            [
                _cell(row, mapping.get("time")),
                str(qty or ""),
                str(price or ""),
                str(i),
            ]
        )
        parsed.append(
            {
                "date": trade_date,
                "symbol": symbol,
                "side": side,
                "is_buy": _is_buy(side, qty, raw_side),
                "qty": qty,
                "price": price,
                "pnl": pnl,
                "notes": _cell(row, mapping.get("notes")) or None,
                "extra": extra,
            }
        )
    stats = {"skipped_rows": skipped, "parsed": len(parsed)}
    return parsed, stats


def import_text(db: Session, raw: str | bytes, user_id: int | None = None) -> dict[str, Any]:
    text = _decode(raw) if isinstance(raw, (bytes, bytearray)) else raw
    if not text.strip():
        raise ImportError_("没有可导入的内容")
    parsed, stats = parse_csv(text)
    if not parsed:
        raise ImportError_("没有读到股票成交行。请确认导出的是股票成交/已实现盈亏，不是资金流水。")

    closed = [r for r in parsed if r["pnl"] is not None and abs(r["pnl"]) >= 1e-8]
    if closed:
        trades = [
            {
                "date": r["date"],
                "symbol": r["symbol"],
                "side": r["side"],
                "pnl": r["pnl"],
                "extra": r["extra"],
                "notes": r["notes"] or "券商已实现盈亏",
            }
            for r in closed
        ]
        mode = "realized"
    else:
        fills = [r for r in parsed if r["qty"] and r["price"]]
        if not fills:
            raise ImportError_("有成交行，但没有已实现盈亏，也缺少数量/价格，无法估算。请导出带 Realized P/L 或成交价的明细。")
        trades = _fifo(fills)
        mode = "fifo"
        if not trades:
            raise ImportError_("按 FIFO 估算后没有平仓盈亏（可能这段时间只有开仓、还没卖出）。")

    existing_q = select(TradeLog.external_id).where(TradeLog.external_id.is_not(None))
    if user_id is not None:
        existing_q = existing_q.where(TradeLog.user_id == user_id)
    existing = {x for x in db.scalars(existing_q) if x}
    tags = get_or_create_tags(db, ["券商导入", "股票"])
    added = 0
    dupes = 0
    for row in trades:
        ext = _ext_id(row["date"], row["symbol"], row["side"], row["pnl"], row["extra"])
        if ext in existing:
            dupes += 1
            continue
        db.add(
            TradeLog(
                user_id=user_id,
                date=row["date"],
                symbol=row["symbol"],
                side=row["side"],
                pnl_amount=round(float(row["pnl"]), 4),
                notes=row.get("notes"),
                source="import",
                external_id=ext,
                tags=list(tags),
            )
        )
        existing.add(ext)
        added += 1
    db.commit()
    return {
        "added": added,
        "duplicates": dupes,
        "mode": mode,
        "parsed": stats["parsed"],
        "skipped_rows": stats["skipped_rows"],
    }
