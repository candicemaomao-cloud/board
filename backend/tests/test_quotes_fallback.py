from app.services import quotes


def test_fetch_quotes_uses_yahoo_when_tradingview_is_empty(monkeypatch):
    monkeypatch.setattr(quotes, "fetch_tv_quotes", lambda _symbols: [])
    monkeypatch.setattr(
        quotes,
        "_fetch_one",
        lambda symbol, name: {
            "symbol": symbol,
            "name": name,
            "price": 123.45,
            "day_pct": 1.2,
            "week_pct": 2.3,
            "kind": "index",
            "ema5": 120.0,
            "ema10": 119.0,
            "ema20": 118.0,
        },
    )

    row = quotes.fetch_quotes([("USDJPY", "美元/日元")])[0]

    assert row["price"] == 123.45
    assert row["symbol"] == "USDJPY"
    assert row["requested"] == "USDJPY"
    assert row["source"] == "yahoo"


def test_fetch_quotes_retries_tradingview_before_yahoo(monkeypatch):
    calls = 0

    def flaky_tradingview(_symbols):
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        return [{"symbol": "SPY", "requested": "SPY", "price": 501.0, "source": "tradingview"}]

    monkeypatch.setattr(quotes, "fetch_tv_quotes", flaky_tradingview)
    monkeypatch.setattr(
        quotes,
        "_fetch_one",
        lambda _symbol, _name: (_ for _ in ()).throw(AssertionError("Yahoo should not run after TV retry succeeds")),
    )

    row = quotes.fetch_quotes([("SPY", "标普")])[0]

    assert calls == 2
    assert row["price"] == 501.0
    assert row["source"] == "tradingview"


def test_fetch_quotes_keeps_valid_tradingview_row(monkeypatch):
    tv_row = {"symbol": "SPY", "requested": "SPY", "price": 500.0, "source": "tradingview"}
    monkeypatch.setattr(quotes, "fetch_tv_quotes", lambda _symbols: [tv_row])

    def unexpected_fallback(_symbol, _name):
        raise AssertionError("valid TradingView quotes must not call Yahoo")

    monkeypatch.setattr(quotes, "_fetch_one", unexpected_fallback)

    assert quotes.fetch_quotes([("SPY", "标普")]) == [tv_row]
