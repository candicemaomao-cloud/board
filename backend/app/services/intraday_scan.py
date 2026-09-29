"""日内残差均值回归：股票 vs 少量因子，不是长期协整。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median, pstdev
from time import time
from zoneinfo import ZoneInfo

from app.services.arb_strategy import _round
from app.services.intraday_universe import all_symbols, clip_hedges, is_etf, iter_specs, list_baskets
from app.services.ohlc import OhlcError
from app.services.tradingview import fetch_tv_ohlc

CACHE = Path("data/intraday_scan.json")
TTL = 180
# 旧缓存是 vs SPY；换同业/行业 ETF 后必须作废。
SCAN_KIND = "spread_level"
LOOKBACK = 390
LOOKBACK_30M = 130
BARS_PER_DAY = {"5m": 78, "30m": 13, "4h": 7, "1d": 1}
Z_ENTRY = 2.5
Z_WATCH = 2.0
COST_BPS = 8.0
RIDGE = 1e-12
ET = ZoneInfo("America/New_York")
REGIME_BARS = {"5m": 12, "30m": 4, "4h": 10, "1d": 20}
# 对冲腿近 1 小时（5m）/ 2 小时（30m）的绝对涨跌，超过就当板块冲击。
SECTOR_SHOCK = {"5m": 0.012, "30m": 0.018, "4h": 0.025, "1d": 0.03}
# 标的单根远大于对冲，当个股利空/利好，不是残差回归。
IDIO_JUMP = {"5m": 0.01, "30m": 0.015, "4h": 0.02, "1d": 0.025}
# 两腿同向且都有幅度：领涨/领跌在扩散，不是残差回归。
SYMPATHY = {"5m": 0.005, "30m": 0.008, "4h": 0.012, "1d": 0.015}
EARN_TTL = 3 * 3600
N_BARS = {"5m": 520, "30m": 260, "4h": 400, "1d": 520}
LOOKBACK_BY_TF = {"5m": 390, "30m": 130, "4h": 60, "1d": 60}

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
_earn_lock = threading.Lock()
_earn_cache: tuple[float, dict, str] | None = None


class IntradayScanError(Exception):
    pass


def _set_job(**kwargs) -> None:
    with _job_lock:
        _job.update(kwargs)


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    n = len(rhs)
    rows = [matrix[i][:] + [rhs[i]] for i in range(n)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(rows[r][i]))
        rows[i], rows[piv] = rows[piv], rows[i]
        if abs(rows[i][i]) < 1e-12:
            raise ValueError("奇异")
        scale = rows[i][i]
        rows[i] = [x / scale for x in rows[i]]
        for r in range(n):
            if r == i:
                continue
            f = rows[r][i]
            rows[r] = [a - f * c for a, c in zip(rows[r], rows[i])]
    return [row[-1] for row in rows]


def _ridge(y: list[float], cols: list[list[float]], lam: float = RIDGE) -> list[float]:
    n = len(y)
    if n < 20 or not cols:
        raise ValueError("样本不够")
    k = len(cols) + 1
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for t in range(n):
        row = [1.0] + [c[t] for c in cols]
        for i in range(k):
            xty[i] += row[i] * y[t]
            for j in range(k):
                xtx[i][j] += row[i] * row[j]
    for i in range(1, k):
        xtx[i][i] += lam
    return _solve(xtx, xty)


def _rt_cost_pct(n_hedges: int) -> float:
    """往返成本，百分数。两只腿约 1.5× 单边 bp。"""
    return COST_BPS / 100.0 * (1.0 + 0.5 * max(1, int(n_hedges)))


def _log_spread(y_px: list[float], f_px: list[list[float]], hedge_betas: list[float], i: int) -> float:
    s = math.log(max(y_px[i], 1e-12))
    for b, series in zip(hedge_betas, f_px):
        s -= b * math.log(max(series[i], 1e-12))
    return s


def _spread_z(y_px: list[float], f_px: list[list[float]], hedge_betas: list[float], i0: int, i1: int) -> tuple[float, float, float]:
    """价差水平 z：log(标的)−Σβ log(对冲)，用 [i0, i1) 估均值方差，z 看 i1（当前根）。"""
    hist = [_log_spread(y_px, f_px, hedge_betas, i) for i in range(i0, i1)]
    now = _log_spread(y_px, f_px, hedge_betas, i1)
    mu = mean(hist)
    sd = pstdev(hist) if len(hist) > 2 else 0.0
    z = (now - mu) / sd if sd > 1e-12 else 0.0
    return z, now - mu, now


def _half_life(resid: list[float]) -> float | None:
    if len(resid) < 24:
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


def _win(tf: str) -> int:
    return LOOKBACK_BY_TF.get(tf, LOOKBACK)


def _lookback_days(tf: str, n: int | None = None) -> float:
    per = BARS_PER_DAY.get(tf, 78)
    return round((n if n is not None else _win(tf)) / per, 1)


def _bucket(ts: int, tf: str) -> int:
    if tf in {"1d", "1w"}:
        dt = datetime.fromtimestamp(int(ts), tz=ET)
        return int(datetime(dt.year, dt.month, dt.day, tzinfo=ET).timestamp())
    step = {"5m": 300, "30m": 1800, "4h": 14400}.get(tf, 300)
    return int(ts) - int(ts) % step


def _rets(px: list[float]) -> list[float]:
    out = [0.0]
    for i in range(1, len(px)):
        prev = px[i - 1]
        out.append(px[i] / prev - 1.0 if prev else 0.0)
    return out


def _align(bars_map: dict[str, list[dict]], tf: str) -> tuple[list[int], dict[str, list[float]], dict[str, list[float]]]:
    keyed: dict[str, dict[int, dict]] = {}
    keys: set[int] | None = None
    for sym, bars in bars_map.items():
        m = {}
        for bar in bars or []:
            if bar.get("close") is None or bar.get("ts") is None:
                continue
            m[_bucket(int(bar["ts"]), tf)] = bar
        keyed[sym] = m
        ks = set(m)
        keys = ks if keys is None else keys & ks
    if not keys:
        return [], {}, {}
    order = sorted(keys)
    px, vol = {}, {}
    for sym, m in keyed.items():
        px[sym] = [float(m[k]["close"]) for k in order]
        vol[sym] = [float(m[k].get("volume") or 0) for k in order]
    return order, px, vol


def _align_subset(bars_map: dict[str, list[dict]], names: list[str], tf: str):
    subset = {s: bars_map[s] for s in names if bars_map.get(s)}
    return _align(subset, tf)


def _load_bars(symbols: list[str], tf: str) -> tuple[dict[str, list[dict]], dict[str, str]]:
    bars: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    total = len(symbols)
    _set_job(done=0, total=total, stage=f"拉 {tf} 0/{total}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_closes_timed, sym, tf): sym for sym in symbols}
        done = 0
        for fut in as_completed(futs):
            sym = futs[fut]
            done += 1
            _set_job(done=done, total=total, symbol=sym, stage=f"拉 {tf} {sym}（{done}/{total}）")
            try:
                bars[sym] = fut.result()
            except Exception as exc:
                bars[sym] = []
                errors[sym] = str(exc)
    return bars, errors


def _session_slice(ts: list[int], px: list[float], vol: list[float]) -> tuple[list[float], list[float]]:
    if not ts:
        return px, vol
    last = datetime.fromtimestamp(ts[-1], tz=ET).date()
    i0 = 0
    for i, t in enumerate(ts):
        if datetime.fromtimestamp(t, tz=ET).date() == last:
            i0 = i
            break
    return px[i0:], vol[i0:]


def _vwap(px: list[float], vol: list[float]) -> float | None:
    num = sum(p * v for p, v in zip(px, vol))
    den = sum(vol)
    if den <= 0:
        return None
    return num / den


def _closes(symbol: str, tf: str, n_bars: int | None = None) -> list[dict]:
    n = int(n_bars or N_BARS.get(tf, 400))
    need = min(_win(tf) + 10, n)
    last = None
    try:
        bars = fetch_tv_ohlc(symbol, tf, n_bars=n)
        if len(bars) >= need:
            return bars
        last = OhlcError(f"{symbol} 的 {tf} 根数不够")
    except Exception as exc:
        last = exc
    try:
        from app.services.ohlc import YAHOO_ALIAS, YAHOO_TF, _yahoo_bars

        interval, range_ = YAHOO_TF.get(tf, YAHOO_TF["5m"])
        bars, _live = _yahoo_bars(YAHOO_ALIAS.get(symbol, symbol), interval, range_)
        if len(bars) >= need:
            return bars
        last = OhlcError(f"{symbol} 的 {tf} 根数不够")
    except Exception as exc:
        last = last or exc
    if symbol == "SOXX":
        return _closes("SMH", tf, n_bars=n_bars)
    raise OhlcError(f"拉不到 {symbol} 的 {tf}：{last}")


def _closes_timed(symbol: str, tf: str, timeout: float = 22.0, n_bars: int | None = None) -> list[dict]:
    box: dict = {}

    def work():
        try:
            box["v"] = _closes(symbol, tf, n_bars=n_bars)
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


def _cal_symbols(raw) -> list[str]:
    rows = []
    if isinstance(raw, dict):
        rows = raw.get("rows") or raw.get("earnings") or []
    elif isinstance(raw, list):
        rows = raw
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        if sym:
            out.append(sym)
    return out


def _load_earnings_window() -> tuple[dict[str, dict], str]:
    """昨天～明天（美东）的财报日历。失败时返回空，靠价格冲击过滤。"""
    global _earn_cache
    now = time()
    with _earn_lock:
        if _earn_cache and now - _earn_cache[0] < EARN_TTL:
            return _earn_cache[1], _earn_cache[2]
    today = datetime.now(tz=ET).date()
    days = [today + timedelta(days=d) for d in (-1, 0, 1)]
    found: dict[str, dict] = {}
    try:
        import httpx

        from app.services.fundamentals import NASDAQ_HEADERS, _get

        with httpx.Client(timeout=12.0, headers=NASDAQ_HEADERS, follow_redirects=True) as client:
            for d in days:
                raw = _get(client, f"/api/calendar/earnings?date={d.isoformat()}")
                for sym in _cal_symbols(raw):
                    gap = (d - today).days
                    prev = found.get(sym)
                    if prev is None or abs(gap) < abs(prev["days"]):
                        found[sym] = {"symbol": sym, "date": d.isoformat(), "days": gap}
        note = f"财报窗口 {days[0].isoformat()}～{days[-1].isoformat()}，{len(found)} 只。"
    except Exception as exc:
        note = f"财报日历没拉到（{exc}），只靠板块急动/跳空过滤。"
        found = {}
    with _earn_lock:
        _earn_cache = (time(), found, note)
    return found, note


def _earn_hit(sym: str, earn_map: dict[str, dict]) -> dict | None:
    row = earn_map.get((sym or "").strip().upper())
    if not row:
        return None
    if -1 <= int(row.get("days") or 99) <= 1:
        return row
    return None


def _regime(spy_px: list[float], soxx_px: list[float] | None, vix_px: list[float] | None, tf: str) -> dict:
    n = REGIME_BARS.get(tf, 12)
    if len(spy_px) < n + 2:
        return {"regime": "unknown", "label": "状态不明", "mean_reversion": False, "why": "大盘K线不够"}
    spy_ret = spy_px[-1] / spy_px[-1 - n] - 1.0
    sector_ret = None
    if soxx_px and len(soxx_px) > n:
        sector_ret = soxx_px[-1] / soxx_px[-1 - n] - 1.0
    vix_up = False
    if vix_px and len(vix_px) > 8:
        vix_up = vix_px[-1] > mean(vix_px[-20:] if len(vix_px) >= 20 else vix_px) * 1.08
    trend = abs(spy_ret) >= 0.005 and (sector_ret is None or spy_ret * sector_ret > 0)
    if trend:
        return {
            "regime": "trend",
            "label": "趋势",
            "mean_reversion": False,
            "spy_ret": _round(spy_ret * 100, 2),
            "sector_ret": _round((sector_ret or 0) * 100, 2) if sector_ret is not None else None,
            "vix_up": vix_up,
            "why": f"近{'1小时' if tf == '5m' else '2小时'} SPY {spy_ret * 100:+.2f}% 且板块同向，残差可能不回归。",
        }
    return {
        "regime": "mean_reversion",
        "label": "均值回归",
        "mean_reversion": True,
        "spy_ret": _round(spy_ret * 100, 2),
        "sector_ret": _round((sector_ret or 0) * 100, 2) if sector_ret is not None else None,
        "vix_up": vix_up,
        "why": "大盘/板块没有单边突破，更适合做残差回归。",
    }


def _score_row(
    spec: dict,
    ts: list[int],
    px: dict[str, list[float]],
    vol: dict[str, list[float]],
    regime: dict,
    tf: str,
    earn_map: dict[str, dict] | None = None,
) -> dict:
    target = spec["target"]
    factors = spec["factors"]
    fail = {
        **spec,
        "ok": False,
        "status": "red",
        "status_label": "数据不够",
        "score": 0,
        "why": "对齐后K线不够",
        "z": None,
        "residual_pct": None,
        "signal": "none",
    }
    y_px = px.get(target) or []
    window = _win(tf)
    if len(y_px) < window + 8:
        return fail
    f_px = []
    for f in factors:
        series = px.get(f) or []
        if len(series) != len(y_px):
            return {**fail, "why": f"缺因子 {f}"}
        f_px.append(series)
    y_all = _rets(y_px)
    cols_all = [_rets(s) for s in f_px]
    y = y_all[-window:]
    cols = [c[-window:] for c in cols_all]
    try:
        beta = _ridge(y, cols)
    except Exception as exc:
        return {**fail, "why": f"回归失败：{exc}"}
    hedge_b = [beta[i + 1] for i in range(len(factors))]
    n_px = len(y_px)
    z, disloc, _sp = _spread_z(y_px, f_px, hedge_b, n_px - window - 1, n_px - 1)
    spread_hist = [_log_spread(y_px, f_px, hedge_b, i) for i in range(n_px - window - 1, n_px)]
    hl = _half_life(spread_hist)
    fitted = beta[0] + sum(hedge_b[i] * cols[i][-1] for i in range(len(cols)))
    eps = y[-1] - fitted
    day_px, day_vol = _session_slice(ts, px[target], vol.get(target) or [0] * len(ts))
    vwap = _vwap(day_px, day_vol)
    vwap_dev = (day_px[-1] / vwap - 1.0) if vwap and day_px else None
    last_vol = (vol.get(target) or [0])[-1]
    med_vol = median([v for v in (vol.get(target) or [])[-window:] if v > 0] or [0])
    rvol = last_vol / med_vol if med_vol else None
    mom_1 = y[-1] * 100
    mom_n = (sum(y[-REGIME_BARS.get(tf, 12) :]) * 100) if len(y) >= 4 else None
    edge = abs(disloc) * 100
    cost = _rt_cost_pct(len(factors))
    net = edge - cost
    info_shock = bool(rvol and rvol >= 3.5 and abs(z) >= Z_WATCH)
    nreg = REGIME_BARS.get(tf, 12)
    hedge_hr = sum(cols[0][-nreg:]) if cols and len(cols[0]) >= nreg else (cols[0][-1] if cols else 0.0)
    tgt_hr = sum(y[-nreg:]) if len(y) >= nreg else y[-1]
    last_y = y[-1]
    last_h = cols[0][-1] if cols else 0.0
    floor = SYMPATHY.get(tf, 0.005)
    sympathy = tgt_hr * hedge_hr > 0 and abs(tgt_hr) >= floor and abs(hedge_hr) >= floor
    sector_shock = abs(hedge_hr) >= SECTOR_SHOCK.get(tf, 0.012)
    idio_jump = abs(last_y) >= IDIO_JUMP.get(tf, 0.01) and abs(last_y) > 2.2 * max(abs(last_h), 0.0005)
    earn_map = earn_map or {}
    leg_hits = []
    seen_earn = set()
    for s in [target, *factors]:
        if is_etf(s) or s in seen_earn:
            continue
        hit = _earn_hit(s, earn_map)
        if hit:
            seen_earn.add(s)
            leg_hits.append(hit)
    leader_hits = []
    for s in spec.get("leaders") or []:
        if s in seen_earn or is_etf(s):
            continue
        hit = _earn_hit(s, earn_map)
        if hit:
            seen_earn.add(s)
            leader_hits.append(hit)
    betas = {factors[i]: _round(beta[i + 1], 3) for i in range(len(factors))}
    hedge = " + ".join(f"{betas[f]}×{f}" for f in factors)
    expected = fitted * 100
    actual = y[-1] * 100

    why = []
    score = 0.0
    score += 30 * min(abs(z) / Z_ENTRY, 1.5)
    event_kind = None
    event_label = None
    if leg_hits:
        event_kind = "earnings"
        bits = "、".join(f"{h['symbol']} {h['date']}" for h in leg_hits)
        event_label = f"财报 {bits}"
        why.append(f"{bits} 在财报窗口。残差是新信息，淡化就是给市场送钱。")
        score -= 40
    elif leader_hits:
        event_kind = "leader"
        bits = "、".join(f"{h['symbol']} {h['date']}" for h in leader_hits)
        event_label = f"龙头财报 {bits}"
        why.append(f"板块龙头 {bits} 出财报，整行业重定价。同业对冲只对冲 β，挡不住这次。")
        score -= 40
    elif sympathy:
        event_kind = "sympathy"
        event_label = "板块带动"
        why.append(
            f"{target} {tgt_hr * 100:+.2f}% 与对冲 {hedge_hr * 100:+.2f}% 同向。"
            "一只股票带动行业是常态，缺口是扩散不是回归。"
        )
        score -= 35
    elif sector_shock:
        event_kind = "sector"
        event_label = "板块冲击"
        why.append(f"对冲腿近窗口 {hedge_hr * 100:+.2f}%，像板块急动，禁止淡化残差。")
        score -= 35
    elif idio_jump:
        event_kind = "jump"
        event_label = "个股跳空"
        why.append(f"{target} 单根 {last_y * 100:+.2f}%，对冲几乎没动，像个股利空/利好。")
        score -= 35
    if regime.get("mean_reversion"):
        score += 20
    else:
        why.append("趋势市，禁止逆势做残差。")
        score -= 25
    if info_shock:
        why.append(f"量能 {rvol:.1f}×，可能是信息冲击，不要当均值回归。")
        score -= 20
    if net <= 0.04:
        why.append(f"价差偏离 {edge:.2f}% 盖不住约 {cost:.2f}% 往返成本。")
        score -= 15
    else:
        score += 15 * min(net / 0.2, 1.0)
    hl_hi = BARS_PER_DAY.get(tf, 78)
    if hl is not None and 4 <= hl <= hl_hi:
        score += 10
    elif hl is not None and hl > hl_hi * 1.5:
        why.append(f"价差半衰期 {hl:.0f} 根，太慢。")
    if vwap_dev is not None and z * vwap_dev > 0:
        score += 8

    blocked = bool(event_kind or info_shock)
    if blocked:
        status, label = "red", event_label or "信息冲击"
        signal = "none"
    elif abs(z) >= Z_ENTRY and regime.get("mean_reversion") and net > 0.04:
        status, label = "green", "值得研究"
        signal = "short" if z > 0 else "long"
    elif abs(z) >= Z_WATCH:
        status, label = "yellow", "观察"
        signal = "short" if z > 0 else "long"
    else:
        status, label = "red", "不交易"
        signal = "none"

    if not why:
        if signal == "short":
            why.append(f"价差相对均值 {disloc * 100:+.2f}%（z={z:.1f}），偏贵，研究空 {target}、买对冲。")
        elif signal == "long":
            why.append(f"价差相对均值 {disloc * 100:+.2f}%（z={z:.1f}），偏便宜，研究买 {target}、空对冲。")
        else:
            why.append("价差还在正常带里。")

    action = "不交易"
    if signal == "short":
        action = f"研究做空 {target}，对冲买 {hedge}"
    elif signal == "long":
        action = f"研究做多 {target}，对冲卖 {hedge}"

    return {
        **spec,
        "ok": True,
        "n": len(y),
        "alpha": _round(beta[0] * 100, 4),
        "betas": betas,
        "hedge": hedge,
        "spread_pct": _round(disloc * 100, 3),
        "residual_pct": _round(eps * 100, 3),
        "expected_pct": _round(expected, 3),
        "actual_pct": _round(actual, 3),
        "z": _round(z, 2),
        "half_life": _round(hl, 1),
        "rvol": _round(rvol, 2),
        "vwap_dev_pct": _round((vwap_dev or 0) * 100, 2) if vwap_dev is not None else None,
        "mom_1": _round(mom_1, 3),
        "mom_n": _round(mom_n, 3) if mom_n is not None else None,
        "edge_pct": _round(edge, 3),
        "cost_pct": _round(cost, 3),
        "net_pct": _round(net, 3),
        "info_shock": info_shock,
        "event": event_kind,
        "event_label": event_label,
        "earnings": (leg_hits + leader_hits)[0] if (leg_hits or leader_hits) else None,
        "target_move_pct": _round(tgt_hr * 100, 2),
        "sector_move_pct": _round(hedge_hr * 100, 2),
        "regime": regime.get("regime"),
        "regime_label": regime.get("label"),
        "signal": signal,
        "action": action,
        "status": status,
        "status_label": label,
        "score": _round(max(score, 0), 1),
        "why": "".join(why),
        "price": _round(y_px[-1], 4),
    }


def peek_scan() -> dict:
    if CACHE.exists():
        try:
            payload = json.loads(CACHE.read_text())
            if (
                time() - float(payload.get("ts") or 0) <= TTL
                and payload.get("rows")
                and payload.get("scan_kind") == SCAN_KIND
            ):
                return payload
        except Exception:
            pass
    return {
        "rows": [],
        "cached": False,
        "as_of": None,
        "n": 0,
        "baskets": list_baskets(),
        "note": "对冲是同业或行业 ETF（NVDA/AMD、MU/SOXX），不是 vs SPY。点扫描。",
    }


def universe() -> dict:
    specs = iter_specs()
    targets = {s["target"] for s in specs}
    return {
        "baskets": list_baskets(),
        "n_rows": len(specs),
        "n_targets": len(targets),
        "n_symbols": len(all_symbols(specs, ["VIX"])),
        "note": "默认扫同业两腿和股票 vs 行业 ETF。vs SPY 只在「对照：vs 大盘」，不当配对。",
    }


def job_status() -> dict:
    with _job_lock:
        out = dict(_job)
    peek = peek_scan()
    result = out.pop("last_result", None) or (peek if peek.get("rows") else None)
    if result and result.get("rows"):
        out["as_of"] = result.get("as_of")
        out["n"] = result.get("n")
        if not out.get("running"):
            out["result"] = result
    return out


def scan(*, force: bool = True, baskets: list[str] | None = None, timeframe: str = "5m") -> dict:
    tf = timeframe if timeframe in {"5m", "30m"} else "5m"
    if not force:
        hit = peek_scan()
        if hit.get("rows") and hit.get("timeframe") == tf and not baskets:
            return hit

    specs = iter_specs(baskets)
    if not specs:
        raise IntradayScanError("没有候选")
    symbols = all_symbols(specs, ["SPY", "SOXX", "QQQ", "VIX"])
    bars, errors = _load_bars(symbols, tf)

    _set_job(stage="财报日历", done=len(symbols), total=len(symbols))
    earn_map, earn_note = _load_earnings_window()

    _set_job(stage="回归残差", done=len(symbols), total=len(symbols))
    _, reg_px, _ = _align_subset(bars, ["SPY", "SOXX", "QQQ", "VIX"], tf)
    regime = _regime(reg_px.get("SPY") or [], reg_px.get("SOXX") or reg_px.get("QQQ"), reg_px.get("VIX"), tf)

    rows = []
    for spec in specs:
        names = [spec["target"], *spec["factors"]]
        ts, px, vol = _align_subset(bars, names, tf)
        missing = [s for s in names if s not in px]
        if missing:
            miss = missing[0]
            rows.append(
                {
                    **spec,
                    "ok": False,
                    "status": "red",
                    "status_label": "缺行情",
                    "score": 0,
                    "why": errors.get(miss) or f"拉不到 {miss}",
                    "z": None,
                    "signal": "none",
                    "event": None,
                    "event_label": None,
                }
            )
            continue
        rows.append(_score_row(spec, ts, px, vol, regime, tf, earn_map))

    order = {"green": 0, "yellow": 1, "red": 2}
    rows.sort(key=lambda r: (order.get(r.get("status"), 9), -(r.get("score") or 0), -abs(r.get("z") or 0)))
    n_ok = sum(1 for r in rows if r.get("ok"))
    payload = {
        "ts": time(),
        "as_of": datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M ET"),
        "timeframe": tf,
        "lookback": _win(tf),
        "lookback_days": _lookback_days(tf),
        "z_entry": Z_ENTRY,
        "cost_bps": COST_BPS,
        "n": len(rows),
        "n_ok": n_ok,
        "n_symbols": len(symbols),
        "scan_kind": SCAN_KIND,
        "regime": regime,
        "baskets": list_baskets(),
        "earnings_note": earn_note,
        "errors": errors,
        "rows": rows,
        "note": (
            "一只股票涨跌带动行业是常态。对冲只拿掉共同 β。"
            "Z 看价差水平（log 价 − β·对冲），不是单根 5 分钟噪声。"
            "价差偏离盖不住往返成本、同向带动、财报、跳空，一律不交易。"
            f"|Z|≥{Z_ENTRY:g} 才研究。回归用约 {_lookback_days(tf)} 个交易日的 {tf} K。"
        ),
    }
    if n_ok:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(payload, ensure_ascii=False))
    return payload


def start_scan(*, force: bool = True, baskets: list[str] | None = None, timeframe: str = "5m") -> dict:
    with _job_lock:
        if _job["running"]:
            return dict(_job)
        _job.update(running=True, stage="开始", done=0, total=0, symbol="", error=None)

    def work():
        try:
            result = scan(force=True, baskets=baskets, timeframe=timeframe)
            _set_job(stage="完成", running=False, symbol="", last_result=result)
        except Exception as exc:
            _set_job(running=False, error=str(exc), stage="失败")

    threading.Thread(target=work, daemon=True).start()
    return job_status()


BT_BARS = {"5m": 1800, "30m": 700, "4h": 800, "1d": 520}
TF_LABEL = {"5m": "5min", "30m": "30min", "4h": "4h", "1d": "D线"}
TREND_TH = {"5m": 0.005, "30m": 0.008, "4h": 0.015, "1d": 0.03}


def _ts_label(ts: int, tf: str = "5m") -> str:
    dt = datetime.fromtimestamp(int(ts), tz=ET)
    if tf in {"1d", "1w"}:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%m-%d %H:%M")


def backtest_residual(
    *,
    target: str,
    factors: list[str],
    timeframe: str = "5m",
    entry_z: float = 2.5,
    exit_z: float = 0.75,
    stop_z: float = 4.0,
    notional: float = 10_000.0,
    cost_bps: float = COST_BPS,
    lookback: int | None = None,
) -> dict:
    """滚动残差 z：样本内开平，成本后盈亏。5 分钟大约一个月样本，不是年线协整回测。"""
    target = (target or "").strip().upper()
    factors = [f.strip().upper() for f in (factors or []) if f and f.strip().upper() != target]
    seen = []
    for f in factors:
        if f not in seen:
            seen.append(f)
    factors = clip_hedges(seen, target)
    tf = timeframe if timeframe in {"5m", "30m", "4h", "1d"} else "5m"
    if not target or not factors:
        raise IntradayScanError("需要标的和至少一个对冲标的")
    window = int(lookback) if lookback else _win(tf)
    window = max(20, min(window, 500))
    n_fetch = BT_BARS.get(tf, 520)

    def stamp(t):
        return _ts_label(t, tf)
    names = [target, *factors]
    bars: dict[str, list[dict]] = {}
    errors = {}
    for sym in names:
        try:
            bars[sym] = _closes_timed(sym, tf, timeout=35.0, n_bars=n_fetch)
        except Exception as exc:
            errors[sym] = str(exc)
    missing = [s for s in names if not bars.get(s)]
    if missing:
        raise IntradayScanError(f"拉不到 {', '.join(missing)}：{errors.get(missing[0]) or ''}")

    ts, px, _vol = _align(bars, tf)
    y_px = px[target]
    y = _rets(y_px)[1:]
    cols = [_rets(px[f])[1:][ : len(y)] for f in factors]
    y = y[: min(len(y), min(len(c) for c in cols))]
    cols = [c[: len(y)] for c in cols]
    ts = ts[1 : len(y) + 1]
    y_px = y_px[1 : len(y) + 1]
    f_px = [px[f][1 : len(y) + 1] for f in factors]
    if len(y) < window + 30:
        raise IntradayScanError(f"对齐后只有 {len(y)} 根，不够回测")

    earn_block: set[date] = set()
    try:
        from app.services.macro_event_study import _earnings_dates

        for s in [target, *factors]:
            if is_etf(s):
                continue
            dates, _ = _earnings_dates(s)
            for ed in dates:
                for g in (-1, 0, 1):
                    earn_block.add(ed + timedelta(days=g))
    except Exception:
        pass

    entry = max(1.5, float(entry_z))
    exit_th = min(float(exit_z), entry - 0.2)
    stop = max(float(stop_z), entry + 0.5)
    cash = max(100.0, float(notional))
    cost_rt = cash * (cost_bps / 10_000.0) * (1.0 + 0.5 * len(factors))

    pos = 0
    wait = False
    entry_i = None
    entry_z_now = None
    entry_px = None
    trades = []
    equity = []
    total = 0.0
    blocked_trend = 0
    blocked_event = 0
    path_z = [0.0] * len(y)
    open_eps_acc = 0.0
    last_beta = None
    entry_b: list[float] | None = None

    def _close_trade(why: str, t: int, z_now: float) -> None:
        nonlocal total, pos, wait, open_eps_acc, entry_i, entry_b
        gross = open_eps_acc
        pnl = gross - cost_rt
        trades.append(
            {
                "side": "long_resid" if pos > 0 else "short_resid",
                "entry": stamp(ts[entry_i]),
                "exit": stamp(ts[t]),
                "entry_z": _round(entry_z_now, 2),
                "exit_z": _round(z_now, 2),
                "px_in": _round(entry_px, 4),
                "px_out": _round(y_px[t], 4),
                "pnl_gross": _round(gross, 2),
                "pnl": _round(pnl, 2),
                "why": why,
            }
        )
        total += pnl
        pos = 0
        wait = why == "stop"
        open_eps_acc = 0.0
        entry_i = None
        entry_b = None

    for t in range(window, len(y)):
        y_w = y[t - window : t]
        cols_w = [c[t - window : t] for c in cols]
        try:
            beta = _ridge(y_w, cols_w)
            last_beta = beta
        except Exception:
            path_z[t] = 0.0
            equity.append(total + open_eps_acc)
            continue
        hedge_b = [beta[i + 1] for i in range(len(factors))]
        use_b = entry_b if pos != 0 and entry_b else hedge_b
        try:
            z, _, _ = _spread_z(y_px, f_px, use_b, t - window, t)
        except Exception:
            z = 0.0
        path_z[t] = z

        if pos != 0 and entry_b:
            resid_ret = y[t] - sum(entry_b[i] * cols[i][t] for i in range(len(factors)))
            open_eps_acc += pos * cash * resid_ret
            if abs(z) >= stop:
                _close_trade("stop", t, z)
            elif abs(z) <= exit_th:
                _close_trade("exit", t, z)
            equity.append(total + open_eps_acc)
            continue

        if wait:
            if abs(z) <= exit_th:
                wait = False
            equity.append(total)
            continue

        spy = None
        if "SPY" in px:
            spy_px = px["SPY"]
            nreg = REGIME_BARS.get(tf, 12)
            if t + 1 < len(spy_px) and t + 1 > nreg:
                spy = spy_px[t + 1] / spy_px[t + 1 - nreg] - 1.0
        trend = spy is not None and abs(spy) >= TREND_TH.get(tf, 0.005)
        nreg = REGIME_BARS.get(tf, 12)
        hedge_move = sum(cols[0][t - nreg + 1 : t + 1]) if t >= nreg - 1 else cols[0][t]
        tgt_move = sum(y[t - nreg + 1 : t + 1]) if t >= nreg - 1 else y[t]
        floor = SYMPATHY.get(tf, 0.005)
        event = False
        if tgt_move * hedge_move > 0 and abs(tgt_move) >= floor and abs(hedge_move) >= floor:
            event = True
        elif abs(hedge_move) >= SECTOR_SHOCK.get(tf, 0.012):
            event = True
        elif abs(y[t]) >= IDIO_JUMP.get(tf, 0.01) and abs(y[t]) > 2.2 * max(abs(cols[0][t]), 0.0005):
            event = True
        else:
            d = datetime.fromtimestamp(int(ts[t]), tz=ET).date()
            event = d in earn_block
        if trend:
            blocked_trend += 1
            equity.append(total)
            continue
        if event:
            blocked_event += 1
            equity.append(total)
            continue

        if z >= entry:
            pos = -1
            entry_i = t
            entry_z_now = z
            entry_px = y_px[t]
            entry_b = hedge_b
            open_eps_acc = 0.0
        elif z <= -entry:
            pos = 1
            entry_i = t
            entry_z_now = z
            entry_px = y_px[t]
            entry_b = hedge_b
            open_eps_acc = 0.0
        equity.append(total + open_eps_acc)

    if pos != 0 and entry_i is not None:
        pnl = open_eps_acc - cost_rt
        trades.append(
            {
                "side": "long_resid" if pos > 0 else "short_resid",
                "entry": stamp(ts[entry_i]),
                "exit": stamp(ts[-1]),
                "entry_z": _round(entry_z_now, 2),
                "exit_z": _round(path_z[-1], 2),
                "px_in": _round(entry_px, 4),
                "px_out": _round(y_px[-1], 4),
                "pnl_gross": _round(open_eps_acc, 2),
                "pnl": _round(pnl, 2),
                "why": "open",
            }
        )
        total += pnl

    pnls = [t["pnl"] for t in trades if t.get("why") != "open"]
    closed = [t for t in trades if t.get("why") != "open"]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    peak = mdd = 0.0
    for x in equity:
        peak = max(peak, x)
        mdd = min(mdd, x - peak)
    split_i = ts[window + int((len(ts) - window) * 0.7)] if len(ts) > window + 10 else ts[-1]
    split_lbl = _ts_label(split_i, tf)
    is_tr = [t for t in closed if t["entry"] < split_lbl]
    oos_tr = [t for t in closed if t["entry"] >= split_lbl]
    net = sum(pnls)
    days = _lookback_days(tf, len(y))
    z_now = path_z[-1] if path_z else None
    open_row = next((t for t in trades if t.get("why") == "open"), None)
    if open_row:
        signal = "long_spread" if open_row["side"] == "long_resid" else "short_spread"
    else:
        signal = "flat"
    betas = {}
    alpha = None
    if last_beta is not None:
        alpha = _round(last_beta[0], 5)
        betas = {factors[i]: _round(last_beta[i + 1], 3) for i in range(len(factors))}
    tf_label = TF_LABEL.get(tf, tf)
    start_lbl = _ts_label(ts[window], tf) if len(ts) > window else (_ts_label(ts[0], tf) if ts else None)
    end_lbl = _ts_label(ts[-1], tf) if ts else None
    return {
        "ok": True,
        "target": target,
        "factors": factors,
        "hedge": "+".join(factors),
        "timeframe": tf,
        "tf_label": tf_label,
        "lookback": window,
        "bars": len(y),
        "trade_bars": max(0, len(y) - window),
        "days": days,
        "start": start_lbl,
        "as_of": end_lbl,
        "z": _round(z_now, 3) if z_now is not None else None,
        "signal": signal,
        "alpha": alpha,
        "betas": betas,
        "price": _round(y_px[-1], 4) if y_px else None,
        "entry_z": entry,
        "exit_z": exit_th,
        "stop_z": stop,
        "notional": cash,
        "cost_bps": cost_bps,
        "cost_drag": _round(cost_rt * len(closed), 2),
        "total_pnl_gross": _round(sum((t.get("pnl_gross") or 0) for t in closed), 2),
        "total_pnl": _round(sum(t["pnl"] for t in closed), 2),
        "total_pnl_net": _round(net, 2),
        "roi_pct": _round(net / cash * 100, 2),
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_pnl": _round(mean(pnls), 2) if pnls else None,
        "mdd": _round(mdd, 2),
        "mdd_pct": _round(mdd / cash * 100, 2),
        "blocked_trend": blocked_trend,
        "blocked_event": blocked_event,
        "holdout": {
            "split": split_lbl,
            "in_sample": {
                "trades": len(is_tr),
                "pnl": _round(sum(t["pnl"] for t in is_tr), 2),
            },
            "out_of_sample": {
                "trades": len(oos_tr),
                "pnl": _round(sum(t["pnl"] for t in oos_tr), 2),
            },
        },
        "recent": trades[-20:],
        "note": (
            f"{target} vs {', '.join(factors)}，{tf} 滚动 {window} 根估 β，"
            f"Z 是价差水平（不是单根噪声）。|Z|≥{entry:g} 开、≤{exit_th:g} 平、≥{stop:g} 停。"
            f"样本约 {days} 个交易日。盈亏按持仓期内价差变化×名义，再扣往返约 {cost_rt:.0f}/笔。"
            "成本后仍亏，说明这对在这段样本里没有边。"
        ),
    }
