# Changelog

## Unreleased

- The dashboard accepts up to five comma-separated tickers per run, shows each result in its own tab, and offers suggested ticker chips under the symbol field.
- Added a browser-local paper trading block: simulated BUY and SELL fills at the analyzed last completed close, with SELL enabled only while the paper portfolio holds the shares.
- Added a Portfolio page (paper cash, positions, fills, realized P&L) next to the History page, and colored BUY/HOLD/SELL badges across results, tabs and history rows.
- Added Bollinger Bands to the chart overlays, with upper and lower bands, a dashed middle average, and a shaded band area.
- Added CSV export for analysis history and simulated paper fills.
- Added unrealized P&L, total P&L, and return against starting cash to the Portfolio page.
- Fixed the phone card order so paper trading follows the decision, chart, indicators and evidence cards instead of appearing first.
- Rebuilt the Portfolio page around the Alpaca paper trading account: cash, buying power, equity and positions come from Alpaca, and manual BUY/SELL submits real paper market orders at the latest Alpaca trade price.
- Added automated paper trading: an opt-in toggle that every five minutes picks the top gainer with a market cap of $1B or more, runs one decision on it, and submits BUY, SELL or HOLD as a paper order, with an activity log and a Run now button.
- Added an interactive equity history chart to the Portfolio page, built from account snapshots stored in the browser, with hover and keyboard inspection.

## 0.2.0 (2026-11-07)

First public release.

### Breaking

- Backtests now fill with estimated square-root impact instead of a flat slippage rate. Pass `--slippage-model flat` to keep the old model. Impact reports set `slippage_bps` to null and expose the flat comparison rate separately as `flat_slippage_bps`; read `slippage_model` and the per-fill impact details instead. Impact mode needs prior liquidity history, and the command checks for it before it makes any model call.
- Calibration splits and edge intervals were wrong for weekly data and have been corrected. Artifacts without outcome-availability timestamps must be regenerated before use.

### Added

- A versioned API under `/v1`: `X-API-Key` authentication, hashed keys, per-key rate limits, request caps and daily budgets, plus `/healthz` and `/readyz`.
- Batch analysis: `ft analyze-batch`, `POST /v1/analyze:batch`, background jobs, idempotency keys and per-key token accounting.
- Selectable market data (Alpaca IEX, Yahoo Finance) and decision providers (Jev, Liquid, vLLM-SR, SubQ, Baseten).
- Bounded retries with `Retry-After` handling, per-credential circuit breakers, and market and indicator caches keyed by source cutoff.
- A non-root container image, a loopback dashboard proxy, and a separate private-network profile for the HTTPS API.
- JSON request logs with request ids, and provider-reported token counts in the per-key usage totals.
- Research tooling: probability calibration, purged folds, the White reality check, the deflated Sharpe ratio and a committed variant registry.

### Changed

- The public README was rewritten: banner, badges, an honest benchmark table and a Docker quick start.
- Package metadata now points at the repository and the issue tracker, and internal planning notes were removed from `docs/`.

## 0.1.0 (2026-10-28)

Initial tagged research release.
