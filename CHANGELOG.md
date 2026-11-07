# Changelog

User-facing changes are recorded under Unreleased. At release, move them under the new version and date, update the package and API versions together, and tag the release. During 0.x, document incompatible changes and migration steps explicitly.

## Unreleased

Nothing yet.

## 0.2.0 (2026-11-07)

- **Incompatible:** backtests now use estimated square-root impact for primary fills. Use `--slippage-model flat` to retain the previous flat model. Impact reports set `slippage_bps` to null and expose the flat comparison rate as `flat_slippage_bps`; consumers should inspect `slippage_model` and per-fill impact details. Impact mode requires prior liquidity history before model calls.
- Corrected calibration splits and edge intervals to preserve weekly dependence; legacy artifacts without outcome-availability times must be regenerated before use.
- Added bounded Alpaca and decision-provider retries, `Retry-After` handling, and per-credential circuit breakers.
- Added a bounded exact-cutoff market cache and a validated-candle indicator cache, both per process.
- Added a non-root container, a loopback dashboard proxy, and a separate VPN-oriented HTTPS API proxy profile.
- Added structured request logs and response request IDs.
- Added provider-reported individual-analysis tokens to per-key usage totals, with older or unavailable records marked incomplete.
- Added authenticated, rate-limited versioned API endpoints, usage reservations, batch jobs, and liveness/readiness checks.
- Added selectable market and decision providers, optional local history, and probability calibration research tooling.
- Added purged-fold, reality-check, deflated-Sharpe and registry-backed overfitting statistics to published research results.
- Refreshed the public README: new dashboard banner, CI and license badges, a Docker quick start, and links to deployment, security and changelog docs.
- Added package metadata (readme, repository and issue URLs) and removed internal planning notes from `docs/`.

## 0.1.0

Initial tagged research release.
