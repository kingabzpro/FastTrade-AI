<div align="center">
  <img src="fasttrade/static/logo.svg" alt="FastTrade AI logo" width="72">
  <h1>FastTrade AI</h1>
  <p><strong>See the market. Check the signal. Stay in control.</strong></p>
  <p>Market research powered by Jev, a System One decision model.</p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.10 or newer">
    <a href="https://typesafe.ai/"><img src="https://framerusercontent.com/images/aNFzSFxM4fjICmnibw7npfZjcQ.png" width="20" height="20" alt="TypeSafe logo"><img src="https://img.shields.io/badge/System%20One-Jev-D943AD" alt="Powered by Jev"></a>
    <a href="https://alpaca.markets/data"><img src="https://img.shields.io/badge/Market%20data-Alpaca%20IEX-177A72" alt="Market data from Alpaca IEX"></a>
    <img src="https://img.shields.io/badge/Mode-Research%20only-087B59" alt="Research only">
  </p>
</div>

## How it works

FastTrade reads market data and technical indicators, asks Jev for a BUY, HOLD or SELL preference, then checks any supplied portfolio limits.

A retrospective 25-stock backtest for October 2026 returned **+2.04%** with Alpaca IEX data, versus **−0.17% for SPY**, including simulated fees and slippage. The earlier Yahoo-data run returned +2.31% versus −0.15% for SPY. See the [Alpaca comparison](docs/backtest-2026-09-28-alpaca/README.md) for methods and limitations.

Both results use a hindsight-selected universe and may contain model lookahead; neither establishes a live trading edge. The IEX feed represents one exchange, so its prices and volume can differ from consolidated data.

## Get started

Install [uv](https://docs.astral.sh/uv/) and Python 3.10+, then add your [TypeSafe](https://typesafe.ai/) key and [Alpaca](https://alpaca.markets/) API credentials to `.env`:

```powershell
Copy-Item .env.example .env
# Set TYPESAFE_API_KEY, ALPACA_API_KEY and ALPACA_SECRET_KEY in .env
uv run ft
```

Market data comes from Alpaca’s free IEX feed, a single US exchange; its prices and volumes may differ from consolidated data.

Open [localhost:8765](http://127.0.0.1:8765/), choose a market, and run an analysis. FastTrade shows model preferences, charts and risk checks; **it never places orders**. Without complete portfolio settings, the result is signal only.

For a single ticker, run `uv run ft analyze NVDA --timeframe 5m`.

For concurrent CLI analysis, run `uv run ft analyze-batch AAPL MSFT NVDA --concurrency 3`. Each ticker may incur a paid model request; batch results are signal only.

To use a compatible endpoint, set `SYSTEMONE_URL`, `SYSTEMONE_MODEL` and `TYPESAFE_API_KEY` in `.env`. The endpoint must support the System One `state`/`questions` request and `choice` response. The key is sent as a bearer token; use only an endpoint you trust.

## Explore

- [Alpaca IEX backtest and frozen inputs](docs/backtest-2026-09-28-alpaca/README.md)
- [Earlier Yahoo backtest](docs/backtest-2026-09-28/README.md)
- [Usage guide: analysis, benchmarks and backtesting](docs/usage.md)
- [API reference](http://127.0.0.1:8765/docs) when the app is running
- [Apache 2.0 license](LICENSE)
