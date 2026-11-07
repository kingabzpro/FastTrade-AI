# Changelog

## Unreleased

Nothing yet.

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
