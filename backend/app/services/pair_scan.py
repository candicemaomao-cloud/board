"""5 层选对：经济关系 → 相关 → OLS β → ADF/半衰期 → 滚动β稳定 → 当前 z / 事件。"""

from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from time import sleep, time

from app.services.arb_strategy import _adf, _align, _corr, _logs, _ols_beta, _rets, _round
from app.services.ohlc import OhlcError, fetch_closes
from app.services.pair_universe import all_symbols, iter_candidates, list_groups

CACHE = Path("data/pairs_scan.json")
BARS_CACHE = Path("data/pairs_bars.json")
TTL = 12 * 3600
LOOKBACK = 60
HL_LO, HL_HI = 5.0, 60.0
MIN_CORR = 0.55
EARNINGS_DAYS = 7

_job_lock = threading.Lock()
_job: dict = {
    "running": False,
    "stage": "",
    "done": 0,
    "total": 0,
    "symbol": "",
    "error": None,
    "last_result": None,
}


class PairScanError(Exception):
    pass


def _set_job(**kwargs) -> None:
    with _job_lock:
        _job.update(kwargs)


def job_status() -> dict:
    with _job_lock:
        out = dict(_job)
    peek = peek_scan()
    result = out.pop("last_result", None) or (peek if peek.get("pairs") else None)
    if result and result.get("pairs"):
        out["as_of"] = result.get("as_of")
        out["n_pairs"] = result.get("n_pairs")
        if not out.get("running"):
            out["result"] = result
    return out


def _bars_disk() -> dict:
    if not BARS_CACHE.exists():
        return {"ts": 0, "bars": {}}
    try:
        payload = json.loads(BARS_CACHE.read_text())
    except Exception:
        return {"ts": 0, "bars": {}}
    if time() - float(payload.get("ts") or 0) > TTL:
        return {"ts": 0, "bars": {}}
    return payload if isinstance(payload.get("bars"), dict) else {"ts": 0, "bars": {}}


def _bars_get(symbol: str) -> list[dict] | None:
    hit = (_bars_disk().get("bars") or {}).get(symbol)
    if isinstance(hit, list) and len(hit) >= LOOKBACK + 20:
        return hit
    return None


def _bars_put(symbol: str, bars: list[dict]) -> None:
    payload = _bars_disk()
    compact = [{"ts": b.get("ts"), "close": b.get("close")} for b in bars if b.get("close") is not None]
    payload.setdefault("bars", {})[symbol] = compact
    payload["ts"] = payload.get("ts") or time()
    BARS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    BARS_CACHE.write_text(json.dumps(payload, ensure_ascii=False))


def _closes(symbol: str) -> list[dict]:
    """和套利页同一条行情通道：TradingView，不行再 Yahoo。命中本地缓存就不重复拉。"""
    cached = _bars_get(symbol)
    if cached:
        return cached
    packed = fetch_closes(symbol, "1d", apply_live=False)
    bars = packed.get("ohlc_bars") or []
    if len(bars) < LOOKBACK + 20:
        raise OhlcError(f"拉不到 {symbol} 的日线")
    _bars_put(symbol, bars)
    return bars


def _closes_timed(symbol: str, timeout: float = 20.0) -> list[dict]:
    box: dict = {}

    def work():
        try:
            box["v"] = _closes(symbol)
        except Exception as exc:
            box["e"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise OhlcError(f"{symbol} 拉行情超时")
    if "e" in box:
        raise box["e"]
    return box["v"]


def _half_life(resid: list[float]) -> float | None:
    if len(resid) < 30:
        return None
    x, y = resid[:-1], resid[1:]
    mx, my = mean(x), mean(y)
    varx = sum((xi - mx) ** 2 for xi in x)
    if varx < 1e-18:
        return None
    phi = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / varx
    if not (0 < phi < 1):
        return None
    return math.log(0.5) / math.log(phi)


def _rolling_betas(log_a: list[float], log_b: list[float], window: int = LOOKBACK, step: int = 5) -> list[float]:
    out = []
    for i in range(window, len(log_a) + 1, step):
        out.append(_ols_beta(log_a[i - window : i], log_b[i - window : i]))
    return out


def _z_now(log_a: list[float], log_b: list[float], window: int = LOOKBACK) -> tuple[float | None, float | None]:
    if len(log_a) < window + 2:
        return None, None
    beta = _ols_beta(log_a[-window:], log_b[-window:])
    spread = [ya - beta * yb for ya, yb in zip(log_a[-window:], log_b[-window:])]
    mu = mean(spread)
    sd = pstdev(spread) if len(spread) > 1 else 0.0
    if sd < 1e-12:
        return _round(beta, 3), 0.0
    return _round(beta, 3), _round((spread[-1] - mu) / sd, 3)


def _earnings_gap(symbol: str) -> tuple[str | None, int | None]:
    try:
        from app.services.macro_event_study import _earnings_dates

        dates, _ = _earnings_dates(symbol)
    except Exception:
        return None, None
    today = date.today()
    future = sorted(d for d in dates if 0 <= (d - today).days <= 45)
    if not future:
        return None, None
    nxt = future[0]
    return nxt.isoformat(), (nxt - today).days


def _score_pair(meta: dict, bars_a: list[dict], bars_b: list[dict]) -> dict:
    px_a, px_b, _dates = _align(bars_a, bars_b, "1d")
    n = len(px_a)
    fail = {
        **meta,
        "ok": False,
        "n": n,
        "corr": None,
        "beta": None,
        "adf_pass": False,
        "adf_t": None,
        "half_life": None,
        "beta_cv": None,
        "beta_stable": "低",
        "z": None,
        "status": "red",
        "status_label": "数据不够",
        "score": 0,
        "why": "对齐后K线不够",
        "earnings": None,
    }
    if n < LOOKBACK + 20:
        return fail

    ra, rb = _rets(px_a)[1:], _rets(px_b)[1:]
    rho = _corr(ra, rb)
    log_a, log_b = _logs(px_a), _logs(px_b)
    beta_full = _ols_beta(log_a, log_b)
    resid = [ya - beta_full * yb for ya, yb in zip(log_a, log_b)]
    adf = _adf(resid)
    hl = _half_life(resid[-252:] if len(resid) > 252 else resid)
    betas = _rolling_betas(log_a[-300:] if len(log_a) > 300 else log_a, log_b[-300:] if len(log_b) > 300 else log_b)
    beta_cv = None
    if len(betas) >= 8 and abs(mean(betas)) > 1e-8:
        beta_cv = pstdev(betas) / abs(mean(betas))
    beta_now, z = _z_now(log_a, log_b)
    adf_pass = bool(adf and adf.get("pass"))
    hl_ok = hl is not None and HL_LO <= hl <= HL_HI
    corr_ok = rho is not None and abs(rho) >= MIN_CORR
    if beta_cv is None:
        beta_grade = "低"
    elif beta_cv <= 0.25:
        beta_grade = "高"
    elif beta_cv <= 0.45:
        beta_grade = "中"
    else:
        beta_grade = "低"

    why_bits = []
    score = 0.0
    if meta.get("counter"):
        why_bits.append("经济关系太松，只当反例。")
    if corr_ok:
        score += 15 * min((abs(rho) - 0.5) / 0.4, 1.0)
    else:
        why_bits.append(f"相关 {None if rho is None else round(rho, 2)} < {MIN_CORR}。")
    if adf_pass:
        score += 40
    else:
        why_bits.append("残差不平稳，相关≠协整。")
    if hl_ok:
        score += 20
    elif hl is None:
        why_bits.append("半衰期算不出（残差不回归）。")
    else:
        why_bits.append(f"半衰期 {hl:.0f} 天，不在 {HL_LO:.0f}～{HL_HI:.0f}。")
        if hl > HL_HI:
            score += 5
    if beta_grade == "高":
        score += 15
    elif beta_grade == "中":
        score += 8
        why_bits.append("滚动 β 一般稳。")
    else:
        why_bits.append("滚动 β 不稳定，关系可能已变。")
    if z is not None:
        score += 10 * min(abs(z) / 2.5, 1.0)

    return {
        **meta,
        "ok": True,
        "n": n,
        "corr": _round(rho, 3),
        "beta": beta_now if beta_now is not None else _round(beta_full, 3),
        "adf_pass": adf_pass,
        "adf_t": (adf or {}).get("t"),
        "half_life": _round(hl, 1),
        "beta_cv": _round(beta_cv, 3),
        "beta_stable": beta_grade,
        "z": z,
        "status": "red",
        "status_label": "待评级",
        "score": _round(score, 1),
        "why_bits": why_bits,
        "earnings": None,
        "hl_ok": hl_ok,
        "corr_ok": corr_ok,
        "_core_ok": adf_pass and hl_ok and beta_grade in {"高", "中"} and not meta.get("counter"),
    }


def _apply_status(row: dict) -> dict:
    why_bits = list(row.pop("why_bits", []) or [])
    earn_soon = row.get("earnings")
    core_ok = row.pop("_core_ok", False)
    score = row.get("score") or 0
    z = row.get("z")
    hl = row.get("half_life")
    if earn_soon:
        score -= 25
        why_bits.append(
            f"{earn_soon['symbol']} {earn_soon['days']} 天内有财报，极端 z 可能是新信息。"
        )
    if row.get("counter") or not row.get("adf_pass") or row.get("beta_stable") == "低" or (hl is not None and hl > 120):
        status, label = "red", "不适合"
    elif earn_soon:
        status, label = "yellow", "事件风险"
    elif core_ok and z is not None and abs(z) >= 2:
        status, label = "green", "值得研究"
    elif core_ok:
        status, label = "yellow", "关系尚可，当前偏离不够"
    else:
        status, label = "red", "不适合"
    if not why_bits:
        if status == "green":
            why_bits.append("协整、半衰期、β 都过关，且 z 已偏离。")
        else:
            why_bits.append("统计关系还行，现在没有入场偏离。")
    row["score"] = _round(score, 1)
    row["status"] = status
    row["status_label"] = label
    row["why"] = "".join(why_bits)
    return row


def _load_bars(symbols: list[str]) -> dict[str, list[dict]]:
    bars: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    total = len(symbols)
    for i, sym in enumerate(symbols):
        _set_job(done=i, total=total, symbol=sym, stage=f"拉行情 {sym}（{i + 1}/{total}）")
        try:
            bars[sym] = _closes_timed(sym)
        except Exception as exc:
            bars[sym] = []
            errors[sym] = str(exc)
            msg = str(exc)
            if "429" in msg or "限流" in msg:
                sleep(1.5)
    _set_job(done=total, total=total, stage="计算协整")
    return bars, errors


def _read_cache() -> dict | None:
    if not CACHE.exists():
        return None
    try:
        payload = json.loads(CACHE.read_text())
    except Exception:
        return None
    ts = payload.get("ts")
    if not ts or time() - float(ts) > TTL:
        return None
    pairs = payload.get("pairs") or []
    if pairs and not any(r.get("ok") for r in pairs):
        return None
    return payload


def _write_cache(payload: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def scan_pairs(*, force: bool = False, groups: list[str] | None = None) -> dict:
    if not force:
        hit = _read_cache()
        if hit and not groups:
            return hit

    cands = list(iter_candidates(groups=groups))
    if not cands:
        raise PairScanError("没有候选对")
    symbols = all_symbols(cands)
    bars, errors = _load_bars(symbols)
    rows = []
    for meta in cands:
        a, b = meta["leg_a"], meta["leg_b"]
        if not bars.get(a) or not bars.get(b):
            rows.append(
                {
                    **meta,
                    "ok": False,
                    "status": "red",
                    "status_label": "缺行情",
                    "score": 0,
                    "why": errors.get(a) or errors.get(b) or "拉不到K线",
                    "corr": None,
                    "beta": None,
                    "adf_pass": False,
                    "half_life": None,
                    "beta_stable": "低",
                    "z": None,
                    "earnings": None,
                }
            )
            continue
        rows.append(_score_pair(meta, bars[a], bars[b]))

    _set_job(stage="评级")
    for r in rows:
        if not r.get("ok"):
            r.setdefault("why", r.get("why") or "缺行情")
            continue
        r["earnings"] = None
        _apply_status(r)

    order = {"green": 0, "yellow": 1, "red": 2}
    rows.sort(key=lambda r: (order.get(r.get("status"), 9), -(r.get("score") or 0)))
    n_ok = sum(1 for r in rows if r.get("ok"))
    n_miss = sum(1 for r in rows if r.get("status_label") == "缺行情")
    note = (
        "不用填代码。股票池已按行业分好，点扫描即可。"
        "先经济关系再统计。相关高不等于协整。绿灯=协整+半衰期5～60天+β尚稳+|z|≥2。"
        "MU/SPY、MU/GOOG 是反例。"
    )
    if n_ok == 0 and n_miss:
        note = "行情没拉到（多半被限流）。不用填代码，过一两分钟再点扫描。"
    payload = {
        "ts": time(),
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "ttl_hours": TTL / 3600,
        "n_symbols": len(symbols),
        "n_pairs": len(rows),
        "n_ok": n_ok,
        "n_missing": n_miss,
        "filters": {
            "min_corr": MIN_CORR,
            "half_life": [HL_LO, HL_HI],
            "lookback": LOOKBACK,
            "earnings_days": EARNINGS_DAYS,
        },
        "note": note,
        "groups": list_groups(),
        "errors": errors,
        "pairs": rows,
    }
    if not groups and n_ok:
        _write_cache(payload)
    return payload


def peek_scan() -> dict:
    hit = _read_cache()
    if hit:
        return hit
    return {
        "pairs": [],
        "cached": False,
        "as_of": None,
        "n_pairs": 0,
        "n_symbols": 0,
        "groups": list_groups(),
        "note": "还没扫过。不用填代码，点扫描即可。按行业组内配对，不会在全市场两两相关。",
    }


def universe() -> dict:
    cands = list(iter_candidates())
    return {
        "groups": list_groups(),
        "n_symbols": len(all_symbols(cands)),
        "n_pairs": len(cands),
        "note": "股票池是美股大盘按行业分组，只在组内配对，外加少量股票vs ETF 和两对反例。",
    }


def start_scan(*, force: bool = True, groups: list[str] | None = None) -> dict:
    with _job_lock:
        if _job["running"]:
            return dict(_job)
        _job.update(running=True, stage="开始", done=0, total=0, symbol="", error=None)

    def work():
        try:
            result = scan_pairs(force=True, groups=groups)
            _set_job(stage="完成", running=False, symbol="", last_result=result)
        except Exception as exc:
            _set_job(running=False, error=str(exc), stage="失败")

    threading.Thread(target=work, daemon=True).start()
    return job_status()
