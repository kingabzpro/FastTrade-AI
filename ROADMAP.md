# FastTrade AI roadmap

**Status:** research tool, v0.1.0. Today it runs as a local, single-process web app. This roadmap describes how it grows into a production-grade signal service that other applications call.

Last updated: 2026-10-30.

> **Scope, said once and meant everywhere:** FastTrade AI is research tooling. Production-ready here means one thing: a production-grade **signal service** that teams self-host and consume from their own applications. It does not mean live trading. No phase in this roadmap adds order execution, broker connectivity, or live trading. The service emits model preferences and risk-checked signals; it never places orders.

## Vision

FastTrade AI becomes a signal service that teams can self-host and call from their own applications: secure, fast, batch-capable, and still signal-only. You send a symbol and your portfolio limits; you get a model preference, the risk-checked verdict, and the cost of the request. It never places orders. That constraint is a feature, not a missing feature: deterministic code stays responsible for money math, and the model stays an advisor.

## Guiding principles

- **Signal-only forever.** No phase connects to a broker or executes an order. The service answers questions about the market; it does not act on them.
- **Every request costs money.** Each analysis is a paid model call, so cost controls are a core feature, not an afterthought: rate limits, spend caps, caching, pre-flight cost estimates, token accounting.
- **Secure by default.** Bind to loopback until deliberately opened, and open only behind authentication.
- **Deterministic code owns money math and limits.** The risk engine decides whether a preference is actionable; models only express preferences.
- **Honest measurement.** Published results include losses, failures, and caveats. A model preference is not a probability of profitable trading.
- **Boring dependencies.** FastAPI, uvicorn, Redis when multi-worker state demands it. No orchestration framework for its own sake.

## Where we are (Phase 0, shipped)

Capabilities today:

- Python 3.10+, uv-managed `fasttrade` package, `ft` CLI (`dashboard`, `analyze`, `benchmark`, `backtest`).
- FastAPI + uvicorn app bound to 127.0.0.1:8765 only; middleware rejects non-loopback hostnames and mismatched origins; sets `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.
- `GET /` dashboard, `GET /api/health` (status ok, mode signal-only, checks no upstreams), `POST /api/analyze` (one symbol, 1m-90m timeframes, optional spread/liquidity, 11 optional portfolio/risk fields, Pydantic `extra=forbid`).
- Every analysis makes one paid model request to a System One decision endpoint (TypeSafe default; bearer key from `.env`).
- Deterministic risk engine (`fasttrade/risk.py`) with 11 portfolio limits; an incomplete portfolio means signal only (`actionable_signal` null).
- Backtest on a frozen, SHA-256-checksummed dataset with next-open fills, fees and slippage in bps, and dividend receivables; weekly benchmark against SPY.
- Data from yfinance (unofficial) or user CSV (tz-aware, 50+ completed bars).
- Indicators: EMA 9/21/50, RSI 14, MACD 12/26/9, session VWAP, ATR 14, volume ratio.
- pytest suite plus node chart tests.

Missing for production, plainly:

- No auth, no rate limits, no per-key cost controls on the API.
- Localhost-only binding, single process, in-memory session history, no caching layer.
- No CI, no container, no observability beyond local logs.
- Version 0.1.0.

## Phase 1: Secure multi-user API

**Goal:** other applications can call it safely.

- Configurable bind host and port, with a loud warning when the host is not loopback.
- API key auth: `X-API-Key` header, keys stored hashed (SHA-256 is fine for high-entropy keys), per-key metadata (tier, rate limit, spend cap), and a rotation grace window.
- The service proxies the paid model call; the model bearer key never reaches a client.
- Rate limiting per key with slowapi. In-memory storage is per-process, so multiple workers need Redis storage or proxy-level enforcement.
- CORS with `allow_credentials=False` and an explicit allowlist; never `*` with auth.
- Version the surface as `/v1`; keep `/api/analyze` working as the local unversioned alias.
- Split health checks: `/healthz` for liveness (touches no upstreams) and `/readyz` for readiness (config valid, optional upstream reachability), separate from today's `/api/health`.
- Keep nosniff and no-store; add `X-Frame-Options`. HSTS belongs at the proxy, not in Python.
- Option to disable OpenAPI docs in production, the freqtrade pattern ([freqtrade REST API docs](https://docs.freqtrade.io/en/stable/rest-api/)).
- Document secrets management beyond `.env`: `.env` is for development only; production uses a secret manager; secrets are never baked into images.
- Consumers call the HTTP API; document a thin client as the integration model rather than encouraging imports of app internals (freqtrade ships one).

The relevant OWASP API Security Top 10 (2023) items: API4 unrestricted resource consumption (every request costs money; rate limits and spend caps are the mitigation), API9 improper inventory (version paths; keep benchmark and debug endpoints off the public surface), API10 unsafe consumption of APIs (validate upstream data and model output) ([OWASP API Security Top 10](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)).

Acceptance criteria:

- [ ] An unauthenticated request to any `/v1` endpoint returns 401.
- [ ] A per-key rate limit holds across multiple workers (Redis storage or proxy enforcement).
- [ ] Key storage is hashed; rotation keeps the old key valid for a configured grace window.
- [ ] Non-allowlisted CORS origins are rejected; `allow_credentials` is False everywhere.
- [ ] `/healthz` touches no upstreams; `/readyz` reports config and upstream status.
- [ ] OpenAPI docs are hidden when disabled by configuration.
- [ ] Binding to a non-loopback host prints a warning that names the risk.
- [ ] A key past its rotation grace window is rejected.

## Phase 2: Performance

**Goal:** fast under real load.

- Move the Yahoo fetch and the model call to async `httpx`. Today's paths are synchronous under async endpoints, so they block the event loop.
- Run uvicorn with 2-4 workers behind a reverse proxy; that is plenty for an IO-bound service. Gunicorn is no longer recommended by the FastAPI deployment docs ([FastAPI Docker deployment](https://fastapi.tiangolo.com/deployment/docker)).
- TTL cache for candle fetches and indicator computation, keyed on `(symbol, timeframe, as_of)`, TTL matched to the timeframe (5-15 minutes for 1h candles). `cachetools.TTLCache` per worker first; Redis when workers must share.
- Upstream resilience with tenacity: exponential backoff with jitter, honor `Retry-After` on 429, retry budget of about 3 attempts, circuit breaker to fail fast during upstream incidents.
- Graceful shutdown: `--timeout-graceful-shutdown` above the 30s model timeout, so in-flight analyses are not killed on deploy.
- Measure and publish p50/p95 latency. Token accounting beats request counting: failed model requests consume no tokens.

Acceptance criteria:

- [ ] Concurrent load no longer stalls the event loop (candle fetches and model calls are async).
- [ ] p50 and p95 latency are measured and published in the docs.
- [ ] A cache hit skips the upstream fetch; the cache key includes `as_of`, so stale data is never served as fresh.
- [ ] 429 responses honor `Retry-After`; retries stay within budget; repeated failures open the circuit.
- [ ] SIGTERM during in-flight analyses lets them finish within the graceful window.

## Phase 3: Batch processing

**Goal:** one request, many symbols, predictable cost.

- `POST /v1/analyze:batch`, synchronous path for small batches (under ~10 symbols): `asyncio.Semaphore(2-5)` around model calls, per-symbol timeout, partial-success response shape (per-symbol result or error).
- Async jobs for larger batches: `202` with a job id, `GET /v1/jobs/{id}` returning status, progress, and partial results. FastAPI BackgroundTasks first; arq (Redis) when durability across restarts matters.
- `Idempotency-Key` on batch POSTs: store key, request hash, and response; replay on retry. Retries must be safe when they cost money; this is the Stripe pattern ([Stripe idempotency](https://stripe.com/blog/idempotency)).
- Pre-flight cost estimate (symbols x cost per call); reject requests over a per-request cap or a per-key daily budget with an explicit error.
- Batch size cap in the Pydantic schema.
- Token-based accounting, not request counting.

Batch is also the infrastructure the multi-agent pipeline reuses: fan-out with bounded concurrency, per-call timeouts, partial results, and job tracking are exactly what Phase 5 needs.

Acceptance criteria:

- [ ] A batch of N symbols returns N outcomes, each a result or an explicit error; one failure does not fail the batch.
- [ ] Retrying a batch with the same `Idempotency-Key` replays the stored response instead of charging again.
- [ ] A cost estimate is available before the call; over-cap requests are rejected with a clear error.
- [ ] A batch above the schema cap returns 422.
- [ ] `GET /v1/jobs/{id}` reports status and partial results; with arq, jobs survive a restart.
- [ ] Provider-reported token usage is recorded per request, per key, and per batch.

## Phase 4: Deploy anywhere

**Goal:** production deployment is a documented path, not an adventure.

- Dockerfile: python slim base, non-root `USER`, `HEALTHCHECK`, copy the dependency manifest first for layer caching, run uvicorn with `--proxy-headers` and `--forwarded-allow-ips` behind a proxy ([FastAPI Docker deployment](https://fastapi.tiangolo.com/deployment/docker)).
- A docker-compose example that wires app and proxy together.
- Reverse proxy guide: Caddy with automatic Let's Encrypt as the worked example; nginx and Traefik notes. TLS terminates at the proxy, never in Python.
- Private-network-first deployment guidance: a VPN or Tailscale address before any public exposure, following the hummingbot precedent. Explicit warning against direct public exposure without auth; freqtrade and hummingbot both default to loopback and warn against it.
- Observability: structured logs with request IDs, per-key usage and cost tracking, optional metrics endpoint.
- CI on GitHub Actions: pytest and node chart tests against the `uv.lock`.
- `SECURITY.md` with a private reporting channel.
- Versioning and changelog discipline through 0.x.
- Default exposure stays loopback even in containers; document the exact configuration that changes bind host, port, and CORS.

Acceptance criteria:

- [ ] `docker compose up` serves the app; the healthcheck passes; the container runs as non-root.
- [ ] Rebuilding after a source-only change does not reinstall dependencies.
- [ ] The Caddy guide produces a working HTTPS deployment on a private network.
- [ ] Every response has a request ID in the logs; per-key cost is queryable.
- [ ] CI runs pytest and node chart tests on every push.
- [ ] `SECURITY.md` names a private reporting channel.

## Phase 5: Multi-agent decisions

**Goal:** richer judgments, bounded cost.

The approved specification is [docs/superpowers/plans/2026-10-28-multi-agent-trading-research.md](docs/superpowers/plans/2026-10-28-multi-agent-trading-research.md); this section summarizes its shape and adds deployment-side findings, nothing that contradicts it. The shape: an immutable point-in-time evidence snapshot, a parallel specialist stage, a manager, a bull/bear debate, and a judge whose INSUFFICIENT is distinct from HOLD. The existing deterministic risk engine stays the final gate. Opt-in only, and never an order.

What the research adds beyond that spec:

- **TypeSafe fan-out.** Pack many typed questions into one System One call. TypeSafe's cookbook measures 13 questions in one call at 11.5x cheaper and 9.6x faster than separate calls ([fan-out pattern](https://docs.typesafe.ai/patterns/fan-out)). The spec already permits batching questions that share a state; this makes it the default.
- **Confidence-gated escalation.** Confidence arrives on every typed answer ([TypeSafe confidence](https://docs.typesafe.ai/confidence)); low confidence can escalate to a deeper model tier. This mirrors the TradingAgents two-tier config, cheap models for breadth and a deeper model for final judgment ([TradingAgents paper](https://arxiv.org/abs/2412.20138)). Confidence is a cost lever, not proof of correctness.
- **A max-rounds cap on the debate.** The spec fixes a single bounded pass; an explicit cap keeps that bound visible and configurable.
- **Per-stage timeouts plus an overall pipeline deadline,** so a stalled stage cannot hold a request open.

Dependency: Phase 5 requires Phase 2 caching and Phase 3 batch/job infrastructure. The pipeline multiplies model calls per analysis, and it reuses the batch fan-out, job tracking, and cost controls directly.

The spec's evaluation discipline stands: compare against an equal-cost single-agent baseline before enabling by default.

Acceptance criteria:

- [ ] Cost per multi-agent analysis is measured against the single-agent baseline at equal cost before any default changes.
- [ ] Debate rounds are capped; per-stage timeouts and the overall deadline are enforced and visible in results.
- [ ] The pipeline is opt-in only; the default single-agent path is unchanged.
- [ ] INSUFFICIENT never becomes an automatic HOLD or a risk-checked action.
- [ ] No endpoint or code path places an order.

## Research backlog: improving the signal

Phases 1-5 make the service trustworthy to operate; this backlog makes the signal worth operating. Each item is an experiment backed by verified papers, filed as an issue with tasks and acceptance criteria, and measured on the frozen backtest at equal cost before anything becomes a default. Nothing here changes the signal-only scope.

| Issue | Idea | Key sources |
| --- | --- | --- |
| [#6](https://github.com/kingabzpro/FastTrade-AI/issues/6) | Calibrate the model's probabilities: temperature/isotonic post-hoc, conformal gating | [Just Ask for Calibration](https://arxiv.org/abs/2305.14975), [Confidence Elicitation](https://arxiv.org/abs/2306.13063) |
| [#7](https://github.com/kingabzpro/FastTrade-AI/issues/7) | Self-consistency sampling: N votes per decision, dispersion as a signal | [Self-Consistency](https://arxiv.org/abs/2203.11171), [Should we be going MAD?](https://arxiv.org/abs/2311.17371) |
| [#8](https://github.com/kingabzpro/FastTrade-AI/issues/8) | Leakage-adjusted scoring: cutoff boundary probe, one-switch context ablation | [Temporal Leakage in LLM Backtesting](https://arxiv.org/abs/2608.02985), [Look-Ahead-Bench](https://arxiv.org/abs/2601.13770) |
| [#9](https://github.com/kingabzpro/FastTrade-AI/issues/9) | Statistical significance vs SPY: stationary bootstrap CIs, Hansen SPA, BH-FDR | [The Stationary Bootstrap](https://www.tandfonline.com/doi/abs/10.1080/01621459.1994.10476870), [Hansen SPA](https://www.tandfonline.com/doi/abs/10.1198/073500105000000063) |
| [#10](https://github.com/kingabzpro/FastTrade-AI/issues/10) | Overfitting statistics: CSCV/PBO, deflated Sharpe, variant registry, walk-forward | [The Probability of Backtest Overfitting](https://scholarworks.wmich.edu/math_pubs/42), [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) |
| [#11](https://github.com/kingabzpro/FastTrade-AI/issues/11) | Position sizing overlays and impact-based costs, simulation only | [Volatility-Managed Portfolios](https://www.nber.org/papers/w22208), [Square-root market impact](https://www.cis.upenn.edu/~mkearns/finread/costestim.pdf) |
| [#12](https://github.com/kingabzpro/FastTrade-AI/issues/12) | Retrieval-augmented evidence: headline scores, analogous-regime retrieval | [Can ChatGPT Forecast Stock Price Movements?](https://arxiv.org/abs/2304.07619), [FinTRM](https://arxiv.org/abs/2310.04027) |
| [#13](https://github.com/kingabzpro/FastTrade-AI/issues/13) | A time-series foundation model as one typed specialist input | [Chronos](https://arxiv.org/abs/2403.07815), [TimesFM](https://arxiv.org/abs/2310.10688) |
| [#14](https://github.com/kingabzpro/FastTrade-AI/issues/14) | Layered memory and risk personas for the agent pipeline (blocked by Phase 5) | [FinMem](https://arxiv.org/abs/2311.13743), [FinAgent](https://arxiv.org/abs/2402.18485) |

Standing rules for every item: measure on the frozen backtest at equal cost, publish the result either way, and change no default until the comparison says so. The dependencies chain deliberately: calibration (#6) gates the Phase 5 escalation path, dispersion from self-consistency (#7) feeds calibration, and the leakage (#8) and significance (#9) tooling protects every other number in this table.

## Non-goals (unchanged)

- **Order execution.** Never, in any phase.
- **Broker connectivity.** Never, in any phase.
- **Live trading.** Never, in any phase.
- **Promises of profit.** FastTrade AI is research tooling; signals are model preferences, not probabilities of profitable trades.

## Feedback

Open an issue arguing priorities, especially which phase matters most to you. If you already run FastTrade AI behind other software, say what blocks you: it decides the order of these phases.
