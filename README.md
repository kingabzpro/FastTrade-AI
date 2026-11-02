<div align="center">
  <img src="docs/fasttrade-banner.jpg" alt="FastTrade AI: market data, model signals, and candlestick chart">
  <h1>FastTrade AI</h1>
  <p>Market research, model signals, and risk checks.</p>
</div>

FastTrade uses Alpaca IEX or Yahoo Finance market data and Jev to generate BUY, HOLD, or SELL preferences. **Research only; no orders are placed.**

## Benchmark results

| Measure | FastTrade | SPY buy-and-hold |
| --- | ---: | ---: |
| Return, Jan–Jun 2026 | +6.60% | +9.35% |
| Return, Oct 2026 (Alpaca IEX) | +2.04% | −0.17% |
| Directional accuracy, Jan–Jun | 49.01% (199 of 406) | Not measured |
| Mean model request time, Jan–Jun | 862 ms | Not applicable |
| Estimated model cost, Jan–Jun | $0.0224 for 650 decisions | Not applicable |

Retrospective results use hindsight-selected stocks and may contain model lookahead; they do not establish a live edge. Directional accuracy excludes HOLD. The six-month test excludes trading costs; the October test includes simulated fees and slippage. Model cost is estimated at a historical rate. See the [six-month benchmark](docs/benchmark-2026-h1.md) and [Alpaca backtest](docs/backtest-2026-09-28-alpaca/README.md) for methods and limitations.

## Get started

Install [uv](https://docs.astral.sh/uv/) and Python 3.10+. Add your [TypeSafe](https://typesafe.ai/) key and, for Alpaca, your API credentials:

```powershell
Copy-Item .env.example .env
# Set TYPESAFE_API_KEY; set ALPACA_API_KEY and ALPACA_SECRET_KEY for Alpaca
uv run ft
```

Open [localhost:8765](http://127.0.0.1:8765/) to analyze a market. Alpaca is the default; the dashboard also offers Yahoo Finance. The automatic SPY summary uses Alpaca.

For a single ticker:

```powershell
uv run ft analyze NVDA --timeframe 5m
```

For a batch:

```powershell
uv run ft analyze-batch AAPL MSFT NVDA --concurrency 3
```

Add `--source yahoo` to either command for Yahoo Finance. Model requests may incur charges.

[Usage guide](docs/usage.md) · [API reference](http://127.0.0.1:8765/docs) · [License](LICENSE)
