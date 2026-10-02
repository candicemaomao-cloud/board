from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HomeMessageDismissal, User
from app.services.auth import require_user
from app.services.board_calendar import build_board_calendar, watchlist_earnings_month
from app.services.positions import list_positions, quote_spec

router = APIRouter()


def _calendar_messages(db: Session, today: date) -> list[dict]:
    day = today.isoformat()
    base = (build_board_calendar(today).get("by_date") or {}).get(day, [])
    earnings = (watchlist_earnings_month(db, year=today.year, month=today.month, today=today).get("by_date") or {}).get(day, [])
    out = []
    for event in [*base, *earnings]:
        kind = event.get("kind") or "event"
        out.append({
            "key": f"{day}:calendar:{kind}:{event.get('key') or event.get('name')}",
            "kind": "earnings" if kind == "earnings" else "macro",
            "title": event.get("name") or "今日事件",
            "detail": event.get("detail") or "今日发布",
            "symbol": event.get("symbol"),
            "tone": "warning" if kind in {"fed", "treasury", "witching"} else "info",
        })
    return out


def _position_messages(db: Session, user: User, today: date) -> list[dict]:
    positions = [row for row in list_positions(db, status="开始", user_id=user.id) if row.shares and row.open_price]
    if not positions:
        return []
    items, specs = [], []
    for row in positions:
        symbol, source = quote_spec(row.name, row.platform)
        specs.append((row, symbol))
        items.append({"symbol": symbol, "name": row.name, "source": source})
    try:
        from app.services.quotes import fetch_mixed
        quotes = fetch_mixed(items)
    except Exception:
        quotes = []
    by_key = {}
    for quote in quotes:
        for key in (quote.get("symbol"), quote.get("requested"), quote.get("name")):
            if key:
                by_key[str(key).upper()] = quote
    out = []
    day = today.isoformat()
    for row, symbol in specs:
        quote = by_key.get((symbol or "").upper()) or by_key.get((row.name or "").upper())
        price = quote.get("price") if quote else None
        if price is None:
            continue
        price, cost = float(price), float(row.open_price)
        profitable = price >= cost
        pnl_pct = (price / cost - 1) * 100 if cost else 0
        status = "above" if profitable else "below"
        out.append({
            "key": f"{day}:position:{row.id}:{status}",
            "kind": "position",
            "title": f"{row.name} {'已达到盈利点' if profitable else '跌破盈亏平衡点'}",
            "detail": f"现价 ${price:,.2f} · 成本 ${cost:,.2f} · {pnl_pct:+.2f}%",
            "symbol": row.name,
            "tone": "positive" if profitable else "negative",
        })
    return out


@router.get("/messages")
def home_messages(db: Session = Depends(get_db), user: User = Depends(require_user)):
    today = date.today()
    messages = _calendar_messages(db, today) + _position_messages(db, user, today)
    dismissed = set(db.scalars(select(HomeMessageDismissal.message_key).where(HomeMessageDismissal.user_id == user.id)))
    return {"date": today.isoformat(), "items": [item for item in messages if item["key"] not in dismissed]}


@router.delete("/messages/{message_key:path}", status_code=204)
def dismiss_home_message(message_key: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    exists = db.scalar(select(HomeMessageDismissal).where(HomeMessageDismissal.user_id == user.id, HomeMessageDismissal.message_key == message_key))
    if not exists:
        db.add(HomeMessageDismissal(user_id=user.id, message_key=message_key[:255]))
        db.commit()
