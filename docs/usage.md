# Usage reference

For the container and private HTTPS API setup, see [deployment](deployment.md).

## Configuration

Set `TYPESAFE_API_KEY` in `.env` or your environment. Alpaca data additionally requires `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`; Yahoo Finance does not. The defaults are `SYSTEMONE_URL=https://api.typesafe.ai/v1/systemone` and `SYSTEMONE_MODEL=jev-latest`; override them in `.env` to use a compatible provider. Only send credentials to an endpoint you trust. Git ignores `.env`.

The startup banner shows local links, the selected model, and the default data source. It does not check provider connectivity or display API-key details. Terminal colors are disabled when `NO_COLOR` is set or output is redirected.

Pandas is temporarily capped below 3.1 because the current yfinance quote parser uses an operation deprecated in pandas 3.1. NumPy is capped below 2.5 to avoid a separate compatibility warning in pandas 2.x.

The service exposes `/healthz` for process liveness and `/readyz` for readiness. Readiness validates model and API configuration and, when API keys are configured, checks Redis availability. It reports model-provider connectivity as `not_checked`; it does not verify API credentials. The existing `/api/health` remains available to local clients.

## Market data

The dashboard shows a provider selector only when at least two choices are configured. Yahoo Finance requires no credentials and is always available; Alpaca appears when both Alpaca credentials are set. Decision models appear when their API key is configured (or Jev has a custom SystemOne endpoint). If no decision provider is configured, add a key and reload the dashboard. Provider availability reflects configuration, not a connectivity or account-access check.

Select a decision provider in the dashboard or use `--decision-provider liquid`, `vllm`, `subq`, or `baseten` with `ft analyze` or `ft analyze-batch`. Jev remains the default. Configure `LIQUID_API_KEY`, `VLLM_SR_API_KEY`, `SUBQ_API_KEY`, or `BASETEN_API_KEY` on the server; keys are not sent to the browser. The selected models are [Liquid d1-ultra](https://www.liquid.ai/blog/d1-ultra), [vLLM-SR d3-ultra](https://decision.vllm-sr.ai/docs), [SubQ Snap 1.1](https://docs.subq.ai/using-the-api/models/), and [Baseten mammoth-1](https://www.baseten.co/library/mammoth-1/). Providers may charge for requests or require account access. Model preferences and confidence are not profit probabilities; the models have not been compared or calibrated for this trading task. Jev calibration artifacts cannot be used with the other providers. The SPY background signal continues to use Jev. Batch API cost caps are estimates configured per key, so set them to cover the selected provider's costs.

The dashboard and live CLI default to Alpaca stock bars with the free `iex` feed and raw prices. Select Yahoo Finance in the dashboard, or pass `--source yahoo` to `ft analyze` or `ft analyze-batch`. Yahoo data can differ from Alpaca in price, volume, coverage and availability; Yahoo company details and recent headlines may also be supplied to the model. IEX represents one US exchange, not consolidated market volume; prices, volume, VWAP, and model decisions can differ from Yahoo or SIP. Alpaca bars can include extended-hours trades. The feed may omit intervals with no trades, and stale bars remain blocked from actionable signals. Company profiles and news are not supplied to the decision model by the Alpaca analysis path. See [Alpaca market data](https://docs.alpaca.markets/us/docs/market-data-faq).

On page load, independently of the selected analysis provider, the dashboard fetches SPY's latest completed 5-minute IEX candle, the most recent change between completed candles exactly 10 minutes apart, and the latest SPY headline from [Alpaca News](https://docs.alpaca.markets/us/reference/news-3). The last available change remains visible when data is stale; it is unavailable if no such pair exists in the returned data. This market overview is cached in the server process for 10 minutes. News may be delayed or unavailable depending on account access. A failed price or news request is shown as unavailable; a partial result can remain cached for 10 minutes.

Every page load also requests SPY's deterministic rule and model preference using the same latest completed 10-minute candles. **The automatic model request can incur charges**; results, including failures, are cached for 10 minutes per server process. Outside normal weekday New York trading hours, a successful previous result remains unchanged; during normal hours it is reused when the latest completed candle is unchanged. Normal-hour detection does not account for exchange holidays or early closures. No portfolio fields are sent. The unvalidated indicator rule shows HOLD when the indicators provide no directional signal; neither signal is risk-checked or an order. Old market data is labeled stale and research-only. The news headline is fetched separately and is not a model input.

The last manual analysis stores only its symbol, timeframe, model preference, candle timestamp, and source in browser local storage. Portfolio values and credentials are not stored there. Browser data is device-specific and can be cleared using browser settings.

Analysis history is kept for the current session by default. Enable “Save history on this device” to retain up to 50 analyses and 2 MiB in browser storage. Reopening a saved analysis does not run the model; its original candle time and data source are shown. Delete individual entries or clear history in the dashboard. Turning saving off leaves previously saved entries on the device until cleared. Saved results can reveal trading activity; credentials and portfolio inputs are excluded. Browser storage is device-local, is not a secure backup, and does not sync across devices.

Dashboard analysis supports 1m, 2m, 5m, 15m, 30m, and 60m. Alpaca and CSV CLI analysis and automatic SPY signals also support 10m; Yahoo does not. The CLI supports 90m with Yahoo or CSV. Both providers return the most recent bars available at the requested time; timestamps and freshness are validated before use. Credentials stay server-side; never put them in a request or share them.

Market snapshots and indicator calculations are cached per process for at most the candle timeframe or 10 minutes, whichever is shorter. The market cache key includes the exact evaluation cutoff, symbol, timeframe, provider and a credential fingerprint; candle validation and the freshness limit still run for every analysis. A cache hit can contain provider data revised since the snapshot was fetched. Live requests normally have distinct cutoffs and therefore do not share market-data cache entries; repeated historical evaluations can reuse them.

## Portfolio and risk inputs

Without complete portfolio and risk inputs, you can inspect the model preference as a signal only; the risk-checked action is unavailable (`null` in JSON). Supply your own values and limits through the dashboard or CLI:

```powershell
uv run ft analyze AAPL --timeframe 5m --position 0 --equity 10000 --exposure 0 --daily-loss 0 --trade-count 0 --quantity 1 --max-position 1000 --max-exposure 5000 --max-daily-loss 200 --max-trades 5 --min-confidence 0.5
```

These values illustrate the input format, not recommended limits or confidence thresholds.

- `position` and `quantity` are shares. SELL cannot exceed the existing long position.
- Equity, exposure, daily loss, and value limits use the instrument's currency. Enter loss as a positive amount and exposure as gross long portfolio exposure.
- `max-position` limits position value at the last close, not the number of shares.
- `trade-count` and `max-trades` count trades. `min-confidence` accepts a value from 0 to 1 and requires your own validation.

The dashboard and live CLI show stale candles as a labeled historical preview and block portfolio actions. CLI JSON exposes the model preference as `model_signal` (also `decision.action`); `actionable_signal` remains null without complete portfolio inputs and stale data cannot produce an actionable trade. Explicit historical and CSV CLI runs still enforce the requested freshness limit. Errors have no actionable signal and produce a nonzero CLI exit code. Each analysis makes a model request and may incur a charge, including synthetic samples.

## Rules-based comparison

The dashboard and analysis JSON compare the model preference with an unvalidated rule using the same completed candles: BUY when EMA 9 is above EMA 21 and MACD is above its signal line; SELL when both comparisons are below. Ties or mixed indicators produce no directional signal. The `rule_comparison` field reports the rule signal and whether it agrees with the model; a model HOLD disagrees with a directional rule. Agreement does not establish accuracy, independent confirmation, or a chance of profit. This comparison does not alter the risk checks or actionable signal.

## Concurrent ticker analysis

```powershell
uv run ft analyze-batch AAPL MSFT NVDA --timeframe 5m --concurrency 3
```

The command runs up to 50 distinct US stock tickers using Alpaca IEX by default (or `--source yahoo`) with 1–5 concurrent workers (default 3). Each ticker uses the normal analysis pipeline and can incur one paid model request. Results are printed in input order, with a result or error for each ticker; the exit code is nonzero if any ticker fails. The batch `usage` summary adds provider-reported input and output tokens and reports when token information is unavailable. `complete` is false when a call fails or usage is missing, so reported totals may omit billable usage; they are not a cost cap or invoice. Stale results are marked as historical previews; they still request the model and may incur charges. Results are signal only: batch mode does not accept portfolio settings or CSV input. It does not account for aggregate portfolio exposure or place orders.

## Versioned API configuration

The dashboard and `/api/analyze` stay local. `/v1/analyze` and the batch endpoints require an `X-API-Key`, Redis for shared per-key rate limits, and estimated cost limits. The server stores only SHA-256 hashes of high-entropy client keys. Configure Redis with `FASTTRADE_REDIS_URL` (for example `redis://127.0.0.1:6379/0`), and set `FASTTRADE_BATCH_KEY_HASHES` to a JSON object keyed by lowercase SHA-256 digests. Each entry contains `estimate_per_call`, `request_cap`, and `daily_budget` in USD, plus optional `rate_per_minute` (default 60) and `tier`. The amounts below illustrate configuration, not provider pricing:

```powershell
# Generate and securely retain a random key; do not print or commit it.
$clientKey = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()
$keyHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($clientKey))).ToLower()
$env:FASTTRADE_BATCH_KEY_HASHES = @{ $keyHash = @{ estimate_per_call='0.01'; request_cap='0.10'; daily_budget='1.00'; rate_per_minute=60; tier='standard' } } | ConvertTo-Json -Compress
$env:FASTTRADE_REDIS_URL = 'redis://127.0.0.1:6379/0'
uv run ft
```

In another client process, use a securely supplied client key with `X-API-Key`. For an individual analysis, POST JSON such as `{"symbol":"AAPL","timeframe":"5m"}` to `/v1/analyze`. This reserves one estimated model-call cost before execution; retries are separate requests and can incur additional charges. The model provider's bearer key remains on the server. A thin HTTP client should call the API rather than import internal modules.

**Migration:** `FASTTRADE_BATCH_KEYS` is no longer accepted. Before upgrading, back up the batch database and configuration. Hash each existing key with SHA-256, use that digest as the new JSON key, and retain its existing budget fields. This preserves the existing job and budget identity. For rotation, add a new digest with the old digest as `account` on both entries, and set a timezone-aware `expires_at` on the old entry. Both credentials then share job history, rate limits, and budget during the grace period; configure identical rates and budget fields for both active entries. Remove the expired entry after the grace period. To roll back, restore the prior release and configuration without deleting the database; the new single-analysis reservation table is ignored by older versions, so do not roll back while relying on its budget totals.

`FASTTRADE_HOST` and `FASTTRADE_PORT` default to `127.0.0.1` and `8765`. Non-loopback binding requires valid hashed keys and reachable Redis and emits a warning. Only `/v1` and health endpoints are available to remote clients; the dashboard and unversioned API require loopback access. Set `FASTTRADE_CORS_ORIGINS` to a comma-separated list of exact trusted origins; wildcard origins and credentials are not allowed. Set `FASTTRADE_ENABLE_OPENAPI=false` to disable the schema and documentation endpoints. For private network use, restrict access at the firewall and terminate TLS at a trusted proxy configured to expose only `/v1` and health routes. Do not trust client-supplied forwarded headers; the bundled server disables proxy-header processing.

`.env` is for local development. For production, inject secrets from a secret manager, protect Redis credentials and storage, rotate client and model keys, and never bake secrets into images or source control. All workers must share the same Redis and the same local SQLite database; this does not support a database shared across hosts.

For batch analysis, use `X-API-Key` on each request, and a unique `Idempotency-Key` on submissions. Reuse that idempotency key only to retry the same request. Estimate and submit the same JSON body with `symbols` (1–50 distinct tickers), optional `timeframe` and optional `source`:

```powershell
$headers = @{ 'X-API-Key' = 'replace-with-random-secret'; 'Idempotency-Key' = 'unique-request-id' }
$body = @{ symbols = @('AAPL', 'MSFT'); timeframe = '5m'; source = 'alpaca' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/v1/analyze:batch/estimate -Headers $headers -ContentType 'application/json' -Body $body
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/v1/analyze:batch -Headers $headers -ContentType 'application/json' -Body $body
```

Fewer than 10 symbols return results synchronously; larger submissions return a job ID. Poll `GET /v1/jobs/{id}` for progress and `GET /v1/usage` for that key's current UTC-day reservations and reported tokens. Each result is either an analysis or an explicit error. Per-symbol batch execution is limited to 90 seconds, with at most four batch analyses running concurrently per worker. A timed-out call may continue in the background and incur charges; it retains its worker slot and reservation, and the same idempotency key will not rerun it.

The API reserves the configured **estimated** cost before starting, including for failures and timeouts. It rejects requests exceeding the per-request cap or remaining daily estimated budget. These are limits on estimates, not guarantees on provider charges: actual token counts and prices can differ, and failed calls may have unreported usage. Use provider-side spending limits for a hard billing cap.

Jobs, partial results, request hashes, budget reservations and reported individual-analysis token counts are stored in `~/.fasttrade/batch.sqlite3`, or the path in `FASTTRADE_BATCH_DATABASE`. Secure this file: results can reveal research activity. Worker leases prevent recovery from taking jobs owned by a live worker. After a worker stops, another worker resumes work that had not started once its lease expires; calls already started are marked as having an unavailable outcome and are never automatically repeated. Revoked keys do not resume jobs. Retrying the same idempotency key returns the original job. Deleting the database removes this retry and budget history. `/v1/usage` includes reported tokens for both batch and individual analyses; records created before individual tracking, errors, interrupted calls, or missing provider usage remain incomplete rather than being counted as zero-cost calls.

## CSV and historical research

Use the included five-minute candles with a fixed evaluation time:

```powershell
uv run ft analyze NVDA --timeframe 5m --csv data/candles.csv --as-of 2026-01-02T21:00:00Z
```

CSV columns are `timestamp,open,high,low,close,volume`. Timestamps mark candle starts and must include a timezone. Supply ascending, unique rows with at least 50 completed candles; the analyzer excludes incomplete candles. It measures freshness against `--as-of`, so a historical result does not establish live data availability.

The latest completed candle must be no older than two intervals by default; override this with `--max-age-seconds`. Set the exchange timezone through `--session-timezone`; CSV defaults to America/New_York. VWAP resets at local midnight and approximates session VWAP from OHLCV data. Supply data covering the session you want to inspect.

Historical evaluation excludes later headlines and current company metadata. Only supply `--spread` and `--liquidity` when you know their values; otherwise the analyzer reports them as unavailable. Output separates features, headlines, model preferences, indicator observations, risk rejections, and the final signal.

The CLI does not check exchange calendars or establish that a market is open. Historical simulations do not establish a validated live strategy.

## Historical benchmark

`uv run ft benchmark --start 2026-09-28 --end 2026-10-23 --symbols AAPL,MSFT,NVDA --model jev-latest` runs the same single-symbol decision engine once per stock at the last available hourly candle of each week. Change the dates, the comma-separated US ticker list, or the model; `--model` defaults to `SYSTEMONE_MODEL`. The end date is inclusive, in New York time. Add `> benchmark.json` to save the report. Each decision makes a paid model request. The endpoint and credentials use the normal configuration.

The simulation starts with $10,000 split equally into independent stock allocations. BUY invests available cash, SELL liquidates that stock's shares, and HOLD leaves it unchanged. Fills use the next common hourly open; the final incomplete week is excluded if there is no later execution candle in the requested range. Positions are valued at the last available common candle close, and SPY is measured from the first execution open to that same close. The report includes model responses, per-decision execution prices, cash and shares, and directional accuracy: BUY is correct when the price rises by the next review's execution open, SELL when it falls, with the final close used for the last review. HOLD is excluded; SELL when no shares are held is still a directional forecast, not a successful trade.

This command uses Yahoo through yfinance, an unofficial research tool; data can be delayed, incomplete, or unavailable. It requests adjusted hourly OHLCV with 30 calendar days for indicator warmup; at least 50 prior completed bars are required. Missing common candles abort the run. Yahoo restricts older intraday data, and revised prices or nondeterministic model responses can change a rerun. The benchmark omits news, company details, portfolio risk checks, commissions, spread, slippage, taxes, and financing. The model may know the historical period, so these retrospective results do not establish a live edge. For US-listed assets only; results are not directly comparable with the earlier January–June 2026 benchmark, which used unadjusted intraday model inputs and adjusted daily execution prices.

The `significance` block reports a seeded 95% stationary-bootstrap interval for the mean return difference between the portfolio and SPY over matching execution-to-execution periods. The final partial period through the last close is excluded from this weekly statistic but remains in the total return. Returns are fractions, not percentages. The interval is unavailable with fewer than two periods; short samples can still be highly unreliable. It does not correct for selecting among model variants or historical leakage and is not evidence of a live edge.

The [October 2026 example report](benchmark-2026-10.json) contains the individual decisions from a live run of this command on 28 October 2026.

## Validated-data backtest

Run a weekly backtest with a separately prepared, frozen dataset:

```powershell
uv run ft backtest --data-dir .\market-data --start 2026-01-19 --end 2026-01-27 --symbols AAPL --model jev-1.14.0 --output .\backtest-report.json
```

The command validates the entire input before making paid model requests and refuses to overwrite an existing report. It saves the decisions, trade ledger, daily close equity curve, daily-close maximum drawdown, portfolio and SPY returns, fees, model version, dataset-manifest hash and per-decision model-state hashes. It also records mean model request latency and provider-reported input tokens from successful decisions; `usage_complete` is false if usage is missing or a request was retried, so totals may exclude billed calls. Model cost depends on the provider's pricing and is separate from simulated portfolio returns. BUY invests each symbol's independent share of $10,000; SELL exits its shares; HOLD leaves the position unchanged. Decisions use only candles completed by the decision time, and fill at the next session's open. Default commission is 5 basis points per side and slippage is 10 basis points per side; set `--fee-bps` and `--slippage-bps` to change them. SPY pays the same assumed entry costs. Ending values are marked at the last close without hypothetical liquidation costs. There is no liquidity, partial-fill, market-impact, financing, tax or portfolio-risk model.

The `market-data` folder must contain:

- `sessions.csv`, with timezone-aware `open,close` timestamps for every exchange session covering the warmup and test, plus at least one session after the end date. Session times must come from a trustworthy point-in-time exchange calendar, including holidays and early closes.
- One unadjusted regular-session hourly OHLCV CSV per ticker and one `SPY.csv`, each with `timestamp,open,high,low,close,volume`. Timestamps mark bar starts and include a timezone; include at least 50 completed warmup candles. The final bar of a session may be shorter than an hour; the engine waits until its nominal hour has ended before asking the model.
- `manifest.json`, attesting `price_basis` as `unadjusted` and `corporate_actions` as `none` or `cash_dividends_only`, with SHA-256 hashes for `sessions.csv` and every ticker CSV. The corporate-action attestation covers the entire dataset, including warmup and SPY. Splits, mergers and other non-cash corporate actions remain unsupported.
- For `cash_dividends_only`, include a checksummed `dividends.csv` with `symbol,ex_date,pay_date,amount` for each cash dividend. Ex-dates must be sessions in the supplied calendar, and dividends with ex-dates during the test must pay after its end. Positions held before the ex-date earn a receivable included in equity, even after a later sale; it is not available to trade. Dividends paid during the test, reinvestment, withholding and splits are not supported and must not be silently omitted. If the dataset has no events, use `corporate_actions: none` and omit the file.

For example, after independently verifying those conditions for AAPL and SPY, create the manifest in PowerShell:

```powershell
$hashes = @{}; 'sessions.csv','AAPL.csv','SPY.csv' | ForEach-Object { $hashes[$_] = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path .\market-data $_)).Hash.ToLower() }; @{ price_basis='unadjusted'; corporate_actions='none'; sha256=$hashes } | ConvertTo-Json -Depth 3 | Set-Content -Encoding utf8 .\market-data\manifest.json
```

Hashes make later changes detectable; they do not prove that the vendor's historical prices, calendar, or corporate-action attestation were correct or available at the time. Do not use Yahoo's retrospectively adjusted prices as raw prices. The engine does not check whether tickers were chosen with hindsight or whether the model's training included future events. These limitations mean the result cannot be certified free of all leakage. For stronger evidence, predefine the universe and rules, use point-in-time data and a model known to predate the test, and evaluate later out-of-sample periods. See the [CFA Institute's backtesting overview](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/backtesting-and-simulation) and [research on temporal leakage in language-model backtests](https://arxiv.org/abs/2608.02985).

## Decision calibration (research)

`ft calibrate` fits a post-hoc mapping from the decision model's P(BUY) to realized weekly outcomes, using a frozen backtest report and its dataset:

```powershell
uv run ft calibrate --report .\backtest-report.json --data-dir .\market-data --train-frac 0.75 --alpha 0.1 --output .\calibration.json
```

It pairs every recorded decision with the move to that symbol's next execution open, splits chronologically (fitting never sees the test window), fits temperature and isotonic mappings, reports ECE and Brier before and after with reliability curves, computes a split-conformal quantile, and fits an expected-return mapping from P(BUY) to the realized move with its own conformal bound. `--fee-bps` and `--slippage-bps` set the round-trip cost the return gate must clear. It refuses to overwrite an existing artifact. The published flagship example, including its honest results, is [docs/calibration-2026-09-28.md](calibration-2026-09-28.md).

In `analyze`, `--calibration PATH` loads an artifact, adds `calibrated_confidence` and a `calibration` provenance block to the decision, and gates `--min-confidence` on the calibrated value instead of the raw confidence. `--require-certified-edge` blocks actionable signals unless the artifact certifies aggregate net-of-cost edge (bootstrap CI on mean excess return per BUY decision vs SPY) on its test window. The response also carries per-trade diagnostics (`conformal_interval`, `expected_return`, `return_interval_lower`, `edge_certified`); they inform but do not block. Calibration is a research label fitted on backtest outcomes; it is not advice, it does not make signals profitable, and an artifact fitted on one horizon applied to another is an extrapolation the response says so. Benchmark and backtest decisions now also record the model's raw `confidence` for future calibration runs.

## Local API

Run `uv run ft`, then open the [interactive API reference](http://127.0.0.1:8765/docs) or [OpenAPI schema](http://127.0.0.1:8765/openapi.json).

- `GET /api/health` checks server availability; it does not check market-data or model connectivity.
- `POST /api/analyze` accepts a symbol, timeframe, optional `sample` flag, and portfolio/risk fields. The server reads its own credentials; do not include them in requests.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/analyze -ContentType 'application/json' -Body '{"symbol":"NVDA","timeframe":"5m"}'
```

For demos, set `sample` to `true`; synthetic samples support only `5m` and block portfolio actions. API access is limited to this computer. The interactive documentation loads interface assets from a CDN; keep the server off public networks.

## Tests

```powershell
uv run pytest
node --test tests/chart.test.cjs
```

Browser regression checks require Playwright and Chromium:

```powershell
node --test --test-timeout=10000 tests/ui.test.cjs
```

The browser tests serve the static UI locally and mock analysis responses, avoiding market-data and paid model requests. To use an existing Chromium installation, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to its executable path. Playwright must be available to Node's module resolver.
