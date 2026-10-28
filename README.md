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
  <a href="#the-result"><img src="https://img.shields.io/badge/VS%20SPY-%2B2.46%20pp-0B8F61?style=for-the-badge" alt="2.46 percentage points over SPY"></a>
  <a href="#the-result"><img src="https://img.shields.io/badge/AVG%20MODEL%20REQUEST-%3C0.6%20sec-536DE0?style=for-the-badge" alt="Average model request under 0.6 seconds"></a>
  <a href="#the-result"><img src="https://img.shields.io/badge/1%2C000%20DECISIONS-%240.0343-D59015?style=for-the-badge" alt="Estimated model cost 0.0343 dollars per 1,000 decisions"></a>
</div>

## The result

**October 5–27, 2026 · 25 stocks · 100 decisions.** In this historical simulation, FastTrade returned **+2.31%** versus **−0.15% for SPY**. A $10,000 portfolio ended at $10,230.73 versus $9,985.41. Returns include assumed commissions and slippage; model costs are separate.

Jev 1.14.0 requests averaged **599 ms**. Based on 81,703 input tokens and [TypeSafe's pricing](https://typesafe.ai/), model cost was an estimated **$0.0343 per 1,000 similar decisions**. Request time includes network latency, not data preparation; actual billing may differ.

> [!NOTE]
> This is one short retrospective test, not evidence of a live trading edge. The stock universe was selected with hindsight, and the model or historical data may contain future information. An [earlier six-month benchmark](docs/benchmark-2026-h1.md) returned +6.60% versus SPY's +9.35%.

## Get started

Install [uv](https://docs.astral.sh/uv/) and Python 3.10+, then add your [TypeSafe](https://typesafe.ai/) key to `.env`:

```powershell
Copy-Item .env.example .env
# Set TYPESAFE_API_KEY in .env
uv run ft
```

Open [localhost:8765](http://127.0.0.1:8765/), choose a market, and run an analysis. FastTrade shows model preferences, charts and risk checks; **it never places orders**. Without complete portfolio settings, the result is signal only.

Jev is [TypeSafe's System One model](https://docs.typesafe.ai/concepts/system-one), returning structured decisions and probabilities. To use a compatible endpoint, set `SYSTEMONE_URL`, `SYSTEMONE_MODEL` and `TYPESAFE_API_KEY` in `.env`. The endpoint must support the System One `state`/`questions` request and `choice` response. The key is sent as a bearer token; use only an endpoint you trust.

## Explore

- [Backtest report and frozen inputs](docs/backtest-2026-09-28/report-measured.json)
- [Method, limitations and rerun command](docs/backtest-2026-09-28/README.md)
- [Usage guide: analysis, benchmarks and backtesting](docs/usage.md)
- [API reference](http://127.0.0.1:8765/docs) when the app is running
- [Apache 2.0 license](LICENSE)
