# Usage reference

## Configuration

Set `TYPESAFE_API_KEY` in `.env` or your environment. The defaults are `SYSTEMONE_URL=https://api.typesafe.ai/v1/systemone` and `SYSTEMONE_MODEL=jev-latest`; override them in `.env` to use a compatible provider. Only send credentials to an endpoint you trust. Git ignores `.env`.

## Portfolio and risk inputs

Without portfolio and risk inputs, you can inspect model preferences, but the actionable signal stays HOLD. Supply your own values and limits through the dashboard or CLI:

```powershell
uv run fasttrade analyze AAPL --timeframe 5m --position 0 --equity 10000 --exposure 0 --daily-loss 0 --trade-count 0 --quantity 1 --max-position 1000 --max-exposure 5000 --max-daily-loss 200 --max-trades 5 --min-confidence 0.5
```

These values illustrate the input format, not recommended limits or confidence thresholds.

- `position` and `quantity` are shares. SELL cannot exceed the existing long position.
- Equity, exposure, daily loss, and value limits use the instrument's currency. Enter loss as a positive amount and exposure as gross long portfolio exposure.
- `max-position` limits position value at the last close, not the number of shares.
- `trade-count` and `max-trades` count trades. `min-confidence` accepts a value from 0 to 1 and requires your own validation.

The browser shows stale candles as a labeled historical preview and forces HOLD. The CLI rejects stale data. Errors produce HOLD and a nonzero CLI exit code. Each analysis makes a model request and may incur a charge, including synthetic samples.

## CSV and historical research

Use the included five-minute candles with a fixed evaluation time:

```powershell
uv run fasttrade analyze NVDA --timeframe 5m --csv data/candles.csv --as-of 2026-01-02T21:00:00Z
```

CSV columns are `timestamp,open,high,low,close,volume`. Timestamps mark candle starts and must include a timezone. Supply ascending, unique rows with at least 50 completed candles; the analyzer excludes incomplete candles. It measures freshness against `--as-of`, so a historical result does not establish live data availability.

The latest completed candle must be no older than two intervals by default; override this with `--max-age-seconds`. Set the exchange timezone through `--session-timezone`; CSV defaults to America/New_York. VWAP resets at local midnight and approximates session VWAP from OHLCV data. Supply data covering the session you want to inspect.

Historical evaluation excludes later headlines and current company metadata. Only supply `--spread` and `--liquidity` when you know their values; otherwise the analyzer reports them as unavailable. Output separates features, headlines, model preferences, indicator observations, risk rejections, and the final signal.

yfinance is an unofficial research tool. Yahoo data can be delayed, incomplete, or unavailable, with limited intraday history. The CLI does not check exchange calendars or establish that a market is open. The strategy has not been backtested or calibrated.

## Local API

Run `uv run ft`, then open the [interactive API reference](http://127.0.0.1:8765/docs) or [OpenAPI schema](http://127.0.0.1:8765/openapi.json).

- `GET /api/health` checks server availability; it does not check market-data or model connectivity.
- `POST /api/analyze` accepts a symbol, timeframe, optional `sample` flag, and portfolio/risk fields. The server reads its own API key; do not include credentials in requests.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/analyze -ContentType 'application/json' -Body '{"symbol":"NVDA","timeframe":"5m","sample":true}'
```

Synthetic samples support only `5m` and force HOLD. API access is limited to this computer. The interactive documentation loads interface assets from a CDN; keep the server off public networks.

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
