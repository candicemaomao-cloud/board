"""统计套利：两条腿的价差 / OLS 残差 / 比值，用 z 分数开平仓。"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ArbStrategy, utcnow
from app.services.ohlc import OhlcError, fetch_closes
from app.services.intraday_universe import clip_hedges

KINDS = {
    "ols": "OLS 回归残差",
    "ratio": "对数比值",
    "dollar": "名义中性价差",
}
TIMEFRAMES = {"5m", "30m", "4h", "1d"}
TF_LABEL = {"5m": "5min", "30m": "30min", "4h": "4h", "1d": "D线"}
BARS_PER_YEAR = {"5m": 252 * 78, "30m": 252 * 13, "4h": 252 * 7, "1d": 252}
ET = ZoneInfo("America/New_York")


class ArbError(Exception):
    pass


def _round(x, n=4):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), n)


def _sym(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not s:
        raise ArbError("请填写两个标的代码")
    return s[:32]


def _kind(raw: str) -> str:
    k = (raw or "ols").strip().lower()
    if k not in KINDS:
        raise ArbError("对冲方式只能是 OLS 残差 / 对数比值 / 名义中性")
    return k


def _tf(raw: str) -> str:
    t = (raw or "1d").strip()
    if t not in TIMEFRAMES:
        raise ArbError("周期只能是 5m / 30m / 4h / 1d")
    return t


def _dump_factors(raw) -> str | None:
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [p.strip() for p in raw.replace(",", "+").split("+") if p.strip()]
    if not isinstance(raw, list):
        return None
    vals = []
    for x in raw:
        s = str(x).strip().upper()
        if s and s not in vals:
            vals.append(s)
    return json.dumps(vals, ensure_ascii=False) if vals else None


def _factors_of(row: ArbStrategy) -> list[str]:
    raw = getattr(row, "factors", None)
    if raw:
        packed = _dump_factors(raw)
        if packed:
            return json.loads(packed)
    notes = row.notes or ""
    if "日内残差" in notes and "~" in notes:
        hedge = notes.split("~", 1)[1]
        hedge = hedge.split("。")[0].replace(" ", "")
        return [p.strip().upper() for p in hedge.split("+") if p.strip()]
    return []


def _hedges(row: ArbStrategy) -> list[str]:
    vals = _factors_of(row)
    b = (row.leg_b or "").strip().upper()
    a = (row.leg_a or "").strip().upper()
    if b and b != a and b not in vals:
        vals = [*vals, b] if vals else [b]
    out = []
    for s in vals:
        if s and s != a and s not in out:
            out.append(s)
    return clip_hedges(out, a)


def _use_residual(row: ArbStrategy) -> bool:
    hedges = _hedges(row)
    if not hedges:
        return False
    if row.timeframe in {"5m", "30m"}:
        return True
    return len(hedges) >= 2


def to_out(row: ArbStrategy) -> dict:
    factors = _hedges(row)
    return {
        "id": row.id,
        "name": row.name,
        "notes": row.notes,
        "kind": row.kind,
        "kind_label": KINDS.get(row.kind, row.kind),
        "timeframe": row.timeframe,
        "timeframe_label": TF_LABEL.get(row.timeframe, row.timeframe),
        "factors": factors,
        "mode": "residual" if _use_residual(row) else "pair",
        "leg_a": row.leg_a,
        "leg_b": row.leg_b,
        "lookback": row.lookback,
        "entry_z": row.entry_z,
        "exit_z": row.exit_z,
        "stop_z": row.stop_z,
        "beta": row.beta,
        "notional": row.notional or 10000,
        "bt_days": int(row.bt_days or 0),
        "macro_filter": bool(row.macro_filter),
        "text": _text(row),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _text(row: ArbStrategy) -> str:
    hedge = KINDS.get(row.kind, row.kind)
    extra = f" β={row.beta:g}" if row.kind != "ols" and row.beta else ""
    cash = row.notional or 10000
    span = {180: "近6个月", 365: "近1年", 730: "近2年"}.get(int(row.bt_days or 0), "全部数据")
    tf = TF_LABEL.get(row.timeframe, row.timeframe)
    factors = _hedges(row)
    pair = f"{row.leg_a} vs {'+'.join(factors)}" if factors else f"{row.leg_a} / {row.leg_b}"
    model = "残差" if _use_residual(row) else hedge
    return (
        f"{pair} · {tf} · {model}{extra} · "
        f"投入${cash:,.0f} · {span} · "
        f"窗口{row.lookback} · 开±{row.entry_z:g} 平±{row.exit_z:g} 停±{row.stop_z:g}"
    )


def _legs_from_payload(payload: dict) -> tuple[str, str, str | None]:
    a = _sym(payload.get("leg_a"))
    packed = _dump_factors(payload.get("factors"))
    hedges = json.loads(packed) if packed else []
    b_raw = (payload.get("leg_b") or "").strip().upper()
    if b_raw and b_raw != a and b_raw not in hedges:
        hedges.append(b_raw)
    hedges = [h for h in hedges if h and h != a]
    hedges = clip_hedges(hedges, a)
    if not hedges:
        raise ArbError("请至少选一个对冲标的，优先 1 只，最多 2 只")
    return a, hedges[0], _dump_factors(hedges)


def list_arbs(db: Session) -> list[ArbStrategy]:
    return list(db.scalars(select(ArbStrategy).order_by(ArbStrategy.id.desc())))


def get_arb(db: Session, arb_id: int) -> ArbStrategy | None:
    return db.get(ArbStrategy, arb_id)


def create_arb(db: Session, payload: dict) -> ArbStrategy:
    name = (payload.get("name") or "").strip()
    if not name:
        raise ArbError("请填写策略名")
    if db.scalar(select(ArbStrategy).where(ArbStrategy.name == name)):
        raise ArbError("已有同名策略")
    leg_a, leg_b, factors = _legs_from_payload(payload)
    row = ArbStrategy(
        name=name[:64],
        notes=(payload.get("notes") or None),
        kind=_kind(payload.get("kind")),
        timeframe=_tf(payload.get("timeframe")),
        leg_a=leg_a,
        leg_b=leg_b,
        lookback=_clip_int(payload.get("lookback"), 20, 500, 60),
        entry_z=_clip_float(payload.get("entry_z"), 0.5, 6, 2.0),
        exit_z=_clip_float(payload.get("exit_z"), 0.1, 3, 0.5),
        stop_z=_clip_float(payload.get("stop_z"), 1.5, 12, 3.5),
        beta=_opt_float(payload.get("beta")),
        notional=_clip_notional(payload.get("notional")),
        bt_days=_clip_bt_days(payload.get("bt_days")),
        macro_filter=1 if payload.get("macro_filter") else 0,
        factors=factors,
    )
    if row.leg_a == row.leg_b:
        raise ArbError("两个标的不能相同")
    if row.exit_z >= row.entry_z:
        raise ArbError("平仓 z 必须小于开仓 z")
    if row.stop_z <= row.entry_z:
        raise ArbError("止损 z 必须大于开仓 z")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_arb(db: Session, row: ArbStrategy, payload: dict) -> ArbStrategy:
    name = (payload.get("name") or "").strip()
    if not name:
        raise ArbError("请填写策略名")
    clash = db.scalar(select(ArbStrategy).where(ArbStrategy.name == name, ArbStrategy.id != row.id))
    if clash:
        raise ArbError("已有同名策略")
    row.name = name[:64]
    row.notes = payload.get("notes") or None
    row.kind = _kind(payload.get("kind"))
    row.timeframe = _tf(payload.get("timeframe"))
    row.leg_a, row.leg_b, row.factors = _legs_from_payload(payload)
    row.lookback = _clip_int(payload.get("lookback"), 20, 500, 60)
    row.entry_z = _clip_float(payload.get("entry_z"), 0.5, 6, 2.0)
    row.exit_z = _clip_float(payload.get("exit_z"), 0.1, 3, 0.5)
    row.stop_z = _clip_float(payload.get("stop_z"), 1.5, 12, 3.5)
    row.beta = _opt_float(payload.get("beta"))
    row.notional = _clip_notional(payload.get("notional"))
    row.bt_days = _clip_bt_days(payload.get("bt_days"))
    row.macro_filter = 1 if payload.get("macro_filter") else 0
    row.updated_at = utcnow()
    if row.leg_a == row.leg_b:
        raise ArbError("两个标的不能相同")
    if row.exit_z >= row.entry_z:
        raise ArbError("平仓 z 必须小于开仓 z")
    if row.stop_z <= row.entry_z:
        raise ArbError("止损 z 必须大于开仓 z")
    db.commit()
    db.refresh(row)
    return row


def delete_arb(db: Session, row: ArbStrategy) -> None:
    db.delete(row)
    db.commit()


def _clip_int(v, lo, hi, default):
    try:
        x = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def _clip_float(v, lo, hi, default):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def _clip_notional(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 10_000.0
    return max(100.0, min(10_000_000.0, round(x, 2)))


def _clip_bt_days(v):
    try:
        x = int(v)
    except (TypeError, ValueError):
        return 0
    if x <= 0:
        return 0
    return max(90, min(2000, x))


def _opt_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bar_key(bar: dict, timeframe: str) -> str:
    ts = int(bar["ts"])
    if timeframe in {"1d", "1w"}:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    return str(ts)


def _align(bars_a: list[dict], bars_b: list[dict], timeframe: str) -> tuple[list[float], list[float], list[str]]:
    mb = {_bar_key(b, timeframe): float(b["close"]) for b in bars_b if b.get("close") is not None}
    xs, ys, keys = [], [], []
    for a in bars_a:
        if a.get("close") is None:
            continue
        k = _bar_key(a, timeframe)
        if k not in mb:
            continue
        xs.append(float(a["close"]))
        ys.append(mb[k])
        keys.append(k)
    return xs, ys, keys


def _as_day(key: str, timeframe: str) -> str:
    if timeframe in {"1d", "1w"} or (len(key) >= 10 and key[4:5] == "-"):
        return key[:10]
    try:
        return datetime.fromtimestamp(int(key), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return key[:10]


def _bar_stamp(key: str, timeframe: str) -> str:
    if timeframe in {"1d", "1w"}:
        return _as_day(key, timeframe)
    try:
        ts = int(key)
        return datetime.fromtimestamp(ts, tz=ET).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return _as_day(key, timeframe)


def _slice_bt(px_a, px_b, dates, timeframe: str, bt_days: int, lookback: int):
    """留下回测窗口，并多留 lookback 根做 z 热身。"""
    if not dates:
        return px_a, px_b, dates, None
    if not bt_days:
        return px_a, px_b, dates, None
    end_day = date.fromisoformat(_as_day(dates[-1], timeframe))
    cut = (end_day - timedelta(days=int(bt_days))).isoformat()
    i_cut = 0
    for i, key in enumerate(dates):
        if _as_day(key, timeframe) >= cut:
            i_cut = i
            break
    i0 = max(0, i_cut - lookback)
    return px_a[i0:], px_b[i0:], dates[i0:], cut


def _window_label(start: str, end: str) -> str:
    try:
        d0 = date.fromisoformat(start[:10])
        d1 = date.fromisoformat(end[:10])
    except ValueError:
        return f"{start} → {end}"
    days = max(1, (d1 - d0).days)
    months = round(days / 30.44, 1)
    if days >= 360:
        return f"{start} → {end}（约 {days / 365.25:.1f} 年）"
    return f"{start} → {end}（约 {months} 个月）"


def _ols_beta(y: list[float], x: list[float]) -> float:
    n = len(x)
    if n < 8:
        return 1.0
    mx = mean(x)
    my = mean(y)
    varx = sum((xi - mx) ** 2 for xi in x)
    if varx < 1e-18:
        return 1.0
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    return cov / varx


def _corr(y: list[float], x: list[float]) -> float | None:
    n = len(x)
    if n < 8:
        return None
    mx = mean(x)
    my = mean(y)
    vx = sum((xi - mx) ** 2 for xi in x)
    vy = sum((yi - my) ** 2 for yi in y)
    if vx < 1e-18 or vy < 1e-18:
        return None
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    return cov / math.sqrt(vx * vy)


def _gauss(A: list[list[float]], b: list[float]) -> list[float] | None:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(M[r][i]))
        M[i], M[piv] = M[piv], M[i]
        if abs(M[i][i]) < 1e-18:
            return None
        f = M[i][i]
        for j in range(i, n + 1):
            M[i][j] /= f
        for r in range(n):
            if r == i:
                continue
            g = M[r][i]
            for j in range(i, n + 1):
                M[r][j] -= g * M[i][j]
    return [M[i][n] for i in range(n)]


def _ols_fit(X: list[list[float]], y: list[float]) -> tuple[list[float], list[float]] | None:
    n = len(y)
    k = len(X[0]) if X else 0
    if n <= k or k == 0:
        return None
    xtx = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    beta = _gauss(xtx, xty)
    if beta is None:
        return None
    resid = [y[i] - sum(beta[j] * X[i][j] for j in range(k)) for i in range(n)]
    s2 = sum(r * r for r in resid) / (n - k)
    eye = [[1.0 if i == j else 0.0 for j in range(k)] for i in range(k)]
    inv_cols = [_gauss(xtx, col) for col in eye]
    if any(c is None for c in inv_cols):
        return beta, [0.0] * k
    se = [math.sqrt(max(s2 * inv_cols[i][i], 0.0)) for i in range(k)]
    return beta, se


def _adf(series: list[float]) -> dict | None:
    """残差 ADF（常数项 + 1 阶滞后）。t < 5% 临界值才算平稳。"""
    n = len(series)
    if n < 50:
        return None
    dy = [series[i] - series[i - 1] for i in range(1, n)]
    y = dy[1:]
    X = [[1.0, series[1:-1][i], dy[:-1][i]] for i in range(len(y))]
    fit = _ols_fit(X, y)
    if not fit or fit[1][1] < 1e-18:
        return None
    tstat = fit[0][1] / fit[1][1]
    crit5 = -2.86 - 2.74 / n
    passed = tstat < crit5
    return {
        "t": _round(tstat, 3),
        "crit_5": _round(crit5, 3),
        "n": n,
        "pass": passed,
        "label": "残差平稳，协整关系成立" if passed else "残差不平稳：相关 ≠ 协整 ≠ 可套利",
    }


def _max_dd(bars: list[float]) -> float:
    peak = 0.0
    eq = 0.0
    mdd = 0.0
    for x in bars:
        eq += x
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return mdd


def _cvar(rets: list[float], q: float = 0.05) -> float | None:
    if len(rets) < 20:
        return None
    ranked = sorted(rets)
    k = max(1, int(len(ranked) * q))
    return mean(ranked[:k])


def _logs(px: list[float]) -> list[float]:
    return [math.log(max(v, 1e-12)) for v in px]


def _rets(px: list[float]) -> list[float]:
    out = [0.0]
    for i in range(1, len(px)):
        prev = max(px[i - 1], 1e-12)
        out.append(px[i] / prev - 1.0)
    return out


def _implied_a(kind: str, spread: float, px_b: float, beta: float, a0: float, b0: float) -> float | None:
    b = max(float(px_b), 1e-12)
    if kind == "ratio":
        return math.exp(spread) * b
    if kind == "dollar":
        return float(a0) * (spread + b / max(float(b0), 1e-12))
    return math.exp(spread + float(beta) * math.log(b))


def _implied_b(kind: str, spread: float, px_a: float, beta: float, a0: float, b0: float) -> float | None:
    a = max(float(px_a), 1e-12)
    if kind == "ratio":
        return a / max(math.exp(spread), 1e-12)
    if kind == "dollar":
        return float(b0) * (a / max(float(a0), 1e-12) - spread)
    if abs(float(beta)) < 1e-8:
        return None
    return math.exp((math.log(a) - spread) / float(beta))


def _dollar_pnl(long_spread: bool, a_in: float, a_out: float, b_in: float, b_out: float, beta: float, notional: float) -> float:
    """腿 A 投入 notional，腿 B 对冲 notional×|β|。做多价差=买A卖B。"""
    if a_in <= 0 or b_in <= 0:
        return 0.0
    ret_a = a_out / a_in - 1.0
    ret_b = b_out / b_in - 1.0
    hedge = notional * abs(float(beta) if beta is not None else 1.0)
    if long_spread:
        return notional * ret_a - hedge * ret_b
    return -notional * ret_a + hedge * ret_b


def _spread_at(kind: str, la: float, lb: float, beta: float, a0: float, b0: float) -> float:
    if kind == "ratio":
        return math.log(max(la, 1e-12) / max(lb, 1e-12))
    if kind == "dollar":
        return la / max(a0, 1e-12) - lb / max(b0, 1e-12)
    return math.log(max(la, 1e-12)) - beta * math.log(max(lb, 1e-12))


def _pair_quality(leg_a: str, leg_b: str, px_a: list[float], px_b: list[float], rho_now: float | None) -> dict:
    """相关、趋势、协整。相关高也不等于价差会回来。"""
    ret_a = px_a[-1] / max(px_a[0], 1e-12) - 1.0
    ret_b = px_b[-1] / max(px_b[0], 1e-12) - 1.0
    rho_all = _corr(_rets(px_a)[1:], _rets(px_b)[1:])
    la, lb_log = _logs(px_a), _logs(px_b)
    beta = _ols_beta(la, lb_log)
    resid = [ya - beta * xb for ya, xb in zip(la, lb_log)]
    adf = _adf(resid)
    x, y = resid[:-1], resid[1:]
    phi = 1.0
    if len(x) >= 20:
        mx, my = mean(x), mean(y)
        varx = sum((xi - mx) ** 2 for xi in x)
        if varx > 1e-18:
            phi = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / varx
    half = None
    if 0 < phi < 1:
        half = math.log(0.5) / math.log(phi)
    gap = abs(ret_a - ret_b)
    rho_abs = abs(rho_now) if rho_now is not None else 0.0
    rho_all_abs = abs(rho_all) if rho_all is not None else 0.0
    if adf and not adf["pass"]:
        key, tone, label = "no_coint", "red", "相关不等于协整"
        detail = (
            f"ADF t={adf['t']}，5%临界 {adf['crit_5']}。残差不平稳，价差不必回到均值。"
            f"{leg_a} 区间 {ret_a:+.0%}，{leg_b} {ret_b:+.0%}，全样本相关 {_round(rho_all, 2)}。"
            "关系会变：β、基本面、Regime 一变，均值回归就变成结构性断裂。"
        )
    elif gap >= 0.5 and rho_all_abs < 0.45:
        key, tone, label = "trend", "red", "相对强弱有趋势，不适合硬做回归"
        detail = (
            f"{leg_a} 区间 {ret_a:+.0%}，{leg_b} {ret_b:+.0%}。"
            f"全样本相关 {_round(rho_all, 2)}，近窗 {_round(rho_now, 2)}。"
            "强的一边会把 z 顶在开仓带外。"
        )
    elif rho_abs < 0.25 or rho_all_abs < 0.3:
        key, tone, label = "weak", "orange", "这两个标的联动偏弱"
        detail = (
            f"全样本相关 {_round(rho_all, 2)}，近窗 {_round(rho_now, 2)}。"
            "相对价值要同一经济逻辑，不是都叫科技股。"
        )
    else:
        key, tone, label = "ok", "agree", "协整尚可，仍要看样本外和成本后是否还有边"
        detail = (
            f"全样本相关 {_round(rho_all, 2)}，近窗 {_round(rho_now, 2)}。"
            f"{leg_a} 区间 {ret_a:+.0%}，{leg_b} {ret_b:+.0%}。"
            "目标不是稳赚的一对，是成本后期望为正、样本外还站得住的相对价值。"
        )
    return {
        "key": key,
        "tone": tone,
        "label": label,
        "detail": detail,
        "ret_a": _round(ret_a * 100, 1),
        "ret_b": _round(ret_b * 100, 1),
        "corr_all": _round(rho_all, 3),
        "corr_window": _round(rho_now, 3),
        "half_life": _round(half, 1),
        "adf": adf,
        "hint": "止损后必须回到平仓带，并且 z 重新穿越开仓线，才开下一笔。相关 ≠ 协整 ≠ 可套利。",
    }


def _why_pnl(leg_a: str, trades: list[dict], total: float, wins: list[float], losses: list[float], quality: dict) -> str:
    if not trades:
        return "这段区间没有成交。"
    n = len(trades)
    n_stop = sum(1 for t in trades if t.get("why") == "stop")
    stop_pnl = sum(t.get("pnl") or 0 for t in trades if t.get("why") == "stop")
    short_stops = [t for t in trades if t.get("side") == "short_spread" and t.get("why") == "stop"]
    wr = len(wins) / n * 100
    if total >= 0:
        return f"样本内赚了。胜率 {wr:.0f}%，止损 {n_stop} 笔。"
    bits = []
    if wins and losses and abs(mean(losses)) > mean(wins):
        bits.append(
            f"胜率 {wr:.0f}% 仍亏，因为平均亏损 ${abs(mean(losses)):,.0f} 大于平均盈利 ${mean(wins):,.0f}。"
        )
    else:
        bits.append(f"胜率 {wr:.0f}%，合计亏损 ${abs(total):,.0f}。")
    if n_stop:
        bits.append(f"{n_stop} 笔止损合计 ${stop_pnl:,.0f}，把回归单的利润吃掉了。")
    if short_stops:
        bits.append(
            f"空价差止损 {len(short_stops)} 笔：规则把 {leg_a} 当成相对太贵会回来，结果它继续强。"
        )
    if quality.get("key") in {"trend", "weak", "no_coint"}:
        bits.append(f"{quality.get('label')}。换更同业或「股票 vs 行业ETF」的一对，比继续调 z 更有用。")
    return "".join(bits)


def _macro_block() -> dict | None:
    try:
        from app.services.macro import _read_cache

        data = _read_cache("fed_risk_v6") or _read_cache("fed_risk_v5") or {}
    except Exception:
        return None
    alert = data.get("jpy_alert") or {}
    risk = data.get("macro_risk")
    if alert.get("key") == "unwind":
        return {"key": "jpy_unwind", "label": "JPY Carry Unwind", "detail": "宏观过滤：日元平仓通道打开，暂停新开仓。"}
    if risk is not None and float(risk) >= 75:
        return {"key": "macro_hot", "label": "Macro Risk 极高", "detail": f"宏观过滤：Macro Risk {float(risk):.0f}，暂停新开仓。"}
    return None


def _run_residual(row: ArbStrategy) -> dict:
    from app.services.intraday_scan import IntradayScanError, backtest_residual

    factors = _hedges(row)
    tf_label = TF_LABEL.get(row.timeframe, row.timeframe)
    try:
        bt = backtest_residual(
            target=row.leg_a,
            factors=factors,
            timeframe=row.timeframe if row.timeframe in {"5m", "30m", "4h", "1d"} else "5m",
            entry_z=float(row.entry_z or 2.5),
            exit_z=float(row.exit_z or 0.75),
            stop_z=float(row.stop_z or 4.0),
            notional=float(row.notional or 10_000),
            lookback=int(row.lookback or 0) or None,
        )
    except IntradayScanError as exc:
        raise ArbError(str(exc)) from exc

    z_now = bt.get("z")
    signal = bt.get("signal") or "flat"
    hedge = bt.get("hedge") or "+".join(factors)
    cash = float(bt.get("notional") or 10_000)
    price = bt.get("price")
    if signal == "long_spread":
        action = f"做多残差：买 {row.leg_a}、对冲 {hedge}。现价 {price}，z={z_now}。"
    elif signal == "short_spread":
        action = f"做空残差：卖 {row.leg_a}、对冲 {hedge}。现价 {price}，z={z_now}。"
    else:
        action = (
            f"空仓。{row.leg_a} vs {hedge}，当前 z={z_now}。"
            f"{tf_label} 残差 |Z|≥{bt.get('entry_z')} 才开。"
        )

    recent = []
    open_mtm = None
    for t in bt.get("recent") or []:
        packed = {
            "side": "long_spread" if t.get("side") == "long_resid" else "short_spread",
            "entry_date": t.get("entry"),
            "exit_date": t.get("exit"),
            "entry_z": t.get("entry_z"),
            "exit_z": t.get("exit_z"),
            "a_entry": t.get("px_in"),
            "a_exit": t.get("px_out"),
            "b_entry": None,
            "b_exit": None,
            "a_notional": cash,
            "b_notional": 0,
            "a_shares": None,
            "b_shares": None,
            "pnl": t.get("pnl"),
            "why": t.get("why"),
        }
        if t.get("why") == "open":
            open_mtm = packed
        else:
            recent.append(packed)

    closed = [t for t in (bt.get("recent") or []) if t.get("why") != "open"]
    wins = [t.get("pnl") or 0 for t in closed if (t.get("pnl") or 0) > 0]
    losses = [t.get("pnl") or 0 for t in closed if (t.get("pnl") or 0) < 0]
    net = bt.get("total_pnl_net") or 0
    if net > 20:
        pnl_call, pnl_emoji = "赚了", "🟢"
    elif net < -20:
        pnl_call, pnl_emoji = "亏了", "🔴"
    else:
        pnl_call, pnl_emoji = "基本打平", "🟡"

    window = {
        "start": bt.get("start"),
        "end": bt.get("as_of"),
        "bars": bt.get("bars"),
        "trade_bars": bt.get("trade_bars"),
        "days": bt.get("days"),
        "bt_days": int(row.bt_days or 0),
        "label": f"{tf_label} · {bt.get('start')} → {bt.get('as_of')}（约 {bt.get('days')} 个交易日，不是 D 线）",
        "warmup": bt.get("lookback"),
        "timeframe": bt.get("timeframe"),
        "tf_label": tf_label,
    }
    beta_map = bt.get("betas") or {}
    beta_txt = "  ".join(f"{k} {v}" for k, v in beta_map.items()) or "—"
    return {
        "ok": True,
        "mode": "residual",
        "timeframe": bt.get("timeframe") or row.timeframe,
        "timeframe_label": tf_label,
        "hedge": hedge,
        "factors": factors,
        "betas": beta_map,
        "strategy": to_out(row),
        "signal": signal,
        "action": action,
        "blocked": False,
        "wait": None,
        "quality": {
            "key": "intraday",
            "tone": "yellow",
            "label": f"{tf_label} 多因子残差",
            "detail": f"{row.leg_a} = α + Σ β·因子 + ε。因子 {hedge}。",
            "hint": "这不是日线协整对。赚的是几分钟到几十分钟的短期偏离。",
        },
        "macro_gate": None,
        "z": z_now,
        "beta": beta_txt,
        "spread": None,
        "corr": None,
        "notional": cash,
        "capital": {
            "notional_a": cash,
            "notional_b": 0,
            "gross": cash,
            "shares_a": None,
            "shares_b": None,
            "price_a": price,
            "price_b": None,
            "note": f"{row.leg_a} 投入 ${cash:,.0f}，对冲 {hedge}。用的是 {tf_label} K 线。",
        },
        "window": window,
        "lab": {
            "formula": f"ε = R_{row.leg_a} − α − Σ β R_factor",
            "beta_returns": None,
            "beta_used": beta_txt,
            "corr_all": None,
            "corr_window": None,
            "adf": None,
            "half_life": None,
            "edge_ann": None,
            "edge_note": f"滚动窗口 {bt.get('lookback')} 根 {tf_label} 估 β，不是 D 线 ADF。",
            "pseudo_hedge": False,
            "pseudo_note": f"当前 β：{beta_txt}",
            "goal": "日内残差均值回归。大盘急冲时不开。",
        },
        "leg_a": {
            "symbol": row.leg_a,
            "price": price,
            "source": "tradingview",
            "side": "long" if signal == "long_spread" else "short" if signal == "short_spread" else "flat",
            "buy_below": None,
            "sell_above": None,
            "mean": None,
        },
        "leg_b": {
            "symbol": hedge,
            "price": None,
            "source": "tradingview",
            "side": "short" if signal == "long_spread" else "long" if signal == "short_spread" else "flat",
            "buy_below": None,
            "sell_above": None,
            "mean": None,
        },
        "levels": {
            "note": f"{tf_label} 残差没有两腿买卖价。看 z：|Z|≥{bt.get('entry_z')} 开、≤{bt.get('exit_z')} 平。",
        },
        "as_of": bt.get("as_of"),
        "bars": bt.get("bars"),
        "lookback": bt.get("lookback"),
        "thresholds": {
            "entry": bt.get("entry_z"),
            "exit": bt.get("exit_z"),
            "stop": bt.get("stop_z"),
        },
        "backtest": {
            "in_sample": True,
            "note": bt.get("note"),
            "notional": cash,
            "cost_bps": bt.get("cost_bps"),
            "cost_drag": bt.get("cost_drag"),
            "total_pnl_net": bt.get("total_pnl_net"),
            "roi_pct": bt.get("roi_pct"),
            "roi_net_pct": bt.get("roi_pct"),
            "mdd": bt.get("mdd"),
            "mdd_pct": bt.get("mdd_pct"),
            "cvar_5": None,
            "holdout": bt.get("holdout"),
            "call": pnl_call,
            "emoji": pnl_emoji,
            "total_pnl": bt.get("total_pnl"),
            "open_pnl": open_mtm.get("pnl") if open_mtm else 0,
            "total_with_open": _round((bt.get("total_pnl") or 0) + ((open_mtm or {}).get("pnl") or 0), 2),
            "trades": bt.get("trades"),
            "wins": bt.get("wins"),
            "losses": bt.get("losses"),
            "win_rate": bt.get("win_rate"),
            "avg_pnl": bt.get("avg_pnl"),
            "avg_win": _round(mean(wins), 2) if wins else None,
            "avg_loss": _round(mean(losses), 2) if losses else None,
            "avg_bars": None,
            "sharpe": None,
            "why": f"{tf_label} 残差回测，不是 D 线两腿 OLS。",
            "open_position": open_mtm is not None,
            "open_trade": open_mtm,
            "recent": recent[-16:],
        },
        "series": {"dates": [], "z": []},
        "hypothesis": (
            f"赚的是 {row.leg_a} 相对 {hedge} 的 {tf_label} 残差，不是日线协整。"
        ),
    }


def run_arb(row: ArbStrategy) -> dict:
    if _use_residual(row):
        return _run_residual(row)
    try:
        oa = fetch_closes(row.leg_a, row.timeframe)
        ob = fetch_closes(row.leg_b, row.timeframe)
    except OhlcError as exc:
        raise ArbError(str(exc)) from exc

    px_a, px_b, dates = _align(oa.get("ohlc_bars") or [], ob.get("ohlc_bars") or [], row.timeframe)
    lb = int(row.lookback or 60)
    bt_days = int(getattr(row, "bt_days", 0) or 0)
    px_a, px_b, dates, cut = _slice_bt(px_a, px_b, dates, row.timeframe, bt_days, lb)
    if len(px_a) < lb + 10:
        raise ArbError(
            f"对齐后的 {TF_LABEL.get(row.timeframe, row.timeframe)} K 线不够。"
            "换一对流动性更好的标的，或把周期改成 5min / 30min / D 线再试。"
        )

    log_a = _logs(px_a)
    log_b = _logs(px_b)
    kind = row.kind or "ols"
    fixed_beta = float(row.beta) if row.beta is not None and kind != "ols" else None
    a0, b0 = px_a[0], px_b[0]

    zs: list[float | None] = [None] * len(px_a)
    betas: list[float | None] = [None] * len(px_a)
    spreads: list[float | None] = [None] * len(px_a)
    mus: list[float | None] = [None] * len(px_a)
    sds: list[float | None] = [None] * len(px_a)

    for i in range(lb, len(px_a)):
        w_a = log_a[i - lb : i]
        w_b = log_b[i - lb : i]
        if kind == "ols":
            beta = _ols_beta(w_a, w_b)
        elif kind == "ratio":
            beta = 1.0
        else:
            beta = fixed_beta if fixed_beta is not None else 1.0
        window_s = [
            _spread_at(kind, px_a[j], px_b[j], beta, a0, b0)
            for j in range(i - lb, i)
        ]
        s = _spread_at(kind, px_a[i], px_b[i], beta, a0, b0)
        mu = mean(window_s)
        sd = pstdev(window_s) if len(window_s) > 1 else 0.0
        z = 0.0 if sd < 1e-12 else (s - mu) / sd
        zs[i] = z
        betas[i] = beta
        spreads[i] = s
        mus[i] = mu
        sds[i] = sd

    entry = float(row.entry_z)
    exit_z = float(row.exit_z)
    stop = float(row.stop_z)
    notional = float(getattr(row, "notional", None) or 10_000.0)
    notional = max(100.0, min(10_000_000.0, notional))

    def _pack_trade(*, long_spread: bool, i0: int, i1: int, why: str, closed: bool) -> dict:
        beta_i = betas[i0] or 1.0
        pnl = _dollar_pnl(long_spread, px_a[i0], px_a[i1], px_b[i0], px_b[i1], beta_i, notional)
        hedge = notional * abs(float(beta_i))
        a_in, b_in = px_a[i0], px_b[i0]
        return {
            "side": "long_spread" if long_spread else "short_spread",
            "entry_date": _bar_stamp(dates[i0], row.timeframe),
            "exit_date": _bar_stamp(dates[i1], row.timeframe) if closed else None,
            "entry_z": _round(zs[i0], 3),
            "exit_z": _round(zs[i1], 3),
            "bars": i1 - i0,
            "why": why,
            "closed": closed,
            "beta": _round(beta_i, 4),
            "a_entry": _round(a_in, 4),
            "a_exit": _round(px_a[i1], 4),
            "b_entry": _round(b_in, 4),
            "b_exit": _round(px_b[i1], 4),
            "a_notional": _round(notional, 2),
            "b_notional": _round(hedge, 2),
            "a_shares": _round(notional / a_in, 4) if a_in else None,
            "b_shares": _round(hedge / b_in, 4) if b_in else None,
            "pnl": _round(pnl, 2),
            "pnl_pct": _round(pnl / notional * 100, 2),
        }

    pos = 0
    trades: list[dict] = []
    open_i = None
    open_beta = 1.0
    dollar_bars: list[float] = []
    # 止损后价差往往还在开仓带外；必须先回到平仓带，再等下一次穿越，避免趋势里反复开仓。
    wait_mean = False
    for i in range(1, len(px_a)):
        if pos != 0:
            dollar_bars.append(
                _dollar_pnl(pos > 0, px_a[i - 1], px_a[i], px_b[i - 1], px_b[i], open_beta, notional)
            )
        else:
            dollar_bars.append(0.0)
        z = zs[i]
        prev_z = zs[i - 1]
        if z is None:
            continue
        if wait_mean:
            if abs(z) <= exit_z:
                wait_mean = False
            else:
                continue
        if pos == 0:
            if prev_z is None:
                continue
            crossed_long = z <= -entry < prev_z
            crossed_short = z >= entry > prev_z
            if crossed_long:
                pos = 1
                open_i = i
                open_beta = betas[i] or 1.0
            elif crossed_short:
                pos = -1
                open_i = i
                open_beta = betas[i] or 1.0
        else:
            if abs(z) <= exit_z or abs(z) >= stop:
                why = "stop" if abs(z) >= stop else "exit"
                trades.append(_pack_trade(long_spread=pos > 0, i0=open_i, i1=i, why=why, closed=True))
                if why == "stop":
                    wait_mean = True
                pos = 0
                open_i = None

    open_mtm = None
    if pos != 0 and open_i is not None:
        open_mtm = _pack_trade(long_spread=pos > 0, i0=open_i, i1=len(px_a) - 1, why="open", closed=False)

    cost_bps = 10.0
    for t in trades:
        gross_n = (t.get("a_notional") or 0) + (t.get("b_notional") or 0)
        t["cost"] = _round(2 * gross_n * cost_bps / 10000.0, 2)
        t["pnl_net"] = _round((t.get("pnl") or 0) - (t.get("cost") or 0), 2)

    closed_pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
    total_closed = sum(closed_pnls) if closed_pnls else 0.0
    total_with_mtm = total_closed + (open_mtm["pnl"] if open_mtm else 0.0)
    wins = [p for p in closed_pnls if p > 0]
    losses = [p for p in closed_pnls if p < 0]
    sharpe = None
    rets = [x / notional for x in dollar_bars]
    if len(rets) > 20 and pstdev(rets) > 1e-12:
        sharpe = (mean(rets) / pstdev(rets)) * math.sqrt(BARS_PER_YEAR.get(row.timeframe, 252))
    if total_with_mtm > 20:
        pnl_call = "赚了"
        pnl_emoji = "🟢"
    elif total_with_mtm < -20:
        pnl_call = "亏了"
        pnl_emoji = "🔴"
    else:
        pnl_call = "基本打平"
        pnl_emoji = "🟡"

    z_now = zs[-1]
    beta_now = betas[-1] or 1.0
    spread_now = spreads[-1]
    mu_now = mus[-1]
    sd_now = sds[-1]
    signal = "flat"
    wait = None
    if pos > 0:
        signal = "long_spread"
    elif pos < 0:
        signal = "short_spread"
    elif wait_mean:
        wait = {
            "key": "after_stop",
            "label": "止损后等待回均值",
            "detail": f"z={_round(z_now, 3)} 还在平仓带外。先回到 ±{exit_z:g} 以内，再等下一次穿越开仓线，不追。",
        }
    elif z_now is not None and abs(z_now) >= entry:
        wait = {
            "key": "no_cross",
            "label": "等回抽后再穿越",
            "detail": f"z={_round(z_now, 3)} 已在开仓带外，但当前不是穿越。不在这个位置新开仓。",
        }

    gate = _macro_block() if row.macro_filter else None
    blocked = False
    if gate and signal in {"long_spread", "short_spread"} and pos == 0:
        blocked = True
        signal = "blocked"

    pa, pb = px_a[-1], px_b[-1]
    rets_a = _rets(px_a)[-lb:]
    rets_b = _rets(px_b)[-lb:]
    rho = _corr(rets_a, rets_b)
    quality = _pair_quality(row.leg_a, row.leg_b, px_a, px_b, rho)
    start_day = _as_day(dates[min(lb, len(dates) - 1)], row.timeframe)
    end_day = _as_day(dates[-1], row.timeframe)
    tf_label = TF_LABEL.get(row.timeframe, row.timeframe)
    start_lbl = _bar_stamp(dates[min(lb, len(dates) - 1)], row.timeframe)
    end_lbl = _bar_stamp(dates[-1], row.timeframe)
    hedge_now = notional * abs(float(beta_now))
    shares_a = notional / pa if pa else None
    shares_b = hedge_now / pb if pb else None
    cal_days = None
    try:
        cal_days = (date.fromisoformat(end_day) - date.fromisoformat(start_day)).days
    except ValueError:
        pass
    window = {
        "start": start_lbl,
        "end": end_lbl,
        "bars": len(px_a),
        "trade_bars": max(0, len(px_a) - lb),
        "days": cal_days,
        "bt_days": bt_days,
        "label": f"{tf_label} · {_window_label(start_day, end_day)}",
        "warmup": lb,
        "timeframe": row.timeframe,
        "tf_label": tf_label,
    }
    capital = {
        "notional_a": _round(notional, 2),
        "notional_b": _round(hedge_now, 2),
        "gross": _round(notional + hedge_now, 2),
        "shares_a": _round(shares_a, 4),
        "shares_b": _round(shares_b, 4),
        "price_a": _round(pa, 4),
        "price_b": _round(pb, 4),
        "note": (
            f"按现价 {row.leg_a}={_round(pa, 2)}、{row.leg_b}={_round(pb, 2)}："
            f"{row.leg_a} 投入 ${notional:,.0f} 约 {shares_a:.2f} 股，"
            f"{row.leg_b} 对冲 ${hedge_now:,.0f} 约 {shares_b:.2f} 股（|β|={abs(float(beta_now)):.3f}）。"
        ),
    }

    ra_all = _rets(px_a)[1:]
    rb_all = _rets(px_b)[1:]
    beta_r = _ols_beta(ra_all, rb_all) if ra_all else 1.0
    pair_r = [a - beta_r * b for a, b in zip(ra_all, rb_all)]
    edge_ann = mean(pair_r) * 252 if pair_r else 0.0
    pseudo = abs(abs(beta_r) - 1.0) > 0.3
    lab = {
        "formula": f"R_pair = R_{row.leg_a} − β R_{row.leg_b}",
        "beta_returns": _round(beta_r, 3),
        "beta_used": _round(beta_now, 3),
        "corr_all": quality.get("corr_all"),
        "corr_window": quality.get("corr_window"),
        "adf": quality.get("adf"),
        "half_life": quality.get("half_life"),
        "edge_ann": _round(edge_ann * 100, 2),
        "edge_note": (
            "这是 R_A−βR_B 的无条件年化均值，不是交易策略收益。"
            "偏大说明残差有漂移，对均值回归是风险。"
            "真正赚钱要回归边 > 手续费+借券+滑点。"
        ),
        "pseudo_hedge": pseudo,
        "pseudo_note": (
            f"收益 β={_round(beta_r, 2)}。若按 1 美元 {row.leg_a} 对 1 美元 {row.leg_b}，并不是市场中性。"
            f"本页按 |β|×{row.leg_a}名义 配 {row.leg_b}。"
            if pseudo
            else f"收益 β={_round(beta_r, 2)}，接近 1 时等名义对冲还算接近中性。本页仍按 β 配仓。"
        ),
        "goal": "不要找稳赚的一对。要找统计检验过、成本后仍有正期望、样本外还站得住的相对价值。年化 8%～15%、回撤低、夏普稳，已经很有价值。",
    }

    split_day = _as_day(dates[int(len(dates) * 0.7)], row.timeframe) if dates else None
    is_tr = [t for t in trades if split_day and t.get("entry_date") < split_day]
    oos_tr = [t for t in trades if split_day and t.get("entry_date") >= split_day]

    def _leg_stats(ts):
        pnls = [t.get("pnl") or 0 for t in ts]
        return {
            "trades": len(ts),
            "pnl": _round(sum(pnls), 2) if pnls else 0.0,
            "win_rate": _round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1) if pnls else None,
        }

    holdout = {
        "split": split_day,
        "in_sample": _leg_stats(is_tr),
        "out_of_sample": _leg_stats(oos_tr),
        "note": f"按开仓日切：{split_day} 之前当样本内，之后当样本外。滚动 z 本身不偷看未来，但这仍不是参数的 walk-forward。",
    }
    mdd = _max_dd(dollar_bars)
    daily_rets = [x / notional for x in dollar_bars]
    cvar = _cvar(daily_rets)
    total_cost = sum(t.get("cost") or 0 for t in trades)
    total_net = total_closed - total_cost

    def _lvl(z_t):
        if mu_now is None or sd_now is None:
            return None
        s_t = mu_now + float(z_t) * float(sd_now)
        return {
            "z": z_t,
            "a": _round(_implied_a(kind, s_t, pb, beta_now, a0, b0), 4),
            "b": _round(_implied_b(kind, s_t, pa, beta_now, a0, b0), 4),
        }

    levels = {
        "long_entry": _lvl(-entry),
        "short_entry": _lvl(entry),
        "mean": _lvl(0),
        "long_exit": _lvl(-exit_z),
        "short_exit": _lvl(exit_z),
        "note": (
            f"在当前 {row.leg_b}={_round(pb, 4)} 下反解 {row.leg_a}；"
            f"在当前 {row.leg_a}={_round(pa, 4)} 下反解 {row.leg_b}。"
            "做多价差：A 相对便宜，买 A、卖 B；做空价差相反。"
        ),
    }

    if signal == "long_spread":
        buy_a = (levels["long_entry"] or {}).get("a")
        sell_b = (levels["long_entry"] or {}).get("b")
        action = (
            f"做多价差：买 {row.leg_a} / 卖 {row.leg_b}（β={_round(beta_now, 3)}）。"
            f"{row.leg_a} 现价 {_round(pa, 4)}，买点 ≤ {buy_a}；"
            f"{row.leg_b} 现价 {_round(pb, 4)}，卖点 ≥ {sell_b}。"
        )
        a_side, b_side = "long", "short"
    elif signal == "short_spread":
        sell_a = (levels["short_entry"] or {}).get("a")
        buy_b = (levels["short_entry"] or {}).get("b")
        action = (
            f"做空价差：卖 {row.leg_a} / 买 {row.leg_b}（β={_round(beta_now, 3)}）。"
            f"{row.leg_a} 现价 {_round(pa, 4)}，卖点 ≥ {sell_a}；"
            f"{row.leg_b} 现价 {_round(pb, 4)}，买点 ≤ {buy_b}。"
        )
        a_side, b_side = "short", "long"
    elif signal == "blocked":
        action = gate["detail"]
        a_side, b_side = "flat", "flat"
    elif wait:
        action = wait["detail"]
        a_side, b_side = "flat", "flat"
    else:
        buy_a = (levels["long_entry"] or {}).get("a")
        sell_a = (levels["short_entry"] or {}).get("a")
        action = (
            f"空仓等待穿越。{row.leg_a} 从上方跌破 ≤ {buy_a} 才做多价差（买{row.leg_a}/卖{row.leg_b}）；"
            f"从下方涨破 ≥ {sell_a} 才做空价差（卖{row.leg_a}/买{row.leg_b}）。"
            f"回归中枢约 {(levels['mean'] or {}).get('a')}。止损后要先回到 ±{exit_z:g}。"
        )
        a_side, b_side = "flat", "flat"

    return {
        "ok": True,
        "mode": "pair",
        "timeframe": row.timeframe,
        "timeframe_label": TF_LABEL.get(row.timeframe, row.timeframe),
        "strategy": to_out(row),
        "signal": signal,
        "action": action,
        "blocked": blocked,
        "wait": wait,
        "quality": quality,
        "macro_gate": gate,
        "z": _round(z_now, 3),
        "beta": _round(beta_now, 4),
        "spread": _round(spread_now, 5),
        "corr": _round(rho, 3),
        "notional": notional,
        "capital": capital,
        "window": window,
        "lab": lab,
        "leg_a": {
            "symbol": row.leg_a,
            "price": _round(pa, 4),
            "source": oa.get("source"),
            "side": a_side,
            "buy_below": (levels["long_entry"] or {}).get("a"),
            "sell_above": (levels["short_entry"] or {}).get("a"),
            "mean": (levels["mean"] or {}).get("a"),
        },
        "leg_b": {
            "symbol": row.leg_b,
            "price": _round(pb, 4),
            "source": ob.get("source"),
            "side": b_side,
            "buy_below": (levels["short_entry"] or {}).get("b"),
            "sell_above": (levels["long_entry"] or {}).get("b"),
            "mean": (levels["mean"] or {}).get("b"),
        },
        "levels": levels,
        "as_of": end_day,
        "bars": len(px_a),
        "lookback": lb,
        "thresholds": {"entry": entry, "exit": exit_z, "stop": stop},
        "backtest": {
            "in_sample": True,
            "note": (
                f"回测 {window['label']}，共 {window['trade_bars']} 根 {TF_LABEL.get(row.timeframe, row.timeframe)} 可交易K线（另有 {lb} 根热身）。"
                f"毛利按 {row.leg_a} 投入 ${notional:,.0f}、{row.leg_b} 对冲 β×投入。"
                f"成本按每边每趟 {cost_bps:g}bp 估算（未含借券）。"
                "开仓只认 z 穿越开仓线；止损后必须先回到平仓带。"
            ),
            "notional": notional,
            "cost_bps": cost_bps,
            "cost_drag": _round(total_cost, 2),
            "total_pnl_net": _round(total_net, 2),
            "roi_pct": _round(total_closed / notional * 100, 2) if notional else None,
            "roi_net_pct": _round(total_net / notional * 100, 2) if notional else None,
            "mdd": _round(mdd, 2),
            "mdd_pct": _round(mdd / notional * 100, 2) if notional else None,
            "cvar_5": _round(cvar * 100, 2) if cvar is not None else None,
            "holdout": holdout,
            "call": pnl_call,
            "emoji": pnl_emoji,
            "total_pnl": _round(total_closed, 2),
            "open_pnl": _round(open_mtm["pnl"] if open_mtm else 0, 2),
            "total_with_open": _round(total_with_mtm, 2),
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": _round(len(wins) / len(trades) * 100, 1) if trades else None,
            "avg_pnl": _round(mean(closed_pnls), 2) if closed_pnls else None,
            "avg_win": _round(mean(wins), 2) if wins else None,
            "avg_loss": _round(mean(losses), 2) if losses else None,
            "avg_bars": _round(mean([t["bars"] for t in trades]), 1) if trades else None,
            "sharpe": _round(sharpe, 2),
            "why": _why_pnl(row.leg_a, trades, total_closed, wins, losses, quality),
            "open_position": pos != 0,
            "open_trade": open_mtm,
            "recent": trades[-16:],
        },
        "series": {
            "dates": dates[-120:],
            "z": [_round(z, 3) for z in zs[-120:]],
        },
        "hypothesis": (
            f"赚的是相对价值 R_{row.leg_a}−βR_{row.leg_b}，不是单边涨跌。"
            "相关 ≠ 协整 ≠ 可套利。关系会变。"
        ),
    }
