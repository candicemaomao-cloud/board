# Changelog

All notable user-facing changes are recorded here. Versions follow Semantic
Versioning, and dates use Asia/Manila calendar dates.

## [1.1.0] - 2026-10-04

### Added

- Crypto dashboard with market state, account capital, watched assets, strategy
  radar, algorithm evidence, review progress, and monthly success statistics.
- Indicator Strategy workspace with BTC, ETH, OP, and PEOPLE defaults, expert
  monitoring presets, three independent short/medium/long pattern windows, and
  Telegram delivery to the configured household group.
- Immutable strategy-hit records containing the trigger snapshot, indicator
  explanations, three-window evidence, decision levels, outcome ranges, and a
  three-day real-market review.
- Historical-path forecasts for maximum upside, maximum downside, final-return
  range, likely duration, sample count, and window agreement.
- One-field fuzzy coin search. Selecting a result now adds it immediately while
  symbol, CoinGecko id, and spot pair are resolved automatically.

### Changed

- Reorganized navigation into stock, crypto, and shared modules, with direct
  access to the crypto dashboard, coin market, patterns, news, and strategies.
- Telegram alerts now explain each triggered indicator and include the monitor,
  historical-pattern, risk, sample, price-range, and review context instead of
  presenting a raw checklist as a recommendation.
- Renamed the ambiguous dashboard state from data error to strategy calculation
  error and expose the underlying quote, candle, indicator, pattern, or push
  failure reason.

### Reliability

- Strategy probabilities are not multiplied across correlated windows. Window
  agreement is treated as supporting evidence and remains gated by sample size,
  price position, expected value, and risk limits.
- New signal records are frozen at creation and later appended with observed
  market outcomes; older records are explicitly marked when full path evidence
  was not historically captured.
- Added focused tests for three-window review, record persistence, review
  outcomes, and complete Telegram strategy reports.

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
