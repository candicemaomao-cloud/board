"""实时播报标的：按用户存 JSON 列表。"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import User

DEFAULT_LIVE_SYMBOLS = [
    {"symbol": "NVDA", "name": "英伟达", "source": "tradingview"},
    {"symbol": "AAPL", "name": "苹果", "source": "tradingview"},
    {"symbol": "TSLA", "name": "特斯拉", "source": "tradingview"},
    {"symbol": "MU", "name": "美光半导体", "source": "tradingview"},
    {"symbol": "AMD", "name": "AMD", "source": "tradingview"},
    {"symbol": "MSFT", "name": "微软", "source": "tradingview"},
]


def _norm_item(raw: dict) -> dict | None:
    symbol = str(raw.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    source = str(raw.get("source") or "tradingview").strip().lower()
    if source not in {"tradingview", "binance", "yahoo"}:
        source = "tradingview"
    name = str(raw.get("name") or symbol).strip() or symbol
    return {"symbol": symbol, "name": name, "source": source}


def parse_live_symbols(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 兼容逗号分隔
        return [
            {"symbol": x.strip().upper(), "name": x.strip().upper(), "source": "tradingview"}
            for x in raw.split(",")
            if x.strip()
        ]
    if not isinstance(data, list):
        return []
    out = []
    seen = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        item = _norm_item(row)
        if not item or item["symbol"] in seen:
            continue
        seen.add(item["symbol"])
        out.append(item)
    return out


def get_live_symbols(user: User) -> list[dict]:
    items = parse_live_symbols(user.live_symbols)
    return items if items else list(DEFAULT_LIVE_SYMBOLS)


def set_live_symbols(db: Session, user: User, items: list[dict]) -> list[dict]:
    cleaned = []
    seen = set()
    for row in items or []:
        item = _norm_item(row if isinstance(row, dict) else {"symbol": row})
        if not item or item["symbol"] in seen:
            continue
        seen.add(item["symbol"])
        cleaned.append(item)
    user.live_symbols = json.dumps(cleaned, ensure_ascii=False)
    db.commit()
    db.refresh(user)
    return cleaned


def add_live_symbol(db: Session, user: User, symbol: str, name: str | None = None, source: str = "tradingview") -> list[dict]:
    items = parse_live_symbols(user.live_symbols)
    if not items:
        items = list(DEFAULT_LIVE_SYMBOLS)
    item = _norm_item({"symbol": symbol, "name": name or symbol, "source": source})
    if not item:
        raise ValueError("请填写股票代码")
    items = [x for x in items if x["symbol"] != item["symbol"]]
    items.append(item)
    return set_live_symbols(db, user, items)


def remove_live_symbol(db: Session, user: User, symbol: str) -> list[dict]:
    sym = (symbol or "").strip().upper()
    items = parse_live_symbols(user.live_symbols)
    if not items:
        items = list(DEFAULT_LIVE_SYMBOLS)
    items = [x for x in items if x["symbol"] != sym]
    return set_live_symbols(db, user, items)
