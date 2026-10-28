# FastTrade AI

A local market research dashboard with candlestick charts, technical indicators, AI BUY/HOLD/SELL preferences, and portfolio risk checks. Uses Yahoo Finance or synthetic sample candles. It never places orders.

## Get started

Install [uv](https://docs.astral.sh/uv/) and Python 3.10+. Create `.env` from [`.env.example`](.env.example), then set `TYPESAFE_API_KEY`.

```powershell
uv run ft
```

Open [localhost:8765](http://127.0.0.1:8765/) if the browser does not open. Press Ctrl+C in the terminal to stop.

## Use the workspace

- Choose a symbol, timeframe, and data source, then press **Run analysis**. Opening the dashboard makes no analysis request.
- Inspect candles, volume, EMA/VWAP overlays, and model preferences. Chart times use UTC; session history clears on reload.
- Expand **Portfolio & risk settings** to supply your holdings and limits. Missing risk inputs, stale data, and synthetic samples keep the actionable signal at HOLD.

Each analysis requests a model decision and may incur a charge, including sample mode. Model preferences are not profit probabilities. This strategy has not been backtested or calibrated, and market data may be delayed. Keep the server local.

## CLI, API, and tests

```powershell
uv run fasttrade analyze NVDA --timeframe 5m
uv run fasttrade tui
uv run pytest
node --test tests/chart.test.cjs
```

See the [usage guide](docs/usage.md) for risk inputs, CSV data, configuration, and browser tests. With the server running, open the [API reference](http://127.0.0.1:8765/docs) for request fields and examples.
