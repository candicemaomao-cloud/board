"""Server-side short/medium/long pattern review for strategy notifications."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from datetime import datetime, timezone

from app.services.crypto_market import klines


DEFAULT_WINDOWS = (("short", "短线", 7, 0.25), ("medium", "中期", 14, 0.35), ("long", "长线", 30, 0.40))
DEFAULT_SAMPLES = ("OPUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


def _percentile(values: list[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _range(values: list[float], digits: int = 2) -> dict:
    low = _percentile(values, 0.25)
    median = _percentile(values, 0.50)
    high = _percentile(values, 0.75)
    return {
        "low": round(low, digits) if low is not None else None,
        "median": round(median, digits) if median is not None else None,
        "high": round(high, digits) if high is not None else None,
    }


def _normalized(source: list[dict]) -> list[list[float]]:
    base = float(source[0].get("close") or 1)
    return [
        [(float(bar.get(field) or 0) / base) - 1 for field in ("open", "high", "low", "close")]
        for bar in source
    ]


def _similarity(left_bars: list[dict], right_bars: list[dict]) -> float:
    left = _normalized(left_bars)
    right = _normalized(right_bars)
    errors = [
        (left[index][field] - right[index][field]) ** 2
        for index in range(len(left))
        for field in range(4)
    ]
    rmse = math.sqrt(sum(errors) / max(1, len(errors)))
    return max(0.0, 100.0 - rmse * 900.0)


def _window_cases(
    target: list[dict], pools: dict[str, list[dict]], current_symbol: str, horizon: int
) -> list[dict]:
    length = len(target)
    target_start = int(target[0]["ts"])
    target_end = int(target[-1]["ts"])
    candidates = []
    for symbol, bars in pools.items():
        for index in range(0, len(bars) - length - horizon + 1):
            segment = bars[index : index + length]
            if (
                symbol == current_symbol
                and int(segment[0]["ts"]) <= target_end
                and int(segment[-1]["ts"]) >= target_start
            ):
                continue
            candidates.append((_similarity(target, segment), symbol, index, segment, bars))
    candidates.sort(key=lambda item: item[0], reverse=True)
    kept = []
    for score, symbol, index, segment, bars in candidates:
        if any(
            item["symbol"] == symbol and abs(item["index"] - index) < max(1, length // 2)
            for item in kept
        ):
            continue
        future = bars[index + length : index + length + horizon]
        base = float(segment[-1]["close"])
        close_path = [(float(bar["close"]) / base - 1) * 100 for bar in future]
        high_path = [(float(bar["high"]) / base - 1) * 100 for bar in future]
        low_path = [(float(bar["low"]) / base - 1) * 100 for bar in future]
        max_upside = max(high_path)
        max_downside = min(low_path)
        kept.append({
            "symbol": symbol,
            "index": index,
            "score": round(score, 2),
            "outcome": close_path[-1],
            "max_upside": max_upside,
            "max_downside": max_downside,
            "peak_bar": high_path.index(max_upside) + 1,
            "trough_bar": low_path.index(max_downside) + 1,
        })
        if len(kept) >= 12:
            break
    return kept


def build_three_window_review(
    symbol: str,
    *,
    pools: dict[str, list[dict]] | None = None,
    horizon: int = 7,
) -> dict:
    """Return an auditable 7/14/30-day review; callers may inject bars in tests."""
    current_symbol = symbol.upper()
    symbols = list(dict.fromkeys((current_symbol, *DEFAULT_SAMPLES)))
    if pools is None:
        pools = {}
        errors = {}
        for item in symbols:
            for attempt in range(2):
                try:
                    bars = [bar for bar in klines(item, interval="1d", limit=1000) if bar.get("closed", True)]
                    if bars:
                        pools[item] = bars
                        break
                except Exception as exc:  # noqa: BLE001
                    errors[item] = str(exc)
                if attempt == 0:
                    time.sleep(0.35)
        if current_symbol not in pools:
            raise ValueError(errors.get(current_symbol) or "当前币种日 K 获取失败")
    else:
        pools = {key.upper(): value for key, value in pools.items()}
    current = pools.get(current_symbol) or []
    if len(current) < 30:
        raise ValueError("当前币种日 K 不足 30 根，无法完成三阶段复核")

    windows = []
    for key, label, length, weight in DEFAULT_WINDOWS:
        target = current[-length:]
        cases = _window_cases(target, pools, current_symbol, horizon)
        up = sum(item["outcome"] > 1 for item in cases) / max(1, len(cases)) * 100
        down = sum(item["outcome"] < -1 for item in cases) / max(1, len(cases)) * 100
        flat = 100 - up - down if cases else 0
        direction = "看涨" if up >= down + 8 else "看跌" if down >= up + 8 else "震荡"
        source_count = len({item["symbol"] for item in cases})
        duration_key = "peak_bar" if direction == "看涨" else "trough_bar" if direction == "看跌" else None
        duration_values = [item[duration_key] for item in cases] if duration_key else [min(item["peak_bar"], item["trough_bar"]) for item in cases]
        windows.append({
            "key": key,
            "label": label,
            "bars": length,
            "horizon": horizon,
            "samples": len(cases),
            "source_count": source_count,
            "up": round(up, 1),
            "flat": round(flat, 1),
            "down": round(down, 1),
            "direction": direction,
            "score": round(max(0, min(100, 50 + (up - down) * 0.5))),
            "weight": weight,
            "path": {
                "final_return_pct": _range([item["outcome"] for item in cases]),
                "upside_probe_pct": _range([item["max_upside"] for item in cases]),
                "downside_probe_pct": _range([item["max_downside"] for item in cases]),
                "duration_bars": _range(duration_values, 0),
            },
            "case_paths": [
                {
                    "symbol": item["symbol"], "index": item["index"], "score": item["score"],
                    "final_return_pct": round(item["outcome"], 2),
                    "upside_probe_pct": round(item["max_upside"], 2),
                    "downside_probe_pct": round(item["max_downside"], 2),
                    "duration_bars": item[duration_key] if duration_key else min(item["peak_bar"], item["trough_bar"]),
                }
                for item in cases
            ],
        })

    counts = Counter(item["direction"] for item in windows)
    direction, agreement = counts.most_common(1)[0]
    score = round(sum(item["score"] * item["weight"] for item in windows))
    ready = agreement >= 2 and all(item["samples"] >= 8 and item["source_count"] >= 2 for item in windows)
    aligned = [item for item in windows if item["direction"] == direction]
    forecast_windows = aligned if len(aligned) >= 2 else windows
    forecast_cases = [case for item in forecast_windows for case in item["case_paths"]]
    forecast = {
        "basis": "matched-path-interquartile",
        "direction": direction,
        "interval": "1d",
        "horizon_bars": horizon,
        "sample_count": sum(item["samples"] for item in forecast_windows),
        "window_count": len(forecast_windows),
        "final_return_pct": _range([item["final_return_pct"] for item in forecast_cases]),
        "upside_probe_pct": _range([item["upside_probe_pct"] for item in forecast_cases]),
        "downside_probe_pct": _range([item["downside_probe_pct"] for item in forecast_cases]),
        "duration_bars": _range([item["duration_bars"] for item in forecast_cases], 0),
    }
    payload = {
        "algorithm": "three-window-shape-v2",
        "asof": datetime.now(timezone.utc).isoformat(),
        "symbol": current_symbol,
        "review_interval": "1d",
        "horizon": horizon,
        "sources": sorted(pools),
        "markets": sorted({str(bar.get("market") or "injected") for bars in pools.values() for bar in bars}),
        "windows": windows,
        "consensus": {"direction": direction, "agreement": agreement, "score": score, "ready": ready},
        "forecast": forecast,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["evidence_id"] = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return payload
