# Changelog

User-facing changes are recorded under Unreleased. At release, move them under the new version and date, update the package and API versions together, and tag the release. During 0.x, document incompatible changes and migration steps explicitly.

## Unreleased

- Added a bounded exact-cutoff market cache and a validated-candle indicator cache, both per process.
- Added a non-root container, a loopback dashboard proxy, and a separate VPN-oriented HTTPS API proxy profile.
- Added structured request logs and response request IDs.
- Added provider-reported individual-analysis tokens to per-key usage totals, with older or unavailable records marked incomplete.
- Added authenticated, rate-limited versioned API endpoints, usage reservations, batch jobs, and liveness/readiness checks.
- Added selectable market and decision providers, optional local history, and probability calibration research tooling.

## 0.1.0

Initial tagged research release.
