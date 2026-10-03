# Changelog

All notable user-facing changes are recorded here. Versions follow Semantic
Versioning, and dates use Asia/Manila calendar dates.

## [1.1.0-beta.1] - 2026-10-03

### Added

- Historical crypto K-line slice analysis across OP, BTC, ETH, SOL, and XRP.
- Exact and similarity matching with real post-match candles and outcome ranges.
- Minute, hourly, daily, weekly, and monthly analysis periods.
- Saved pattern studies with later real-market outcome review.
- Spot paper trading with buy, partial/full sell, fees, holdings, and realized P&L.
- Separate retained leverage simulation controls.
- News, market breadth, price structure, volume psychology, and tail-risk factors
  in the composite market behavior index.

### Fixed

- Recreate ECharts instances after leaving and returning to the historical-pattern page.
- Recreate the probability chart after its result container is remounted.
- Prefer Binance spot candles so chart colors and OHLC data match the Binance spot app.
- Prevent stale entry HTML from hiding newly deployed hashed frontend assets.
