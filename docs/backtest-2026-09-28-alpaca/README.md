# Alpaca IEX backtest: September–October 2026

This retrospective comparison uses the same 25-stock universe, September 28–October 27 dates, Jev 1.14.0 model, weekly reviews, $10,000 starting balance, 5 bps commission and 10 bps slippage as the [earlier Yahoo-based backtest](../backtest-2026-09-28/README.md). The universe was selected with hindsight. The source is Alpaca's IEX feed rather than Yahoo. The model is nondeterministic, so the data source is not the only possible cause of differences.

| Measure | Alpaca IEX | Yahoo reference |
| --- | ---: | ---: |
| Portfolio return | +2.04% | +2.31% |
| SPY return | −0.17% | −0.15% |
| Portfolio vs. SPY | +2.21 percentage points | +2.46 percentage points |
| Ending balance | $10,204.14 | $10,230.73 |
| Maximum drawdown (daily closes) | −1.12% | −1.08% |
| Trading fees | $6.36 | $6.37 |

The Alpaca run made 100 model decisions, with no retries, 80,718 reported input tokens and a mean model request time of 746.13 ms. Of the 100 matching decision times and tickers, 94 actions matched the Yahoo reference. Model request latency does not include market-data retrieval. Model charges are separate from simulated returns.

The [report](report.json) records each decision, trade, daily equity value, model usage and the snapshot hash. The snapshot contains Alpaca `iex` raw one-minute bars aggregated into NYSE-session-aligned hourly bars using the first open, highest high, lowest low, last close and sum of IEX volumes. It excludes trades outside each recorded session. `prepare.py` captures the preparation method; the resulting hourly CSVs are frozen here. The session calendar and corporate-action attestations are copied from the earlier Yahoo snapshot and were not independently verified against Alpaca.

IEX is one exchange, not the consolidated market. An hourly bar's first IEX trade can occur after the nominal start; for opening prices used in this backtest, five of 104 symbol/session observations started late, by at most two minutes. **One executed trade (IBM on October 12) used a first IEX trade two minutes after the session open** even though the backtest records the execution timestamp as 09:30. The other four delayed observations did not result in an executed stock trade. See [opening delays](opening-delays.json). Fills, fees and slippage are simulated; no liquidity or market impact is modeled.

The data may be revised, the model may know subsequent events, and the same historical sample was used for comparison. This is not evidence of a live trading edge or proof of future performance. The [Alpaca FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) explains the difference between IEX and consolidated SIP data. Dividends, missing corporate actions, and model-training leakage remain limitations described in the original report.

To rerun the frozen snapshot (model requests may be charged), choose a new output path:

```powershell
$symbols = (Get-Content docs/backtest-2026-09-28-alpaca/symbols.txt -Raw).Trim()
uv run ft backtest --data-dir docs/backtest-2026-09-28-alpaca --start 2026-09-28 --end 2026-10-27 --symbols $symbols --model jev-1.14.0 --output .\alpaca-rerun.json
```
