from app.services.crypto_pattern_review import build_three_window_review


def _bars(scale=1.0, count=120):
    rows = []
    price = 100.0 * scale
    for index in range(count):
        change = (0.012 if index % 9 < 6 else -0.006) * (1 + (index % 5) * 0.03)
        open_price = price
        price *= 1 + change
        rows.append({
            "ts": 1_700_000_000 + index * 86400,
            "open": open_price,
            "high": max(open_price, price) * 1.005,
            "low": min(open_price, price) * 0.995,
            "close": price,
            "volume": 1000 + index,
            "market": "test_spot",
            "closed": True,
        })
    return rows


def test_three_window_review_exposes_auditable_evidence():
    pools = {
        "OPUSDT": _bars(1.0),
        "BTCUSDT": _bars(2.0),
        "ETHUSDT": _bars(1.5),
        "SOLUSDT": _bars(0.8),
        "XRPUSDT": _bars(0.5),
    }

    result = build_three_window_review("OPUSDT", pools=pools, horizon=7)

    assert [row["bars"] for row in result["windows"]] == [7, 14, 30]
    assert all(row["samples"] == 12 for row in result["windows"])
    assert all(row["source_count"] >= 2 for row in result["windows"])
    assert result["consensus"]["agreement"] >= 2
    assert result["consensus"]["ready"] is True
    assert result["algorithm"] == "three-window-shape-v2"
    assert result["forecast"]["horizon_bars"] == 7
    assert result["forecast"]["sample_count"] >= 24
    assert result["forecast"]["upside_probe_pct"]["high"] >= result["forecast"]["upside_probe_pct"]["low"]
    assert result["forecast"]["downside_probe_pct"]["high"] >= result["forecast"]["downside_probe_pct"]["low"]
    assert result["forecast"]["duration_bars"]["low"] >= 1
    assert len(result["evidence_id"]) == 12
