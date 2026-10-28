<div align="center">
  <img src="fasttrade/static/logo.svg" alt="FastTrade AI logo" width="72">
  <h1>FastTrade AI</h1>
  <p><strong>See the market. Check the signal. Stay in control.</strong></p>
  <p>Market research powered by Jev, a System One decision model.</p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.10 or newer">
    <img src="https://img.shields.io/badge/Powered%20by-Jev-6750A4" alt="Powered by Jev">
    <img src="https://img.shields.io/badge/Mode-Research%20only-087B59" alt="Research only">
    <img src="https://img.shields.io/badge/License-Apache%202.0-blue" alt="Apache 2.0 license">
  </p>
</div>

<table align="center">
  <tr>
    <td align="center"><strong>+2.46 pp</strong><br><sub>over SPY in one backtest</sub></td>
    <td align="center"><strong>0.60 sec</strong><br><sub>average model request</sub></td>
    <td align="center"><strong>$0.0343</strong><br><sub>estimated per 1,000 decisions</sub></td>
  </tr>
</table>

## The result

In a **25-stock historical simulation with four weekly reviews**, FastTrade returned **+2.31%** versus **−0.15% for SPY**. On $10,000, that was $10,230.73 versus $9,985.41. The investment period was **October 5–27, 2026**; the simulation included 5 bps commission and 10 bps slippage per trade side. Model costs are separate from portfolio returns.

The speed and cost figures are from the same run: 100 Jev 1.14.0 requests averaged **599 ms** and used 81,703 input tokens. At [TypeSafe's published rate](https://typesafe.ai/), estimated model cost was **$0.00343 total**, or **$0.0343 per 1,000 similar decisions**; actual billing may differ. Request time includes network latency, not data preparation.

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

Jev is [TypeSafe's System One model](https://docs.typesafe.ai/concepts/system-one): it returns structured decisions and probabilities for the supplied market state. To use another compatible endpoint, set these values in `.env`:

```dotenv
SYSTEMONE_URL=https://api.typesafe.ai/v1/systemone
SYSTEMONE_MODEL=jev-latest
TYPESAFE_API_KEY=your-provider-key
```

Replace the URL and model with your provider's values. The endpoint must accept the System One `state` and `questions` request and return a compatible `choice` response. The key is sent as a bearer token; use only an endpoint you trust.

## Explore

- [Backtest report and frozen inputs](docs/backtest-2026-09-28/report-measured.json)
- [Method, limitations and rerun command](docs/backtest-2026-09-28/README.md)
- [Usage guide: analysis, benchmarks and backtesting](docs/usage.md)
- [API reference](http://127.0.0.1:8765/docs) when the app is running
- [Apache 2.0 license](LICENSE)
