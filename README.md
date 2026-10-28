<div align="center">
  <img src="fasttrade/static/logo.svg" alt="FastTrade AI logo" width="72">
  <h1>FastTrade AI</h1>
  <p><strong>See the market. Check the signal. Stay in control.</strong></p>
  <p>Market research powered by Jev, a System One decision model.</p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.10 or newer">
    <a href="https://typesafe.ai/"><img src="https://framerusercontent.com/images/aNFzSFxM4fjICmnibw7npfZjcQ.png" width="20" height="20" alt="TypeSafe logo"><img src="https://img.shields.io/badge/System%20One-Jev-D943AD" alt="Powered by Jev"></a>
    <a href="https://finance.yahoo.com/"><img src="https://img.shields.io/badge/Market%20data-Yahoo%20Finance-6001D2" alt="Market data from Yahoo Finance"></a>
    <img src="https://img.shields.io/badge/Mode-Research%20only-087B59" alt="Research only">
  </p>
</div>

<div align="center">
  <a href="#how-it-works"><img src="https://img.shields.io/badge/VS%20SPY-%2B2.46%20pp-0B8F61?style=for-the-badge" alt="2.46 percentage points over SPY"></a>
  <a href="#how-it-works"><img src="https://img.shields.io/badge/AVG%20MODEL%20REQUEST-%3C0.6%20sec-536DE0?style=for-the-badge" alt="Average model request under 0.6 seconds"></a>
  <a href="#how-it-works"><img src="https://img.shields.io/badge/1%2C000%20DECISIONS-%240.0343-D59015?style=for-the-badge" alt="Estimated model cost 0.0343 dollars per 1,000 decisions"></a>
</div>

## How it works

FastTrade reads market data and technical indicators, asks Jev for a BUY, HOLD or SELL preference, then checks any supplied portfolio limits.

In a 25-stock backtest from October 5–27, 2026, FastTrade returned **+2.31%** versus **−0.15% for SPY**, including assumed commissions and slippage.

Across 100 decisions, model requests averaged **599 ms** and cost an estimated **$0.0343 per 1,000 similar decisions** at [TypeSafe's pricing](https://typesafe.ai/). Actual billing may differ.

FastTrade supports compatible System One API endpoints, so you can change the model or provider in `.env`.

Next, we plan a live-market experiment using Jev's decisions to measure whether they can be profitable.

> [!NOTE]
> The backtest is retrospective and may contain hindsight; it does not establish a live trading edge. An [earlier six-month benchmark](docs/benchmark-2026-h1.md) returned +6.60% versus SPY's +9.35%.

## Get started

Install [uv](https://docs.astral.sh/uv/) and Python 3.10+, then add your [TypeSafe](https://typesafe.ai/) key to `.env`:

```powershell
Copy-Item .env.example .env
# Set TYPESAFE_API_KEY in .env
uv run ft
```

Open [localhost:8765](http://127.0.0.1:8765/), choose a market, and run an analysis. FastTrade shows model preferences, charts and risk checks; **it never places orders**. Without complete portfolio settings, the result is signal only.

To use a compatible endpoint, set `SYSTEMONE_URL`, `SYSTEMONE_MODEL` and `TYPESAFE_API_KEY` in `.env`. The endpoint must support the System One `state`/`questions` request and `choice` response. The key is sent as a bearer token; use only an endpoint you trust.

## Explore

- [Backtest report and frozen inputs](docs/backtest-2026-09-28/report-measured.json)
- [Method, limitations and rerun command](docs/backtest-2026-09-28/README.md)
- [Usage guide: analysis, benchmarks and backtesting](docs/usage.md)
- [API reference](http://127.0.0.1:8765/docs) when the app is running
- [Apache 2.0 license](LICENSE)
