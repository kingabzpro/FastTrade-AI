# FastTrade AI roadmap

FastTrade reads market data, asks a decision model for a BUY, HOLD or SELL preference, runs that preference past the deterministic risk engine, and shows the result. It never places an order, and nothing planned here changes that.

Last updated: 2026-11-07.

## Where the project is now

Version 0.2.0 is the first public release. The four phases below have shipped, so this page mostly records what is done and what is still open.

### Secure API (done)

The `/v1` endpoints need an `X-API-Key`. Keys are stored as SHA-256 hashes, never in plaintext. Each key carries its own rate limit, per-request cap and daily spend cap, enforced against Redis so the limits hold across workers. `/healthz` is liveness; `/readyz` also checks configuration and Redis. CORS takes an exact origin allowlist and never allows credentials, and the OpenAPI pages can be switched off. The dashboard and the older `/api` routes stay bound to loopback.

### Performance (done)

The decision pipeline runs in a worker thread, so a slow model call no longer blocks the event loop. Market snapshots and indicator results are cached per process, keyed by source cutoff, symbol, timeframe, provider and credential fingerprint. Upstream calls retry with bounded jitter, respect `Retry-After`, and trip a per-credential circuit breaker during an outage. Deployments drain in-flight requests instead of cutting them off. Median decision time is published with the benchmark.

### Batch (done)

`ft analyze-batch` and `POST /v1/analyze:batch` take up to 50 tickers with bounded concurrency. Small submissions answer straight away, larger ones come back as a job you can poll. Every symbol gets a result or an explicit error, so one bad symbol does not sink the batch. Submissions can carry an `Idempotency-Key`, and each request reserves an estimated cost before the model is called.

### Deployment (done)

There is a non-root Docker image, a Compose setup that keeps the dashboard on loopback behind Caddy, a separate profile for serving `/v1` over a private network, CI on every push, JSON request logs with request ids, and a private reporting channel in [SECURITY.md](SECURITY.md).

## What is next

Nothing is committed to a schedule. The useful next steps, roughly in order:

- A longer frozen dataset. The published intervals rest on 3 to 25 weekly periods, which is too thin to say much.
- A head-to-head run of the second decision model on the same frozen data, so calibration and comparison stop being Jev-only.
- Point-in-time prices with an independently verified corporate-action history. Most of the caveats in the benchmark docs trace back to this.

The research backlog behind the earlier phases is closed. Historical research files are kept locally and are not included in the repository.

Open an issue if something above is missing, or if you would rather work on something else. Priorities follow what people actually use.

## Non-goals

- Order execution. Never.
- Broker connectivity. Never.
- Live trading. Never.
- Claims of profit. Signals are model preferences, not probabilities of profitable trades.

## Feedback

Open an issue arguing priorities, especially which item above matters most to you. If you already run FastTrade behind other software, say what gets in the way: that decides the order of the list.
