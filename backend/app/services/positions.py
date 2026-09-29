from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.models import Position, Side, Strategy, TradeLog
from app.services.tags import get_or_create_tags

PLATFORMS = {"众安", "币安"}
STATUSES = ("未开始", "开始", "已结束")
STATUS_ORDER = {name: i for i, name in enumerate(STATUSES)}


class PositionError(Exception):
    pass


def _trade_symbol(name: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9.]", "", name).upper()
    return (compact or name)[:16]


def quote_spec(name: str, platform: str) -> tuple[str, str]:
    from app.services.binance import looks_like_crypto, normalize_symbol

    compact = re.sub(r"[^A-Za-z0-9.]", "", name).upper()
    if looks_like_crypto(compact) or platform == "币安":
        return normalize_symbol(compact), "binance"
    return compact, "tradingview"


def _pnl(open_amount: float, compare: float | None, fee: float = 0) -> tuple[float, float | None]:
    if compare is None:
        return round(-float(fee or 0), 4), None
    amount = round(compare - open_amount - float(fee or 0), 4)
    pct = round(amount / open_amount, 6) if open_amount else None
    return amount, pct


def _holding_days(opened_on: date | None, closed_on: date | None, status: str) -> int:
    if status == "未开始" or not opened_on:
        return 0
    end = closed_on if status == "已结束" and closed_on else date.today()
    if end < opened_on:
        return 0
    return (end - opened_on).days + 1


def _notional(price: float, shares: float) -> float:
    return round(float(price) * float(shares), 4)


def _apply_cost(row: Position, open_price: float, shares: float) -> None:
    if shares <= 0:
        raise PositionError("持仓数量必须大于 0")
    if open_price <= 0:
        raise PositionError("开仓价格必须大于 0")
    row.open_price = open_price
    row.shares = shares
    amount = _notional(open_price, shares)
    row.open_amount = amount
    if row.status != "已结束":
        row.market_value = amount


def _reason(raw: str | None) -> str | None:
    text = (raw or "").strip()
    return text[:64] or None


def to_out(row: Position) -> dict:
    open_price = row.open_price or (row.open_amount / row.shares if row.shares else 0)
    market_value = _notional(open_price, row.shares)
    compare = row.close_amount if row.status == "已结束" else market_value
    fee = float(getattr(row, "fee", 0) or 0)
    pnl_amount, pnl_pct = _pnl(row.open_amount or market_value, compare, fee)
    quote_symbol, quote_source = quote_spec(row.name, row.platform)
    strategy_id = getattr(row, "strategy_id", None)
    strategy_name = None
    if strategy_id:
        db = object_session(row)
        plan = db.get(Strategy, strategy_id) if db else None
        strategy_name = plan.name if plan else None
    return {
        "id": row.id,
        "platform": row.platform,
        "status": row.status,
        "name": row.name,
        "quote_symbol": quote_symbol,
        "quote_source": quote_source,
        "shares": row.shares,
        "open_price": round(open_price, 6),
        "market_value": market_value,
        "open_amount": row.open_amount,
        "fee": fee,
        "close_amount": row.close_amount,
        "opened_on": row.opened_on,
        "closed_on": row.closed_on,
        "expected_days": int(getattr(row, "expected_days", None) or 21),
        "notes": row.notes,
        "open_reason": getattr(row, "open_reason", None),
        "close_reason": getattr(row, "close_reason", None),
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "trade_id": row.trade_id,
        "holding_days": _holding_days(row.opened_on, row.closed_on, row.status),
        "pnl_amount": pnl_amount,
        "pnl_pct": pnl_pct,
        "created_at": row.created_at,
    }


def list_positions(
    db: Session,
    platform: str | None = None,
    status: str | None = None,
    user_id: int | None = None,
) -> list[Position]:
    stmt = select(Position)
    if user_id is not None:
        stmt = stmt.where(Position.user_id == user_id)
    if platform:
        stmt = stmt.where(Position.platform == platform)
    if status:
        stmt = stmt.where(Position.status == status)
    rows = list(db.scalars(stmt))
    rows.sort(
        key=lambda r: (
            STATUS_ORDER.get(r.status, 9),
            -(r.opened_on.toordinal() if r.opened_on else 0),
            -r.id,
        )
    )
    return rows


def _clamp_expected_days(raw) -> int:
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = 21
    return max(1, min(days, 252))


def create_position(
    db: Session,
    *,
    platform: str,
    name: str,
    shares: float,
    open_price: float,
    opened_on: date | None,
    notes: str | None,
    fee: float = 0,
    status: str = "未开始",
    open_reason: str | None = None,
    strategy_id: int | None = None,
    expected_days: int = 21,
    user_id: int | None = None,
) -> Position:
    from app.services.binance import looks_like_crypto

    if looks_like_crypto(name) and platform != "币安":
        platform = "币安"
    if platform not in PLATFORMS:
        raise PositionError("平台只能是众安或币安")
    if status not in {"未开始", "开始"}:
        raise PositionError("状态只能是未开始或开始")
    if fee < 0:
        raise PositionError("手续费不能为负")
    plan = None
    if strategy_id:
        plan = db.get(Strategy, strategy_id)
        if not plan:
            raise PositionError("策略不存在")
    row = Position(
        user_id=user_id,
        platform=platform,
        status=status,
        name=name.strip().upper() if looks_like_crypto(name) else name.strip(),
        fee=fee or 0,
        close_amount=None,
        opened_on=opened_on or date.today(),
        expected_days=_clamp_expected_days(expected_days),
        notes=notes,
        open_reason=_reason(open_reason) or (plan.name if plan else None),
        strategy_id=strategy_id,
    )
    _apply_cost(row, open_price, shares)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_position(db: Session, row: Position, data: dict) -> Position:
    from app.services.binance import looks_like_crypto

    if row.status == "已结束":
        raise PositionError("已结束的持仓不能再改，请到交易日志查看")
    if "name" in data and data["name"] is not None:
        name = str(data["name"]).strip()
        if looks_like_crypto(name):
            data["name"] = name.upper()
            data.setdefault("platform", "币安")
            if data.get("platform") != "币安":
                data["platform"] = "币安"
    if "platform" in data and data["platform"] not in PLATFORMS:
        raise PositionError("平台只能是众安或币安")
    if "name" in data and data["name"]:
        data["name"] = data["name"].strip()
    new_status = data.pop("status", row.status)
    if new_status not in {"未开始", "开始"}:
        raise PositionError("状态只能是未开始或开始，结束请用平仓")
    data.pop("opened_on", None)
    shares = data.pop("shares", row.shares)
    open_price = data.pop("open_price", row.open_price or (row.open_amount / row.shares if row.shares else 0))
    if "fee" in data:
        if data["fee"] is not None and data["fee"] < 0:
            raise PositionError("手续费不能为负")
        row.fee = data.pop("fee") or 0
    if "expected_days" in data and data["expected_days"] is not None:
        data["expected_days"] = _clamp_expected_days(data["expected_days"])
    if "open_reason" in data:
        data["open_reason"] = _reason(data["open_reason"])
    if "strategy_id" in data and data["strategy_id"]:
        plan = db.get(Strategy, data["strategy_id"])
        if not plan:
            raise PositionError("策略不存在")
        if not data.get("open_reason") and not row.open_reason:
            data["open_reason"] = plan.name
    for key, value in data.items():
        setattr(row, key, value)
    if row.status == "未开始" and new_status == "开始":
        row.opened_on = date.today()
        row.closed_on = None
        row.close_amount = None
    row.status = new_status
    _apply_cost(row, open_price, shares)
    db.commit()
    db.refresh(row)
    return row


def close_position(
    db: Session,
    row: Position,
    *,
    close_amount: float,
    closed_on: date | None,
    notes: str | None,
    fee: float | None = None,
    close_reason: str | None = None,
) -> Position:
    if row.status == "已结束":
        raise PositionError("这笔持仓已经结束")
    if row.status != "开始":
        raise PositionError("只有已开始的持仓才能平仓")
    if fee is not None:
        if fee < 0:
            raise PositionError("手续费不能为负")
        row.fee = fee
    closed_on = closed_on or date.today()
    row.status = "已结束"
    row.close_amount = close_amount
    row.closed_on = closed_on
    row.close_reason = _reason(close_reason)
    if notes:
        row.notes = notes

    pnl_amount, pnl_pct = _pnl(row.open_amount, close_amount, row.fee or 0)
    ext = f"pos:{row.id}"
    existing = db.scalars(select(TradeLog).where(TradeLog.external_id == ext)).first()
    tag_names = ["持仓平仓", row.platform]
    if row.open_reason:
        tag_names.append(f"开仓·{row.open_reason}")
    if row.close_reason:
        tag_names.append(f"关仓·{row.close_reason}")
    reason_note = " · ".join(
        part
        for part in (
            f"开仓原因 {row.open_reason}" if row.open_reason else "",
            f"关仓原因 {row.close_reason}" if row.close_reason else "",
        )
        if part
    )
    default_note = f"{row.platform} {row.name} {row.shares:g}股 开仓 {row.open_amount} 关仓 {close_amount} 手续费 {row.fee or 0}"
    if reason_note:
        default_note = f"{default_note} · {reason_note}"
    if existing is None:
        trade = TradeLog(
            user_id=row.user_id,
            date=closed_on,
            symbol=_trade_symbol(row.name),
            side=Side.LONG,
            pnl_amount=pnl_amount,
            pnl_pct=pnl_pct,
            notes=notes or default_note,
            source="position",
            external_id=ext,
            tags=get_or_create_tags(db, tag_names),
        )
        db.add(trade)
        db.flush()
        row.trade_id = trade.id
    else:
        row.trade_id = existing.id

    db.commit()
    db.refresh(row)
    return row


def edit_closed_record(db: Session, row: Position, data: dict) -> Position:
    """已结束的持仓不让走 update_position()/close_position()（那两个是给未结束/
    开始状态用的），但录入的时候难免手滑打错开仓价、关仓金额之类的数字——这个
    专门给「已结束」状态用，只能改开仓记录（股份/开仓价/开仓日期/开仓原因）和
    关仓记录（关仓金额/关仓日期/关仓原因/手续费），平台、状态、策略这些字段
    这里不接受、也不会改。

    改完以后同步更新已经生成的交易日志（trade_id 关联的那条 TradeLog）的盈亏
    金额和日期，不然交易日志里的数字会跟持仓记录对不上——但交易日志里的备注/
    标签留着不动，那些可能是用户自己在交易日志页面里改过的，这里不该覆盖。
    """
    if row.status != "已结束":
        raise PositionError("这笔持仓还没结束，改开仓用编辑，结束用平仓")

    shares = data["shares"]
    open_price = data["open_price"]
    opened_on = data["opened_on"]
    close_amount = data["close_amount"]
    closed_on = data.get("closed_on") or date.today()
    fee = data.get("fee")

    if not opened_on:
        raise PositionError("开仓日期不能为空")
    if closed_on < opened_on:
        raise PositionError("关仓日期不能早于开仓日期")
    if close_amount is None or close_amount < 0:
        raise PositionError("关仓金额不能为负")
    if fee is not None and fee < 0:
        raise PositionError("手续费不能为负")

    _apply_cost(row, open_price, shares)
    row.opened_on = opened_on
    row.open_reason = _reason(data.get("open_reason"))
    row.close_amount = close_amount
    row.closed_on = closed_on
    row.close_reason = _reason(data.get("close_reason"))
    if fee is not None:
        row.fee = fee

    pnl_amount, pnl_pct = _pnl(row.open_amount, close_amount, row.fee or 0)
    if row.trade_id:
        trade = db.get(TradeLog, row.trade_id)
        if trade:
            trade.pnl_amount = pnl_amount
            trade.pnl_pct = pnl_pct
            trade.date = closed_on

    db.commit()
    db.refresh(row)
    return row


def mark_open_positions(db: Session, user_id: int | None = None) -> dict:
    rows = [r for r in list_positions(db, user_id=user_id) if r.status == "开始"]
    invested = sum(float(r.open_amount or 0) for r in rows)
    fees = sum(float(getattr(r, "fee", 0) or 0) for r in rows)
    items = []
    specs: list[tuple[Position, str]] = []
    for row in rows:
        symbol, source = quote_spec(row.name, row.platform)
        specs.append((row, symbol))
        if symbol:
            items.append({"symbol": symbol, "name": row.name, "source": source})
    quotes = []
    if items:
        from app.services.quotes import fetch_mixed

        quotes = fetch_mixed(items)
    by_key: dict[str, dict] = {}
    for quote in quotes:
        for key in (quote.get("symbol"), quote.get("requested"), quote.get("name")):
            if key:
                by_key[str(key).upper()] = quote
    live_value = 0.0
    for row, symbol in specs:
        quote = by_key.get((symbol or "").upper()) or by_key.get((row.name or "").upper())
        price = quote.get("price") if quote else None
        if price:
            live_value += float(price) * float(row.shares or 0)
        else:
            live_value += float(row.open_amount or 0)
    return {
        "invested": round(invested, 4),
        "fees": round(fees, 4),
        "live_value": round(live_value, 4),
        "pnl": round(live_value - invested - fees, 4),
    }


def estimate_position_outlook(
    *,
    name: str | None = None,
    platform: str = "众安",
    capital: float,
    horizon_days: int = 21,
    side: str = "long",
    open_price: float | None = None,
    window: int = 252,
    n_sims: int = 2000,
    model_id: str = "block_bootstrap",
    legs: list[dict] | None = None,
    portfolio_name: str | None = None,
) -> dict:
    """记持仓时：走统一预算模型，反算持仓 N 天中位赚亏。可单票或组合腿。"""
    from app.services.budget_models import BudgetModelError, run_budget_model

    capital = float(capital or 0)
    if capital <= 0:
        raise PositionError("请先填写开仓价格和股份，才能反算收益")

    side_n = "short" if str(side or "").lower() in ("short", "做空") else "long"
    use_legs: list[dict]
    label_sym: str
    if legs:
        use_legs = []
        for raw in legs:
            sym = str((raw or {}).get("symbol") or "").strip().upper()
            if not sym:
                continue
            leg_side = str((raw or {}).get("side") or "long").strip().lower()
            if leg_side in ("short", "做空"):
                leg_side = "short"
            else:
                leg_side = "long"
            use_legs.append({
                "symbol": sym,
                "weight": (raw or {}).get("weight"),
                "amount": (raw or {}).get("amount"),
                "side": leg_side,
            })
        if not use_legs:
            raise PositionError("组合没有有效腿")
        label_sym = portfolio_name or "+".join(x["symbol"] for x in use_legs[:4])
        if len(use_legs) > 4:
            label_sym += "…"
    else:
        if not name:
            raise PositionError("请填写股票代码")
        symbol, _src = quote_spec(name, platform)
        if not symbol:
            raise PositionError("股票代码无效")
        use_legs = [{"symbol": symbol, "amount": capital, "side": side_n}]
        label_sym = symbol

    try:
        out = run_budget_model(
            model_id=model_id,
            legs=use_legs,
            horizon_days=horizon_days,
            capital=capital,
            window=window,
            n_sims=n_sims,
            drift_mode="historical",
        )
    except BudgetModelError as exc:
        raise PositionError(str(exc)) from exc

    return {
        "ok": True,
        "symbol": label_sym,
        "side": side_n if not legs else "portfolio",
        "capital": out["capital"],
        "open_price": open_price,
        "horizon_days": out["horizon_days"],
        "model_id": out["model_id"],
        "model_label": out.get("model_label"),
        "median_terminal": out["median_terminal"],
        "median_pnl": out["median_pnl"],
        "p5_pnl": out["p5_pnl"],
        "p95_pnl": out["p95_pnl"],
        "prob_profit": out["prob_profit"],
        "prob_mdd_gt_10pct": out.get("prob_mdd_gt_10pct"),
        "mu_annual_approx": out.get("mu_annual_approx"),
        "verdict": out["verdict"],
        "avoid_entry": out["avoid_entry"],
        "hint": out["hint"],
        "note": out.get("note"),
        "drift_bias": None,
        "portfolio_name": portfolio_name,
        "legs": out.get("legs") or use_legs,
    }
