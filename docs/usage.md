# Usage reference

## Configuration

Set `TYPESAFE_API_KEY` in `.env` or your environment. The defaults are `SYSTEMONE_URL=https://api.typesafe.ai/v1/systemone` and `SYSTEMONE_MODEL=jev-latest`; override them in `.env` to use a compatible provider. Only send credentials to an endpoint you trust. Git ignores `.env`.

The startup banner shows local links, the selected model, and the default data source. It does not check provider connectivity or display API-key details. Terminal colors are disabled when `NO_COLOR` is set or output is redirected.

Pandas is temporarily capped below 3.1 because the current yfinance quote parser uses an operation deprecated in pandas 3.1. NumPy is capped below 2.5 to avoid a separate compatibility warning in pandas 2.x.

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

The browser shows stale candles as a labeled historical preview and blocks portfolio actions. The CLI rejects stale data. Errors have no actionable signal and produce a nonzero CLI exit code. Each analysis makes a model request and may incur a charge, including synthetic samples.

## CSV and historical research

Use the included five-minute candles with a fixed evaluation time:

```powershell
uv run ft analyze NVDA --timeframe 5m --csv data/candles.csv --as-of 2026-01-02T21:00:00Z
```

CSV columns are `timestamp,open,high,low,close,volume`. Timestamps mark candle starts and must include a timezone. Supply ascending, unique rows with at least 50 completed candles; the analyzer excludes incomplete candles. It measures freshness against `--as-of`, so a historical result does not establish live data availability.

The latest completed candle must be no older than two intervals by default; override this with `--max-age-seconds`. Set the exchange timezone through `--session-timezone`; CSV defaults to America/New_York. VWAP resets at local midnight and approximates session VWAP from OHLCV data. Supply data covering the session you want to inspect.

Historical evaluation excludes later headlines and current company metadata. Only supply `--spread` and `--liquidity` when you know their values; otherwise the analyzer reports them as unavailable. Output separates features, headlines, model preferences, indicator observations, risk rejections, and the final signal.

yfinance is an unofficial research tool. Yahoo data can be delayed, incomplete, or unavailable, with limited intraday history. The CLI does not check exchange calendars or establish that a market is open. Historical simulations do not establish a validated live strategy.

## Historical benchmark

`uv run ft benchmark --start 2026-09-28 --end 2026-10-23 --symbols AAPL,MSFT,NVDA --model jev-latest` runs the same single-symbol decision engine once per stock at the last available hourly candle of each week. Change the dates, the comma-separated US ticker list, or the model; `--model` defaults to `SYSTEMONE_MODEL`. The end date is inclusive, in New York time. Add `> benchmark.json` to save the report. Each decision makes a paid model request. The endpoint and credentials use the normal configuration.

The simulation starts with $10,000 split equally into independent stock allocations. BUY invests available cash, SELL liquidates that stock's shares, and HOLD leaves it unchanged. Fills use the next common hourly open; the final incomplete week is excluded if there is no later execution candle in the requested range. Positions are valued at the last available common candle close, and SPY is measured from the first execution open to that same close. The report includes model responses, per-decision execution prices, cash and shares, and directional accuracy: BUY is correct when the price rises by the next review's execution open, SELL when it falls, with the final close used for the last review. HOLD is excluded; SELL when no shares are held is still a directional forecast, not a successful trade.

Data is Yahoo adjusted hourly OHLCV, with 30 calendar days requested for indicator warmup; at least 50 prior completed bars are required. Missing common candles abort the run. Yahoo restricts older intraday data, and revised prices or nondeterministic model responses can change a rerun. The benchmark omits news, company details, portfolio risk checks, commissions, spread, slippage, taxes, and financing. The model may know the historical period, so these retrospective results do not establish a live edge. For US-listed assets only; results are not directly comparable with the earlier January–June 2026 benchmark, which used unadjusted intraday model inputs and adjusted daily execution prices.

The [October 2026 example report](benchmark-2026-10.json) contains the individual decisions from a live run of this command on 28 October 2026.

## Validated-data backtest

Run a weekly backtest with a separately prepared, frozen dataset:

```powershell
uv run ft backtest --data-dir .\market-data --start 2026-01-19 --end 2026-01-27 --symbols AAPL --model jev-1.14.0 --output .\backtest-report.json
```

The command validates the entire input before making paid model requests and refuses to overwrite an existing report. It saves the decisions, trade ledger, daily close equity curve, daily-close maximum drawdown, portfolio and SPY returns, fees, model version, dataset-manifest hash and per-decision model-state hashes. BUY invests each symbol's independent share of $10,000; SELL exits its shares; HOLD leaves the position unchanged. Decisions use only candles completed by the decision time, and fill at the next session's open. Default commission is 5 basis points per side and slippage is 10 basis points per side; set `--fee-bps` and `--slippage-bps` to change them. SPY pays the same assumed entry costs. Ending values are marked at the last close without hypothetical liquidation costs. There is no liquidity, partial-fill, market-impact, financing, tax or portfolio-risk model.

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

## Local API

Run `uv run ft`, then open the [interactive API reference](http://127.0.0.1:8765/docs) or [OpenAPI schema](http://127.0.0.1:8765/openapi.json).

- `GET /api/health` checks server availability; it does not check market-data or model connectivity.
- `POST /api/analyze` accepts a symbol, timeframe, optional `sample` flag, and portfolio/risk fields. The server reads its own API key; do not include credentials in requests.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/analyze -ContentType 'application/json' -Body '{"symbol":"NVDA","timeframe":"5m","sample":true}'
```

Synthetic samples support only `5m` and block portfolio actions. API access is limited to this computer. The interactive documentation loads interface assets from a CDN; keep the server off public networks.

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
