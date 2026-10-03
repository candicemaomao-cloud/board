# Changelog

All notable user-facing changes are recorded here. Versions follow Semantic
Versioning, and dates use Asia/Manila calendar dates.

## [1.1.0-beta.3] - 2026-10-04

### Added

- Sticky Binance live-price header with 24-hour change and five-second refresh.
- Separate pullback-entry and confirmed-breakout strategy paths with entry,
  exit, invalidation, and candle-confirmation conditions.
- Strategy evidence table showing each information source, connection status,
  and whether it participates in the composite score.
- Explicit macro-filter roadmap for Nasdaq, oil, rates, and yen data without
  treating unavailable feeds as live evidence.

### Fixed

- Ensure the breakout take-profit remains above its confirmation entry when the
  recent swing high already exceeds the historical median target.
- Identify and replace stale NAS frontend images by verifying the served asset
  hash after each rebuild.

## [1.1.0-beta.2] - 2026-10-03

### Added

- Capital-aware decision support below the historical outcome distribution.
- Reference entry range, median-based take-profit and stop prices, expected return,
  risk/reward ratio, break-even probability, and one-percent account risk sizing.
- Safety gates that keep the recommendation at observation when sample size,
  direction, expected value, price, or risk/reward requirements are not met.
- One-click transfer of an actionable plan into the spot paper-trading form.

### Fixed

- Prevent narrow screens from expanding to desktop content width.

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
