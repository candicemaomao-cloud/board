"""用户虚拟币列表 CRUD（按用户隔离，默认种主流币）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CryptoCoin
from app.services.binance import normalize_symbol
from app.services.crypto_market import DEFAULT_COINS, enrich_coin_row, enrich_coin_rows


class CryptoCoinError(ValueError):
    pass


def _base_out(row: CryptoCoin) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "symbol": row.symbol,
        "name": row.name or row.symbol,
        "coingecko_id": row.coingecko_id,
        "binance_symbol": row.binance_symbol,
        "sort_order": row.sort_order or 0,
        "notes": row.notes or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def to_out(row: CryptoCoin, *, with_quote: bool = False) -> dict:
    data = _base_out(row)
    if with_quote:
        return enrich_coin_row(data)
    return data


def list_out(rows: list[CryptoCoin], *, with_quote: bool = False) -> list[dict]:
    items = [_base_out(r) for r in rows]
    if with_quote:
        return enrich_coin_rows(items)
    return items


def list_coins(db: Session, user_id: int) -> list[CryptoCoin]:
    return list(
        db.scalars(
            select(CryptoCoin)
            .where(CryptoCoin.user_id == user_id)
            .order_by(CryptoCoin.sort_order.asc(), CryptoCoin.id.asc())
        )
    )


def ensure_defaults(db: Session, user_id: int) -> list[CryptoCoin]:
    rows = list_coins(db, user_id)
    if rows:
        return rows
    for i, item in enumerate(DEFAULT_COINS):
        db.add(
            CryptoCoin(
                user_id=user_id,
                symbol=item["symbol"],
                name=item["name"],
                coingecko_id=item.get("coingecko_id"),
                binance_symbol=item.get("binance_symbol"),
                sort_order=i,
            )
        )
    db.commit()
    return list_coins(db, user_id)


def get_coin(db: Session, coin_id: int, user_id: int) -> CryptoCoin | None:
    row = db.get(CryptoCoin, coin_id)
    if not row or row.user_id != user_id:
        return None
    return row


def create_coin(
    db: Session,
    *,
    user_id: int,
    symbol: str,
    name: str | None = None,
    coingecko_id: str | None = None,
    binance_symbol: str | None = None,
    notes: str | None = None,
    sort_order: int | None = None,
) -> CryptoCoin:
    sym = (symbol or "").strip().upper().replace("/", "").replace("-", "")
    if sym.endswith("USDT"):
        sym = sym[:-4]
    if not sym:
        raise CryptoCoinError("请填写币种代码，例如 BTC")
    if db.scalar(select(CryptoCoin).where(CryptoCoin.user_id == user_id, CryptoCoin.symbol == sym)):
        raise CryptoCoinError(f"已有 {sym}")
    bn = normalize_symbol(binance_symbol or sym)
    order = sort_order
    if order is None:
        existing = list_coins(db, user_id)
        order = (existing[-1].sort_order + 1) if existing else 0
    row = CryptoCoin(
        user_id=user_id,
        symbol=sym[:32],
        name=(name or sym).strip()[:64],
        coingecko_id=(coingecko_id or "").strip() or None,
        binance_symbol=bn[:32] if bn else None,
        notes=(notes or "").strip() or None,
        sort_order=int(order),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_coin(db: Session, row: CryptoCoin, data: dict) -> CryptoCoin:
    if "symbol" in data and data["symbol"] is not None:
        sym = str(data["symbol"]).strip().upper().replace("/", "").replace("-", "")
        if sym.endswith("USDT"):
            sym = sym[:-4]
        if not sym:
            raise CryptoCoinError("请填写币种代码")
        other = db.scalar(
            select(CryptoCoin).where(
                CryptoCoin.user_id == row.user_id,
                CryptoCoin.symbol == sym,
                CryptoCoin.id != row.id,
            )
        )
        if other:
            raise CryptoCoinError(f"已有 {sym}")
        row.symbol = sym[:32]
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()[:64] or row.symbol
    if "coingecko_id" in data:
        raw = data["coingecko_id"]
        row.coingecko_id = (str(raw).strip() if raw is not None else "") or None
    if "binance_symbol" in data:
        raw = data["binance_symbol"]
        row.binance_symbol = normalize_symbol(raw)[:32] if raw else normalize_symbol(row.symbol)
    if "notes" in data:
        raw = data["notes"]
        row.notes = (str(raw).strip() if raw is not None else "") or None
    if "sort_order" in data and data["sort_order"] is not None:
        row.sort_order = int(data["sort_order"])
    db.commit()
    db.refresh(row)
    return row


def delete_coin(db: Session, row: CryptoCoin) -> None:
    db.delete(row)
    db.commit()
