"""风险组合列表：CRUD、危机系数、每日跑批。"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import RiskPortfolio, RiskPortfolioRun, utcnow
from app.services.portfolio import PortfolioError, analyze_portfolio

_log = logging.getLogger("risk_portfolios")
_tick_lock = threading.Lock()


class RiskPortfolioError(Exception):
    pass


def _parse_legs(raw) -> list[dict]:
    if isinstance(raw, list):
        legs = raw
    elif isinstance(raw, str):
        try:
            legs = json.loads(raw or "[]")
        except json.JSONDecodeError as exc:
            raise RiskPortfolioError("legs JSON 无效") from exc
    else:
        legs = []
    if not isinstance(legs, list) or not legs:
        raise RiskPortfolioError("至少需要一只标的")
    out = []
    for item in legs:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol") or "").strip().upper()
        if not sym or sym == "CASH":
            continue
        side = str(item.get("side") or "long").strip().lower()
        if side in ("做空", "short"):
            side = "short"
        else:
            side = "long"
        w = item.get("weight")
        amt = item.get("amount")
        entry = item.get("entry_price")
        if entry is None:
            entry = item.get("cost") or item.get("open_price")
        try:
            w = float(w) if w is not None and w != "" else None
        except (TypeError, ValueError):
            w = None
        try:
            amt = float(amt) if amt is not None and amt != "" else None
        except (TypeError, ValueError):
            amt = None
        try:
            entry = float(entry) if entry is not None and entry != "" else None
        except (TypeError, ValueError):
            entry = None
        if entry is not None and entry <= 0:
            entry = None
        out.append({
            "symbol": sym,
            "weight": w,
            "amount": amt,
            "side": side,
            "entry_price": entry,
        })
    if not out:
        raise RiskPortfolioError("没有有效标的")
    if len(out) > 20:
        raise RiskPortfolioError("最多 20 只标的")
    return out


def _portfolio_pnl_from_entry(legs: list[dict], assets: list[dict]) -> dict | None:
    """按成本价算组合浮盈亏（权重按 |w| 归一）。有成本价才返回。"""
    px_map = {str(a.get("symbol") or "").upper(): a.get("price") for a in (assets or [])}
    signed: list[tuple[float, float]] = []  # (abs_w, pnl)
    rows = []
    for leg in legs:
        sym = leg["symbol"]
        entry = leg.get("entry_price")
        px = px_map.get(sym)
        if entry is None or px is None:
            continue
        try:
            entry_f = float(entry)
            px_f = float(px)
        except (TypeError, ValueError):
            continue
        if entry_f <= 0 or px_f <= 0:
            continue
        side = leg.get("side") or "long"
        if side == "short":
            pnl = (entry_f - px_f) / entry_f
        else:
            pnl = (px_f - entry_f) / entry_f
        w = leg.get("weight")
        amt = leg.get("amount")
        if w is not None:
            abs_w = abs(float(w))
        elif amt is not None:
            abs_w = abs(float(amt))
        else:
            abs_w = 1.0
        if abs_w <= 0:
            continue
        signed.append((abs_w, pnl))
        rows.append({
            "symbol": sym,
            "side": side,
            "entry_price": entry_f,
            "price": px_f,
            "pnl": round(pnl, 6),
        })
    if not signed:
        return None
    gross = sum(a for a, _ in signed)
    if gross <= 0:
        return None
    port_pnl = sum(a / gross * p for a, p in signed)
    return {
        "unrealized_pnl": round(port_pnl, 6),
        "drawdown_from_entry": round(max(0.0, -port_pnl), 6),
        "legs": rows,
    }


def _legs_json(legs: list[dict]) -> str:
    return json.dumps(legs, ensure_ascii=False)


def _leg_weights(legs: list[dict]) -> list[tuple[dict, float]]:
    """返回 (leg, signed_weight) 毛敞口归一。"""
    signed = []
    for leg in legs:
        side = -1.0 if leg.get("side") == "short" else 1.0
        if leg.get("weight") is not None:
            mag = abs(float(leg["weight"]))
        elif leg.get("amount") is not None:
            mag = abs(float(leg["amount"]))
        else:
            mag = 1.0
        signed.append((leg, side * mag))
    gross = sum(abs(w) for _, w in signed) or 1.0
    return [(leg, w / gross) for leg, w in signed]


def capture_budget_baseline(legs: list[dict], capital: float) -> dict:
    """记下预算起点各标的现价，作为一个月后对比基准。"""
    from app.services.ohlc import OhlcError, fetch_closes

    prices: dict[str, float] = {}
    errors: dict[str, str] = {}
    for leg, _w in _leg_weights(legs):
        sym = leg["symbol"]
        # 优先用成本价作为起点（你真实持仓成本）；否则拉现价
        if leg.get("entry_price"):
            prices[sym] = float(leg["entry_price"])
            continue
        try:
            ohlc = fetch_closes(sym, "1d", apply_live=True)
            px = ohlc.get("price")
            closes = ohlc.get("closes") or []
            val = float(px) if px is not None else (float(closes[-1]) if closes else None)
            if val is None or val <= 0:
                raise OhlcError("无报价")
            prices[sym] = val
        except Exception as exc:  # noqa: BLE001
            errors[sym] = str(exc)
    if not prices:
        raise RiskPortfolioError("无法采集预算起点价格：" + "; ".join(f"{k}:{v}" for k, v in errors.items()))
    return {
        "prices": prices,
        "capital": float(capital),
        "errors": errors or None,
        "captured_at": utcnow().isoformat(),
    }


def _parse_entry_date(raw) -> date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    text = str(raw).strip()[:10]
    try:
        y, m, d = text.split("-")
        return date(int(y), int(m), int(d))
    except Exception as exc:  # noqa: BLE001
        raise RiskPortfolioError(f"持仓日期无效：{raw}") from exc


def close_on_or_before(symbol: str, day: date) -> tuple[float, date]:
    """取持仓日（或之前最近交易日）收盘价。"""
    from app.services.ohlc import OhlcError, _bar_day, fetch_closes_covering

    start = day - timedelta(days=14)
    ohlc = fetch_closes_covering(symbol, "1d", start=start)
    bars = ohlc.get("ohlc_bars") or []
    best: tuple[float, date] | None = None
    for bar in bars:
        bd = _bar_day(bar.get("ts"))
        if bd is None:
            continue
        try:
            px = float(bar.get("close"))
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        if bd <= day:
            best = (px, bd)
        elif bd > day and best is not None:
            break
    if best is None:
        raise RiskPortfolioError(f"{symbol} 在 {day} 附近找不到收盘价")
    return best


def build_outlook_budget(
    legs: list[dict],
    *,
    capital: float,
    entry_date: date,
    horizon_days: int = 21,
    window: int = 252,
    n_sims: int = 2000,
    drift_mode: str = "historical",
    loss_limit: float = 0.05,
    model_id: str = "block_bootstrap",
) -> dict:
    """
    按持仓第一天收盘价为起点，用预算模型估算预计持仓天后中位盈利。
    """
    from app.services.budget_models import BudgetModelError, run_budget_model

    if capital <= 0:
        raise RiskPortfolioError("预算本金须大于 0")
    horizon_days = max(5, min(int(horizon_days or 21), 126))
    prices: dict[str, float] = {}
    price_days: dict[str, str] = {}
    enriched: list[dict] = []
    for leg in legs:
        sym = leg["symbol"]
        px, d = close_on_or_before(sym, entry_date)
        prices[sym] = px
        price_days[sym] = d.isoformat()
        item = dict(leg)
        item["entry_price"] = px
        enriched.append(item)

    try:
        out = run_budget_model(
            model_id=model_id,
            legs=enriched,
            capital=float(capital),
            horizon_days=horizon_days,
            window=window,
            n_sims=n_sims,
            drift_mode=drift_mode,
            loss_limit=loss_limit,
        )
    except BudgetModelError as exc:
        raise RiskPortfolioError(str(exc)) from exc

    return {
        "legs": enriched,
        "prices": prices,
        "price_days": price_days,
        "entry_date": entry_date.isoformat(),
        "capital": float(capital),
        "horizon_days": out["horizon_days"],
        "model_id": out["model_id"],
        "drift_mode": drift_mode,
        "median_terminal": out["median_terminal"],
        "target_pnl": out["median_pnl"],
        "p5_pnl": out["p5_pnl"],
        "p95_pnl": out["p95_pnl"],
        "prob_profit": out["prob_profit"],
        "prob_mdd_gt_10pct": out.get("prob_mdd_gt_10pct"),
        "verdict": out["verdict"],
        "note": (
            f"以 {entry_date} 持仓日收盘为起点，"
            f"{out.get('model_label') or out['model_id']} 展望 {out['horizon_days']} 个交易日"
            f"中位盈利约 ${out['median_pnl']:,.2f}"
        ),
    }


def apply_budget(
    row: RiskPortfolio,
    *,
    capital: float | None,
    target_pnl: float | None = None,
    budget_days: int | None = 30,
    model_pnl: float | None = None,
    reset_baseline: bool = True,
    entry_date=None,
    auto_outlook: bool = True,
    horizon_days: int | None = None,
    window: int | None = None,
    drift_mode: str = "historical",
    loss_limit: float = 0.05,
    model_id: str = "block_bootstrap",
) -> RiskPortfolio:
    """
    写入/更新一个月预算。
    若提供持仓日期且 auto_outlook：按持仓日收盘 + 预算模型自动算目标盈利。
    """
    ed = _parse_entry_date(entry_date) if entry_date is not None else row.entry_date

    # 清空
    if target_pnl is None and not auto_outlook and ed is None:
        row.budget_target_pnl = None
        row.budget_capital = None
        row.budget_start_date = None
        row.budget_baseline = None
        row.budget_model_pnl = None
        row.budget_review = None
        row.entry_date = None
        return row

    cap = float(capital) if capital not in (None, "") else float(row.budget_capital or 10000)
    if cap <= 0:
        raise RiskPortfolioError("预算本金须大于 0")

    days = max(7, min(int(budget_days or 30), 120))
    hz = max(5, min(int(horizon_days or row.horizon_days or 21), 126))
    win = max(30, min(int(window or row.window or 252), 1000))
    legs = _parse_legs(row.legs)

    outlook = None
    if auto_outlook and ed is not None:
        outlook = build_outlook_budget(
            legs,
            capital=cap,
            entry_date=ed,
            horizon_days=hz,
            window=win,
            n_sims=2000,
            drift_mode=drift_mode or "historical",
            loss_limit=float(row.loss_limit or 0.05),
            model_id=model_id or "block_bootstrap",
        )
        target_f = float(outlook["target_pnl"])
        model_f = target_f
        row.legs = _legs_json(outlook["legs"])
        base = {
            "prices": outlook["prices"],
            "price_days": outlook["price_days"],
            "capital": cap,
            "entry_date": ed.isoformat(),
            "outlook": {
                "horizon_days": hz,
                "model_id": outlook.get("model_id"),
                "drift_mode": drift_mode,
                "target_pnl": target_f,
                "p5_pnl": outlook.get("p5_pnl"),
                "p95_pnl": outlook.get("p95_pnl"),
                "prob_profit": outlook.get("prob_profit"),
                "verdict": outlook.get("verdict"),
                "note": outlook.get("note"),
            },
            "captured_at": utcnow().isoformat(),
        }
        row.budget_baseline = json.dumps(base, ensure_ascii=False)
        row.budget_start_date = ed
        row.entry_date = ed
        row.budget_review = None
    else:
        if target_pnl is None or target_pnl == "":
            raise RiskPortfolioError("请填写持仓日期以自动计算预算，或手动提供目标盈利")
        target_f = float(target_pnl)
        model_f = float(model_pnl) if model_pnl is not None else target_f
        if reset_baseline or not row.budget_baseline:
            # 有持仓日则用持仓日收盘，否则现价/成本
            if ed is not None:
                prices = {}
                price_days = {}
                enriched = []
                for leg in legs:
                    px, d = close_on_or_before(leg["symbol"], ed)
                    prices[leg["symbol"]] = px
                    price_days[leg["symbol"]] = d.isoformat()
                    item = dict(leg)
                    item["entry_price"] = px
                    enriched.append(item)
                row.legs = _legs_json(enriched)
                base = {
                    "prices": prices,
                    "price_days": price_days,
                    "capital": cap,
                    "entry_date": ed.isoformat(),
                    "captured_at": utcnow().isoformat(),
                }
                row.budget_baseline = json.dumps(base, ensure_ascii=False)
                row.budget_start_date = ed
                row.entry_date = ed
            else:
                base = capture_budget_baseline(legs, cap)
                row.budget_baseline = json.dumps(base, ensure_ascii=False)
                row.budget_start_date = datetime.now(timezone.utc).date()
            row.budget_review = None

    row.budget_capital = cap
    row.budget_target_pnl = target_f
    row.budget_days = days
    row.budget_model_pnl = model_f
    row.horizon_days = hz
    return row


def evaluate_month_budget(
    row: RiskPortfolio,
    current_prices: dict[str, float] | None = None,
) -> dict | None:
    """对比持仓日起预算目标 vs 至今实际盈亏。"""
    target = row.budget_target_pnl
    if target is None:
        return None
    try:
        target_f = float(target)
    except (TypeError, ValueError):
        return None

    baseline = _detail_loads(row.budget_baseline) or {}
    start_prices = baseline.get("prices") or {}
    if not start_prices:
        return {"ok": False, "note": "预算未采集到起点价格，请填写持仓日期后重新保存"}

    capital = float(row.budget_capital or baseline.get("capital") or 0.0)
    if capital <= 0:
        capital = 10000.0

    legs = _parse_legs(row.legs)
    weighted = _leg_weights(legs)
    prices = dict(current_prices or {})
    if len(prices) < len(start_prices):
        from app.services.ohlc import fetch_closes

        for leg, _w in weighted:
            sym = leg["symbol"]
            if sym in prices:
                continue
            try:
                ohlc = fetch_closes(sym, "1d", apply_live=True)
                px = ohlc.get("price")
                closes = ohlc.get("closes") or []
                val = float(px) if px is not None else (float(closes[-1]) if closes else None)
                if val and val > 0:
                    prices[sym] = val
            except Exception:  # noqa: BLE001
                pass

    ret = 0.0
    used = 0.0
    detail_legs = []
    for leg, w in weighted:
        sym = leg["symbol"]
        p0 = start_prices.get(sym)
        p1 = prices.get(sym)
        if p0 is None or p1 is None or p0 <= 0:
            continue
        r = (p1 / p0) - 1.0
        contrib = w * r
        ret += contrib
        used += abs(w)
        detail_legs.append({
            "symbol": sym,
            "side": leg.get("side") or "long",
            "weight": round(w, 6),
            "start_price": round(float(p0), 4),
            "price": round(float(p1), 4),
            "return": round(r if w >= 0 else -r, 6),
            "pnl": round(capital * contrib, 2),
        })

    if used < 0.5:
        return {"ok": False, "note": "缺少足够现价，无法核算预算进度"}

    actual_pnl = round(capital * ret, 2)
    target_return = target_f / capital if capital else None
    days = max(1, int(row.budget_days or 30))
    start = row.budget_start_date or row.entry_date
    today = datetime.now(timezone.utc).date()
    elapsed = (today - start).days if start else 0
    remaining = max(0, days - elapsed)
    due = elapsed >= days
    progress = (actual_pnl / target_f) if abs(target_f) > 1e-9 else None
    time_frac = min(1.0, elapsed / days) if days else 1.0
    expected_now = target_f * time_frac
    # 目标为负时：实际越高越好（少亏）；不能沿用「超前→止盈」话术
    neg_target = target_f < 0

    if due:
        if neg_target:
            if actual_pnl >= 0:
                status = "好于展望"
            elif actual_pnl >= target_f:
                status = "好于/贴近展望"
            elif actual_pnl >= target_f * 1.5:
                status = "接近展望"
            else:
                status = "差于展望"
        elif actual_pnl >= target_f:
            status = "达标"
        elif actual_pnl >= target_f * 0.7:
            status = "接近"
        elif actual_pnl >= 0:
            status = "未达标"
        else:
            status = "亏损"
    else:
        if neg_target:
            # expected_now 为负；实际 ≥ 时间比例展望 → 比悲观外推更好
            if actual_pnl >= 0:
                status = "好于展望"
            elif actual_pnl >= expected_now:
                status = "跟上"
            elif actual_pnl >= expected_now * 1.5:
                status = "接近"
            else:
                status = "回撤中"
        elif actual_pnl >= expected_now * 1.15:
            status = "超前"
        elif actual_pnl >= expected_now * 0.7:
            status = "跟上"
        elif actual_pnl >= 0:
            status = "落后"
        else:
            status = "回撤中"

    model_pnl = row.budget_model_pnl
    gap_vs_model = None if model_pnl is None else round(actual_pnl - float(model_pnl), 2)
    outlook = (baseline.get("outlook") or {}) if isinstance(baseline, dict) else {}

    hints = []
    if neg_target:
        hints.append(
            f"持仓日展望中位为负（约 ${target_f:,.0f}）：近一年样本外推偏空，"
            "不宜当「盈利预算」追进度；优先看危机系数与是否减仓。"
        )
    if due:
        if status in ("达标", "好于展望", "好于/贴近展望"):
            if neg_target:
                hints.append("期末实际好于悲观展望。仍勿因一次反弹大幅加仓。")
            else:
                hints.append("已达到持仓日展望中位盈利。可维持结构，勿因一次达标大幅加仓。")
        elif status in ("接近", "接近展望"):
            hints.append("接近展望中位。可微调权重，避免冲刺放大回撤。")
        elif status == "未达标":
            hints.append("未达展望中位。下次可用 μ=0 风险视角重估，或提高安全仓位/现金垫。")
        elif status == "差于展望":
            hints.append("实际差于悲观展望。优先安全仓位重配，并收紧亏损底线。")
        else:
            hints.append("期间录得亏损。优先安全仓位重配，并收紧亏损底线。")
    else:
        if status in ("落后", "回撤中"):
            hints.append(f"进度落后时间线（应按比例约 ${expected_now:,.0f}）。可提前降仓。")
        elif status == "超前":
            hints.append("进度超前于时间线。可部分止盈，避免追高。")
        elif status == "好于展望":
            hints.append("实际好于悲观展望（样本中位为负）。勿解读成该加仓追高。")
        else:
            hints.append("进度大致跟上展望时间线，继续看危机系数与路径回撤。")

    end_date = (start + timedelta(days=days)) if start else None
    return {
        "ok": True,
        "capital": round(capital, 2),
        "target_pnl": round(target_f, 2),
        "target_return": None if target_return is None else round(target_return, 6),
        "actual_pnl": actual_pnl,
        "actual_return": round(ret, 6),
        "progress": None if progress is None else round(progress, 4),
        "expected_pnl_by_now": round(expected_now, 2),
        "status": status,
        "due": due,
        "budget_days": days,
        "elapsed_days": elapsed,
        "remaining_days": remaining,
        "start_date": start.isoformat() if start else None,
        "entry_date": (row.entry_date.isoformat() if row.entry_date else None),
        "end_date": end_date.isoformat() if end_date else None,
        "model_pnl": None if model_pnl is None else round(float(model_pnl), 2),
        "gap_vs_model": gap_vs_model,
        "p5_pnl": outlook.get("p5_pnl"),
        "p95_pnl": outlook.get("p95_pnl"),
        "prob_profit": outlook.get("prob_profit"),
        "outlook_note": outlook.get("note"),
        "legs": detail_legs,
        "hints": hints,
        "hint": hints[0] if hints else "",
    }


def _detail_loads(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def to_out(row: RiskPortfolio, runs: list[RiskPortfolioRun] | None = None) -> dict:
    try:
        legs = json.loads(row.legs or "[]")
    except json.JSONDecodeError:
        legs = []
    detail = _detail_loads(row.last_detail)
    vix = None
    if isinstance(detail, dict):
        vix = detail.get("vix") or ((detail.get("crisis") or {}).get("vix"))
    budget = None
    try:
        budget = evaluate_month_budget(row)
    except Exception:  # noqa: BLE001
        budget = {"ok": False, "note": "预算核算失败"}
    return {
        "id": row.id,
        "name": row.name,
        "notes": row.notes,
        "legs": legs,
        "loss_limit": row.loss_limit,
        "window": row.window,
        "horizon_days": row.horizon_days,
        "enabled": bool(row.enabled),
        "crisis_coefficient": row.crisis_coefficient,
        "crisis_level": row.crisis_level,
        "vix": vix,
        "budget_capital": row.budget_capital,
        "budget_target_pnl": row.budget_target_pnl,
        "budget_days": row.budget_days,
        "budget_start_date": row.budget_start_date.isoformat() if row.budget_start_date else None,
        "entry_date": row.entry_date.isoformat() if row.entry_date else None,
        "budget_model_pnl": row.budget_model_pnl,
        "budget": budget,
        "last_detail": detail,
        "last_error": row.last_error,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "last_run_date": row.last_run_date.isoformat() if row.last_run_date else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "recent_runs": [
            {
                "run_date": r.run_date.isoformat(),
                "crisis_coefficient": r.crisis_coefficient,
                "crisis_level": r.crisis_level,
            }
            for r in (runs or [])
        ],
    }


def list_portfolios(db: Session) -> list[RiskPortfolio]:
    return db.query(RiskPortfolio).order_by(RiskPortfolio.id.desc()).all()


def get_portfolio(db: Session, pid: int) -> RiskPortfolio | None:
    return db.get(RiskPortfolio, pid)


def list_runs(db: Session, portfolio_id: int, limit: int = 30) -> list[RiskPortfolioRun]:
    return (
        db.query(RiskPortfolioRun)
        .filter(RiskPortfolioRun.portfolio_id == portfolio_id)
        .order_by(RiskPortfolioRun.run_date.desc(), RiskPortfolioRun.id.desc())
        .limit(limit)
        .all()
    )


def create_portfolio(
    db: Session,
    *,
    name: str,
    legs: list[dict],
    notes: str | None = None,
    loss_limit: float = 0.05,
    window: int = 252,
    horizon_days: int = 21,
    enabled: bool = False,
    budget_capital: float | None = None,
    budget_target_pnl: float | None = None,
    budget_days: int = 30,
    budget_model_pnl: float | None = None,
    entry_date=None,
    budget_model_id: str = "block_bootstrap",
) -> RiskPortfolio:
    name = (name or "").strip()
    if not name:
        raise RiskPortfolioError("请填写组合名称")
    if db.query(RiskPortfolio).filter(RiskPortfolio.name == name).first():
        raise RiskPortfolioError(f"名称已存在：{name}")
    parsed = _parse_legs(legs)
    row = RiskPortfolio(
        name=name,
        notes=(notes or "").strip() or None,
        legs=_legs_json(parsed),
        loss_limit=float(loss_limit or 0.05),
        window=max(30, min(int(window or 252), 1000)),
        horizon_days=max(1, min(int(horizon_days or 21), 252)),
        enabled=1 if enabled else 0,
    )
    ed = _parse_entry_date(entry_date)
    if ed is not None or (budget_target_pnl is not None and budget_target_pnl != ""):
        apply_budget(
            row,
            capital=budget_capital if budget_capital is not None else 10000,
            target_pnl=budget_target_pnl,
            budget_days=budget_days,
            model_pnl=budget_model_pnl,
            reset_baseline=True,
            entry_date=ed,
            auto_outlook=ed is not None,
            horizon_days=row.horizon_days,
            window=row.window,
            model_id=budget_model_id or "block_bootstrap",
        )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_portfolio(
    db: Session,
    row: RiskPortfolio,
    *,
    name: str | None = None,
    legs: list[dict] | None = None,
    notes: str | None = None,
    loss_limit: float | None = None,
    window: int | None = None,
    horizon_days: int | None = None,
    enabled: bool | None = None,
    budget_capital: float | None = None,
    budget_target_pnl: float | None = None,
    budget_days: int | None = None,
    budget_model_pnl: float | None = None,
    reset_budget: bool | None = None,
    entry_date=None,
    budget_model_id: str | None = None,
) -> RiskPortfolio:
    if name is not None:
        name = name.strip()
        if not name:
            raise RiskPortfolioError("请填写组合名称")
        clash = (
            db.query(RiskPortfolio)
            .filter(RiskPortfolio.name == name, RiskPortfolio.id != row.id)
            .first()
        )
        if clash:
            raise RiskPortfolioError(f"名称已存在：{name}")
        row.name = name
    if legs is not None:
        row.legs = _legs_json(_parse_legs(legs))
    if notes is not None:
        row.notes = notes.strip() or None
    if loss_limit is not None:
        row.loss_limit = float(loss_limit)
    if window is not None:
        row.window = max(30, min(int(window), 1000))
    if horizon_days is not None:
        row.horizon_days = max(1, min(int(horizon_days), 252))
    if enabled is not None:
        row.enabled = 1 if enabled else 0

    ed = None
    has_entry = False
    if entry_date is not None:
        has_entry = True
        ed = _parse_entry_date(entry_date)

    if has_entry or budget_target_pnl is not None or reset_budget or budget_capital is not None:
        if reset_budget and not has_entry and budget_target_pnl is None and ed is None:
            apply_budget(row, capital=None, target_pnl=None, auto_outlook=False)
        else:
            use_date = ed if has_entry else row.entry_date
            apply_budget(
                row,
                capital=budget_capital if budget_capital is not None else row.budget_capital or 10000,
                target_pnl=budget_target_pnl,
                budget_days=budget_days if budget_days is not None else row.budget_days,
                model_pnl=budget_model_pnl,
                reset_baseline=True if reset_budget is None else bool(reset_budget),
                entry_date=use_date,
                auto_outlook=use_date is not None,
                horizon_days=horizon_days or row.horizon_days,
                window=window or row.window,
                model_id=budget_model_id or "block_bootstrap",
            )
    db.commit()
    db.refresh(row)
    return row


def set_enabled(db: Session, row: RiskPortfolio, enabled: bool) -> RiskPortfolio:
    row.enabled = 1 if enabled else 0
    db.commit()
    db.refresh(row)
    return row


def delete_portfolio(db: Session, row: RiskPortfolio) -> None:
    db.query(RiskPortfolioRun).filter(RiskPortfolioRun.portfolio_id == row.id).delete()
    db.delete(row)
    db.commit()


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def fetch_vix_panic() -> dict:
    """
    拉取 VIX 现价与近端变化，映射为恐慌加分（0–25 量级）。
    档位：<15 平静 · 15–20 正常 · 20–30 抬升 · 30–40 恐慌 · >40 极端。
    """
    from app.services.ohlc import OhlcError, fetch_closes

    try:
        ohlc = fetch_closes("VIX", "1d", apply_live=True)
    except OhlcError as exc:
        return {"ok": False, "error": str(exc), "vix_score": 0.0}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "vix_score": 0.0}

    closes = [float(x) for x in (ohlc.get("closes") or []) if x is not None]
    px = ohlc.get("price")
    try:
        level = float(px) if px is not None else (closes[-1] if closes else None)
    except (TypeError, ValueError):
        level = closes[-1] if closes else None
    if level is None or level <= 0:
        return {"ok": False, "error": "VIX 无有效报价", "vix_score": 0.0}

    def _chg(n: int) -> float | None:
        if len(closes) <= n or closes[-1 - n] <= 0:
            return None
        return closes[-1] / closes[-1 - n] - 1.0

    chg_5d = _chg(5)
    chg_20d = _chg(20)

    # 水平分（最高约 20）
    if level < 15:
        level_score = 0.0
        regime = "平静"
    elif level < 20:
        level_score = (level - 15) / 5.0 * 4.0
        regime = "正常"
    elif level < 25:
        level_score = 4.0 + (level - 20) / 5.0 * 5.0
        regime = "抬升"
    elif level < 30:
        level_score = 9.0 + (level - 25) / 5.0 * 5.0
        regime = "偏高"
    elif level < 40:
        level_score = 14.0 + (level - 30) / 10.0 * 6.0
        regime = "恐慌"
    else:
        level_score = min(20.0, 20.0 + (level - 40) / 20.0 * 2.0)
        regime = "极端"

    # 急涨加分（5 日涨幅）
    spike_score = 0.0
    if chg_5d is not None and chg_5d > 0:
        # +20% → ~5 分；+40% → ~10 分，封顶 8
        spike_score = _clamp(chg_5d / 0.20 * 5.0, 0.0, 8.0)

    vix_score = _clamp(level_score + spike_score, 0.0, 25.0)
    return {
        "ok": True,
        "level": round(level, 4),
        "chg_5d": None if chg_5d is None else round(chg_5d, 6),
        "chg_20d": None if chg_20d is None else round(chg_20d, 6),
        "regime": regime,
        "level_score": round(level_score, 2),
        "spike_score": round(spike_score, 2),
        "vix_score": round(vix_score, 2),
        "source": ohlc.get("source"),
    }


def compute_crisis_coefficient(
    analysis: dict,
    loss_limit: float,
    legs: list[dict] | None = None,
    vix: dict | None = None,
) -> dict:
    """
    危机系数 0–100（越高越危险）。

    若腿上填了成本价 entry_price：回撤压迫按「相对买入价的浮亏」计算，
    不再用样本期历史峰值回撤（那会把买入前的暴跌也算进去）。
    未填成本价时仍用样本持有 MDD，并标注 basis=sample_hold。
    另加入 VIX 恐慌水平与近 5 日急涨。
    """
    port = analysis.get("portfolio") or {}
    forecast = analysis.get("forecast") or {}
    safe = analysis.get("safe_allocate") or {}
    dd = port.get("drawdown") or {}
    block = forecast.get("block_bootstrap") or forecast.get("bootstrap") or {}
    assets = analysis.get("assets") or []

    hist_mdd = float(dd.get("max_drawdown") or 0.0)
    limit = max(float(loss_limit or 0.05), 1e-6)

    entry_pnl = _portfolio_pnl_from_entry(legs or [], assets)
    if entry_pnl is not None:
        basis = "cost"
        from_entry_dd = float(entry_pnl["drawdown_from_entry"])
        remaining = limit - from_entry_dd
        if remaining <= 0:
            mdd_score = 100.0
        else:
            mdd_score = _clamp((from_entry_dd / limit) * 35.0)
        thin_budget = _clamp((1.0 - remaining / limit) * 15.0) if remaining > 0 else 15.0
        ref_dd = from_entry_dd
    else:
        basis = "sample_hold"
        mdd_score = _clamp((hist_mdd / limit) * 12.0, 0.0, 25.0)
        thin_budget = 0.0
        ref_dd = hist_mdd
        entry_pnl = None

    p_mdd = float(block.get("prob_mdd_gt_10pct") or 0.0)
    p_loss = float(block.get("prob_terminal_loss_10pct") or 0.0)
    cvar = float(block.get("cvar_5pct_loss") or 0.0)
    unrecovered = float(block.get("unrecovered_rate_mdd10") or 0.0)
    path_score = _clamp(p_mdd * 25.0 + p_loss * 15.0 + min(cvar, 0.25) / 0.25 * 10.0)

    hedge = (safe.get("primary_hedge") or {}) if isinstance(safe, dict) else {}
    hedge_corr = hedge.get("corr")
    hedge_score = 0.0
    if hedge_corr is not None:
        hc = float(hedge_corr)
        if hc >= 0:
            hedge_score = _clamp(20.0 + hc * 15.0)
        else:
            hedge_score = _clamp(max(0.0, 10.0 + hc * 20.0))

    unrecovered_score = _clamp(unrecovered * 10.0)

    vix = vix or {}
    vix_score = float(vix.get("vix_score") or 0.0) if vix.get("ok") else 0.0

    total = _clamp(
        mdd_score
        + path_score * 0.7
        + hedge_score
        + unrecovered_score * 0.5
        + thin_budget
        + vix_score
    )

    if total >= 70:
        level = "高危"
    elif total >= 40:
        level = "警惕"
    elif total >= 20:
        level = "关注"
    else:
        level = "平稳"

    note_bits = []
    if basis == "cost":
        note_bits.append("回撤按成本价计算（买入前的历史暴跌不计入）")
    else:
        note_bits.append("未填成本价：样本持有回撤仅弱参考；请填写买入价以按你的仓位计价")
    if vix.get("ok"):
        note_bits.append(
            f"VIX={vix.get('level')}（{vix.get('regime')}）计入恐慌分 {vix_score}"
        )
    elif vix.get("error"):
        note_bits.append(f"VIX 未计入：{vix.get('error')}")

    return {
        "crisis_coefficient": round(total, 2),
        "crisis_level": level,
        "basis": basis,
        "vix": vix if vix else None,
        "components": {
            "mdd_vs_limit": round(mdd_score, 2),
            "path_risk": round(path_score, 2),
            "hedge_breakdown": round(hedge_score, 2),
            "unrecovered": round(unrecovered_score, 2),
            "thin_budget": round(thin_budget, 2) if basis == "cost" else 0.0,
            "vix_panic": round(vix_score, 2),
            "hist_mdd": hist_mdd,
            "ref_drawdown": ref_dd,
            "loss_limit": limit,
            "unrealized_pnl": None if entry_pnl is None else entry_pnl["unrealized_pnl"],
            "drawdown_from_entry": None if entry_pnl is None else entry_pnl["drawdown_from_entry"],
            "entry_legs": None if entry_pnl is None else entry_pnl["legs"],
            "prob_mdd_gt_10pct": p_mdd,
            "prob_terminal_loss_10pct": p_loss,
            "hedge_corr": hedge_corr,
            "primary_hedge": hedge or None,
            "note": "；".join(note_bits),
        },
    }


def run_crisis_scan(
    db: Session,
    row: RiskPortfolio,
    *,
    force: bool = False,
    n_sims: int = 2000,
    run_date=None,
) -> RiskPortfolio:
    today = run_date or after_us_close_ready()["et_date"]
    if not force and row.last_run_date == today and row.crisis_coefficient is not None:
        return row

    legs = _parse_legs(row.legs)
    try:
        analysis = analyze_portfolio(
            legs,
            window=row.window or 252,
            confidence=0.95,
            capital=None,
            horizon_days=row.horizon_days or 21,
            n_sims=n_sims,
            drift_mode="zero",  # 日扫描用风险视角，避免牛市漂移掩盖危机
            loss_limit=row.loss_limit or 0.05,
        )
        vix = fetch_vix_panic()
        crisis = compute_crisis_coefficient(
            analysis, row.loss_limit or 0.05, legs=legs, vix=vix
        )
        # 用分析里的现价做预算进度（避免再拉一遍）
        px_map = {
            str(a.get("symbol") or "").upper(): a.get("price")
            for a in (analysis.get("assets") or [])
            if a.get("price") is not None
        }
        budget = evaluate_month_budget(row, current_prices=px_map)
        if budget and budget.get("ok") and budget.get("due"):
            row.budget_review = json.dumps(budget, ensure_ascii=False)
        detail = {
            "crisis": crisis,
            "basis": crisis.get("basis"),
            "vix": vix,
            "budget": budget,
            "portfolio_vol_annual": (analysis.get("portfolio") or {}).get("vol_annual"),
            "hist_mdd": ((analysis.get("portfolio") or {}).get("drawdown") or {}).get("max_drawdown"),
            "safe_allocate": {
                "hist_mdd": (analysis.get("safe_allocate") or {}).get("hist_mdd"),
                "cash_weight": (analysis.get("safe_allocate") or {}).get("cash_weight"),
                "primary_hedge": (analysis.get("safe_allocate") or {}).get("primary_hedge"),
            },
            "forecast_block": {
                k: (analysis.get("forecast") or {}).get("block_bootstrap", {}).get(k)
                for k in (
                    "prob_profit",
                    "prob_mdd_gt_10pct",
                    "prob_terminal_loss_10pct",
                    "cvar_5pct_loss",
                    "unrecovered_rate_mdd10",
                )
            },
            "symbols": analysis.get("symbols"),
            "scanned_at": utcnow().isoformat(),
        }
        row.crisis_coefficient = crisis["crisis_coefficient"]
        row.crisis_level = crisis["crisis_level"]
        row.last_detail = json.dumps(detail, ensure_ascii=False)
        row.last_error = None
        row.last_run_at = utcnow()
        row.last_run_date = today

        existing = (
            db.query(RiskPortfolioRun)
            .filter(
                RiskPortfolioRun.portfolio_id == row.id,
                RiskPortfolioRun.run_date == today,
            )
            .first()
        )
        if existing:
            existing.crisis_coefficient = crisis["crisis_coefficient"]
            existing.crisis_level = crisis["crisis_level"]
            existing.detail = row.last_detail
        else:
            db.add(
                RiskPortfolioRun(
                    portfolio_id=row.id,
                    run_date=today,
                    crisis_coefficient=crisis["crisis_coefficient"],
                    crisis_level=crisis["crisis_level"],
                    detail=row.last_detail,
                )
            )
        db.commit()
        db.refresh(row)
        return row
    except (PortfolioError, RiskPortfolioError) as exc:
        row.last_error = str(exc)[:250]
        row.last_run_at = utcnow()
        db.commit()
        db.refresh(row)
        raise RiskPortfolioError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        row.last_error = str(exc)[:250]
        row.last_run_at = utcnow()
        db.commit()
        db.refresh(row)
        raise RiskPortfolioError(f"扫描失败：{exc}") from exc


def _et_trading_day(day) -> bool:
    from app.services.tradingview import _is_trading_day

    return _is_trading_day(day)


def after_us_close_ready(now: datetime | None = None) -> dict:
    """
    是否已到「美股常规交易收盘后」可跑日扫描的窗口。
    交易日 16:05 ET 起可跑；用 ET 日历日作为 last_run_date。
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover
        from backports.zoneinfo import ZoneInfo  # type: ignore

    et = ZoneInfo("America/New_York")
    now = now or datetime.now(et)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone(et)
    else:
        now = now.astimezone(et)

    trade_day = now.date()
    ready = False
    reason = "waiting"
    if not _et_trading_day(trade_day):
        reason = "non_trading_day"
    elif (now.hour, now.minute) >= (16, 5):
        ready = True
        reason = "after_close"
    else:
        reason = "before_close"

    return {
        "ready": ready,
        "reason": reason,
        "et_date": trade_day,
        "et_now": now.isoformat(),
    }


def seconds_until_next_post_close(now: datetime | None = None, *, after_attempt: bool = False) -> float:
    """
    距下一次收盘扫描的秒数。
    - 交易日且已过 16:05：返回短等待（应立即扫）；若 after_attempt=True 则跳到下一交易日。
    - 否则等到本/下一交易日 16:05 ET。
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover
        from backports.zoneinfo import ZoneInfo  # type: ignore
    from datetime import date, time, timedelta

    et = ZoneInfo("America/New_York")
    now = now or datetime.now(et)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone(et)
    else:
        now = now.astimezone(et)

    target_t = time(16, 5)

    def next_trade_day(d: date) -> date:
        d = d + timedelta(days=1)
        while not _et_trading_day(d):
            d += timedelta(days=1)
        return d

    if after_attempt:
        d = next_trade_day(now.date())
        target = datetime(d.year, d.month, d.day, target_t.hour, target_t.minute, tzinfo=et)
        return max(30.0, (target - now).total_seconds())

    if _et_trading_day(now.date()) and now.time() < target_t:
        target = datetime(
            now.year, now.month, now.day, target_t.hour, target_t.minute, tzinfo=et
        )
        return max(30.0, (target - now).total_seconds())

    if _et_trading_day(now.date()) and now.time() >= target_t:
        # 已在今日收盘窗口，应马上检查是否还有未扫组合
        return 30.0

    d = next_trade_day(now.date())
    target = datetime(d.year, d.month, d.day, target_t.hour, target_t.minute, tzinfo=et)
    return max(30.0, (target - now).total_seconds())


def tick_due_risk_portfolios(*, force_window: bool = False) -> dict:
    """美股收盘后对已开启组合跑危机系数（每个组合每个 ET 交易日最多一次）。"""
    if not _tick_lock.acquire(blocking=False):
        return {"ran": 0, "skipped": True}
    try:
        window = after_us_close_ready()
        if not force_window and not window["ready"]:
            return {
                "ran": 0,
                "skipped": True,
                "reason": window["reason"],
                "et_now": window["et_now"],
                "next_wait_sec": seconds_until_next_post_close(),
            }

        from app.database import SessionLocal

        db = SessionLocal()
        try:
            today = window["et_date"]
            rows = (
                db.query(RiskPortfolio)
                .filter(RiskPortfolio.enabled == 1)
                .order_by(RiskPortfolio.id.asc())
                .all()
            )
            ran = 0
            errors: list[str] = []
            for row in rows:
                if row.last_run_date == today and row.crisis_coefficient is not None:
                    continue
                try:
                    # 用 ET 交易日写入 last_run_date
                    run_crisis_scan(
                        db, row, force=False, n_sims=1500, run_date=today
                    )
                    ran += 1
                    _log.info(
                        "risk portfolio #%s %s crisis=%s level=%s",
                        row.id,
                        row.name,
                        row.crisis_coefficient,
                        row.crisis_level,
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{row.name}: {exc}")
                    _log.exception("risk portfolio scan failed id=%s", row.id)
            return {
                "ran": ran,
                "errors": errors,
                "date": today.isoformat(),
                "reason": window["reason"],
            }
        finally:
            db.close()
    except Exception:
        _log.exception("risk portfolio tick failed")
        return {"ran": 0, "errors": ["日扫描失败"]}
    finally:
        _tick_lock.release()
