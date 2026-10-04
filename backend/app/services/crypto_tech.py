"""虚拟币技术指标：复用股票侧 snapshot / 条件库，基于币安 K 线；另加链上大额买卖。"""

from __future__ import annotations

from app.services.crypto_market import CryptoMarketError, klines
from app.services.strategy_calc import pack_prices
from app.services.strategy_spec import INDICATORS, flags_from_snapshot
from app.services.indicators import snapshot

TF_MAP = {
    "5m": "5m",
    "5min": "5m",
    "30m": "30m",
    "30min": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
}

# 仅虚拟币可用的链上/大户指标（不进股票指标库）
CRYPTO_EXTRA_INDICATORS = [
    {
        "id": "whale_large_buy",
        "name": "链上大量买入",
        "group": "链上",
        "hint": "近期大额成交净买入占优（买入额明显大于卖出）",
    },
    {
        "id": "whale_large_sell",
        "name": "链上大量卖出",
        "group": "链上",
        "hint": "近期大额成交净卖出占优（卖出额明显大于买入）",
    },
]

CRYPTO_INDICATORS = list(INDICATORS) + CRYPTO_EXTRA_INDICATORS
CRYPTO_INDICATOR_MAP = {row["id"]: row for row in CRYPTO_INDICATORS}


def bars_to_ohlc(symbol: str, timeframe: str, bars: list[dict]) -> dict:
    closes = [float(b["close"]) for b in bars]
    return {
        "symbol": symbol,
        "source": bars[-1].get("market") or "binance_spot",
        "timeframe": timeframe,
        "price": closes[-1] if closes else None,
        "closes": closes,
        "ohlc_bars": bars,
        "bars": len(closes),
    }


def fetch_ohlc(binance_symbol: str, interval: str = "1d", limit: int | None = None) -> dict:
    tf = TF_MAP.get(interval, TF_MAP.get(str(interval).lower(), "1d"))
    if limit is None:
        limit = 500 if tf in ("5m", "30m", "1h") else 220
    bars = klines(binance_symbol, interval=tf, limit=limit)
    if len(bars) < 30:
        raise CryptoMarketError("K 线不足，无法计算指标")
    return bars_to_ohlc(binance_symbol, tf, bars)


def whale_flags(binance_symbol: str) -> dict:
    """大额成交代理链上大量买卖：净买入/净卖出 + 占比。"""
    from app.services.crypto_onchain import whale_trades

    out = {"whale_large_buy": False, "whale_large_sell": False, "whale": None}
    try:
        data = whale_trades(binance_symbol)
    except Exception:  # noqa: BLE001
        return out
    buy = float(data.get("buy_quote") or 0)
    sell = float(data.get("sell_quote") or 0)
    net = float(data.get("net_quote") or (buy - sell))
    total = buy + sell
    out["whale"] = {
        "buy_quote": buy,
        "sell_quote": sell,
        "net_quote": net,
        "min_quote": data.get("min_quote"),
        "count": len(data.get("items") or []),
    }
    if total <= 0:
        return out
    # 净方向占优：净额至少占合计 15%，且绝对净额不低于门槛
    min_net = max(200_000.0, total * 0.15)
    if net >= min_net:
        out["whale_large_buy"] = True
    elif net <= -min_net:
        out["whale_large_sell"] = True
    return out


def analyze(binance_symbol: str, interval: str = "1d") -> dict:
    ohlc = fetch_ohlc(binance_symbol, interval=interval)
    extra: dict = {}
    for need in ("4h", "1d"):
        if ohlc.get("timeframe") == need:
            extra[need] = ohlc
            continue
        try:
            extra[need] = fetch_ohlc(binance_symbol, interval=need)
        except CryptoMarketError:
            extra[need] = None
    ind = snapshot(ohlc, extra)
    flags = flags_from_snapshot(ind)
    for k, v in (ind.get("flags") or {}).items():
        flags.setdefault(k, bool(v))

    whale = whale_flags(binance_symbol)
    for key in ("whale_large_buy", "whale_large_sell"):
        flags[key] = bool(whale.get(key))

    items = []
    groups: dict[str, list] = {}
    for row in CRYPTO_INDICATORS:
        hit = bool(flags.get(row["id"]))
        item = {**row, "hit": hit}
        items.append(item)
        groups.setdefault(row.get("group") or "其他", []).append(item)
    hit_n = sum(1 for x in items if x["hit"])
    return {
        "symbol": binance_symbol,
        "interval": ohlc.get("timeframe"),
        "asof": bars_asof(ohlc),
        "prices": pack_prices(ind, ohlc.get("price")),
        "flags": flags,
        "whale": whale.get("whale"),
        "items": items,
        "groups": [{"name": name, "items": rows} for name, rows in groups.items()],
        "hit_count": hit_n,
        "total": len(items),
    }


def bars_asof(ohlc: dict) -> str | None:
    bars = ohlc.get("ohlc_bars") or []
    if not bars:
        return None
    ts = bars[-1].get("ts")
    return str(ts) if ts is not None else None


def eval_combo(binance_symbol: str, indicator_ids: list[str], join: str = "and", interval: str = "1d") -> dict:
    data = analyze(binance_symbol, interval=interval)
    flags = data.get("flags") or {}
    ids = [str(x) for x in indicator_ids if x]
    details = []
    hits = []
    for iid in ids:
        ok = bool(flags.get(iid))
        hits.append(ok)
        meta = CRYPTO_INDICATOR_MAP.get(iid)
        details.append({"id": iid, "name": (meta or {}).get("name") or iid, "hit": ok})
    if not ids:
        hit = False
    elif (join or "and").lower() == "or":
        hit = any(hits)
    else:
        hit = all(hits)
    return {
        **data,
        "combo_hit": hit,
        "combo_join": join or "and",
        "combo_details": details,
        "price": (data.get("prices") or {}).get("last"),
    }


_WHALE_IDS = {"whale_large_buy", "whale_large_sell"}
_BT_WARMUP = 80
_BT_MAX = {"5m": 800, "30m": 800, "1h": 600, "4h": 500, "1d": 400, "1w": 200}


def _cut_ohlc(ohlc: dict | None, end_ts: int) -> dict | None:
    if not ohlc:
        return None
    bars = [row for row in (ohlc.get("ohlc_bars") or []) if row.get("ts") is not None and int(row["ts"]) <= end_ts]
    if not bars:
        return None
    closes = [float(row["close"]) for row in bars]
    return {
        **ohlc,
        "ohlc_bars": bars,
        "closes": closes,
        "price": closes[-1],
        "bars": len(closes),
    }


def _asof_bar(ts, timeframe: str) -> str:
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    if timeframe in ("1d", "1w"):
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M")


def backtest_combo(
    binance_symbol: str,
    indicator_ids: list[str],
    join: str = "and",
    interval: str = "1d",
    *,
    capital: float = 10000.0,
    max_bars: int | None = None,
) -> dict:
    """指标组合回测：组合从「未命中→命中」开多，从「命中→未命中」平多。

    链上大量买/卖依赖实时大额成交，历史回测里当作永不命中，并在 warnings 里说明。
    """
    from datetime import datetime, timezone

    from app.services.binance import normalize_symbol
    from app.services.ohlc import fetch_closes_covering

    sym = normalize_symbol(binance_symbol)
    if not sym:
        raise CryptoMarketError("请填写交易对")
    ids = [str(x) for x in indicator_ids if x]
    if not ids:
        raise CryptoMarketError("请至少选一个指标")

    tf = TF_MAP.get(interval, TF_MAP.get(str(interval).lower(), "1d"))
    whale_used = [i for i in ids if i in _WHALE_IDS]
    tech_ids = [i for i in ids if i not in _WHALE_IDS]
    warnings: list[str] = []
    if whale_used:
        warnings.append(
            "「链上大量买入/卖出」依赖实时大额成交，回测里按未命中处理："
            + "、".join((CRYPTO_INDICATOR_MAP.get(i) or {}).get("name") or i for i in whale_used)
        )
    if not tech_ids and whale_used:
        raise CryptoMarketError("当前组合只含链上指标，无法做历史回测，请再加技术指标")

    ohlc = fetch_closes_covering(sym, tf, start=None)
    bars = list(ohlc.get("ohlc_bars") or [])
    if len(bars) < _BT_WARMUP + 10:
        # 短周期再试分页拉
        try:
            from app.services.binance import public_kline_bars_covering
            from datetime import date, timedelta

            covered = public_kline_bars_covering(sym, tf, start=date.today() - timedelta(days=90))
            if covered:
                ohlc = bars_to_ohlc(sym, tf, covered)
                bars = list(ohlc.get("ohlc_bars") or [])
        except Exception:  # noqa: BLE001
            pass
    if len(bars) < _BT_WARMUP + 10:
        raise CryptoMarketError(f"K 线不足（需要至少 {_BT_WARMUP + 10} 根），换日线或稍后再试")

    extra: dict = {}
    for need in ("4h", "1d"):
        if tf == need:
            extra[need] = ohlc
            continue
        try:
            extra[need] = fetch_closes_covering(sym, need, start=None)
        except Exception:  # noqa: BLE001
            extra[need] = None

    cap = max_bars or _BT_MAX.get(tf, 400)
    start_i = max(_BT_WARMUP, len(bars) - cap)
    indexes = list(range(start_i, len(bars)))

    join_or = (join or "and").lower() == "or"
    trades: list[dict] = []
    position = None
    prev_hit = False
    signal_log: list[dict] = []
    hit_bars = 0

    for i in indexes:
        bar = bars[i]
        end_ts = int(bar["ts"])
        window = {
            **ohlc,
            "ohlc_bars": bars[: i + 1],
            "closes": [float(row["close"]) for row in bars[: i + 1]],
            "price": float(bar["close"]),
            "bars": i + 1,
        }
        extra_cut = {k: _cut_ohlc(v, end_ts) for k, v in extra.items()}
        ind = snapshot(window, extra_cut)
        flags = flags_from_snapshot(ind)
        for k, v in (ind.get("flags") or {}).items():
            flags.setdefault(k, bool(v))
        for wid in _WHALE_IDS:
            flags[wid] = False

        hits = [bool(flags.get(iid)) for iid in (tech_ids if whale_used else ids)]
        if not hits:
            hit = False
        elif join_or:
            hit = any(hits)
        else:
            hit = all(hits)

        if hit:
            hit_bars += 1
        price = float(bar["close"])
        asof = _asof_bar(end_ts, tf)
        turned_on = hit and not prev_hit
        turned_off = (not hit) and prev_hit
        signal_log.append({"asof": asof, "price": round(price, 6), "hit": hit, "enter": turned_on, "exit": turned_off})

        if turned_on and position is None:
            qty = capital / price if price > 0 else 0
            position = {"entry_asof": asof, "entry_price": price, "qty": qty, "side": "多"}
        elif turned_off and position is not None:
            pnl = (price - position["entry_price"]) * position["qty"]
            trades.append(
                {
                    "side": "多",
                    "entry_day": position["entry_asof"],
                    "exit_day": asof,
                    "entry_price": round(position["entry_price"], 6),
                    "exit_price": round(price, 6),
                    "qty": round(position["qty"], 6),
                    "pnl": round(pnl, 4),
                    "exit_reason": "组合失效",
                }
            )
            capital += pnl
            position = None
        prev_hit = hit

    unrealized = None
    if position is not None:
        last = bars[indexes[-1]]
        px = float(last["close"])
        pnl = (px - position["entry_price"]) * position["qty"]
        unrealized = {
            "side": "多",
            "entry_day": position["entry_asof"],
            "exit_day": None,
            "entry_price": round(position["entry_price"], 6),
            "exit_price": round(px, 6),
            "qty": round(position["qty"], 6),
            "pnl": round(pnl, 4),
            "exit_reason": "未平仓",
        }
        capital += pnl

    realized = [t for t in trades if t.get("exit_day")]
    win_trades = [t for t in realized if (t.get("pnl") or 0) > 0]
    loss_trades = [t for t in realized if (t.get("pnl") or 0) <= 0]
    wins = len(win_trades)
    avg_win = (sum(float(t["pnl"]) for t in win_trades) / wins) if wins else 0.0
    avg_loss = (sum(float(t["pnl"]) for t in loss_trades) / len(loss_trades)) if loss_trades else 0.0
    if avg_loss < 0 and avg_win > 0:
        payoff_ratio = round(avg_win / abs(avg_loss), 2)
    elif not loss_trades and wins:
        payoff_ratio = None
    else:
        payoff_ratio = 0.0 if realized else None
    total_pnl = sum(float(t.get("pnl") or 0) for t in realized)
    if unrealized:
        total_pnl += float(unrealized.get("pnl") or 0)

    first_ts = bars[indexes[0]]["ts"]
    last_ts = bars[indexes[-1]]["ts"]
    return {
        "symbol": sym,
        "interval": tf,
        "join": join or "and",
        "indicators": [
            {"id": iid, "name": (CRYPTO_INDICATOR_MAP.get(iid) or {}).get("name") or iid}
            for iid in ids
        ],
        "bars_scanned": len(indexes),
        "hit_bars": hit_bars,
        "hit_rate": round(hit_bars / len(indexes), 4) if indexes else 0,
        "range": {
            "start": _asof_bar(first_ts, tf),
            "end": _asof_bar(last_ts, tf),
        },
        "warnings": warnings,
        "trades": trades + ([unrealized] if unrealized else []),
        "summary": {
            "n_trades": len(realized),
            "wins": wins,
            "win_rate": round(wins / len(realized), 4) if realized else None,
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "payoff_ratio": payoff_ratio,
            "total_pnl": round(total_pnl, 4),
            "final_equity": round(capital, 4),
            "open_position": bool(unrealized),
        },
        "signal_log_tail": signal_log[-80:],
        "asof": datetime.now(timezone.utc).isoformat(),
    }
