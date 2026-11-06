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

Replaying the recorded decisions yields three complete execution-to-execution weeks. The mean weekly portfolio-minus-SPY return is +1.201 percentage points; a seeded stationary-bootstrap 95% interval is +0.377 to +2.024 percentage points per week. The final partial period is excluded. **Three weeks are too few for a dependable interval**, and this calculation does not adjust for variant selection or leakage. It also inherits the two-minute IBM opening delay described above. Reproduce the replay without provider calls with `uv run --locked pytest tests/test_backtest.py::test_other_recorded_backtests_replay_significance_without_model_calls -q`; the test checks the original balances, fees and manifest hash.

The same recorded decisions can be replayed under identical illustrative risk limits with fixed or volatility-scaled BUY sizing. Using a 10% annual volatility target, $400 maximum position, $10,000 maximum exposure, $10,000 maximum daily loss, 25 maximum trades per day and zero minimum confidence, the fixed replay returned **2.04%** and the volatility-scaled replay **1.21%**. Both use 5 bps commission and 10 bps slippage per side. The fixed replay made 32 simulated fills and the scaled replay 38; the 43 calculated BUY weights ranged from 0.259 to 0.747. The shortest volatility window had nine completed sessions and eight adjacent returns. The position limit is an illustrative cap, and these limits are not a recommendation.

This scales BUY entries or additions; it does not automatically rebalance existing positions when volatility changes or on HOLD, and it does not guarantee portfolio volatility. Recorded SELL decisions attempt a full exit subject to the same risk engine. The replay computes portfolio values, clips quantities, checks risk, and fills at the same session opening prints. That is an optimistic simulation: opening prices generally cannot all be observed before an order receives those same prices. The model decisions are fixed, the data may be revised, and the short, hindsight-selected sample does not establish an edge. The IEX feed and delayed opening fills noted above remain limitations. The original Yahoo flagship report lacks recorded confidence values, so it is not used for this risk-checked comparison.

Reproduce without provider calls using `uv run --locked python docs/replay_sizing.py`. It verifies the original balances, fees and dataset manifest, then prints the comparison, assumptions and hashed per-trade sizing states. The source report SHA-256 is `bce2ece2ddcdb31c8329a2c64fef519b94f22c52bacba0ad20183e55b99730dc`; the dataset manifest SHA-256 is `cf9392006fcd098cff829c6b697ac51131d74ef161e7b908305dd9491622053e`.

As a separate illustration, `uv run --locked python docs/replay_sizing.py --drawdown .02` enables the same 2% drawdown throttle for both variants with those illustrative limits. Fixed sizing returned 2.02% with 29 automatic reduction fills; volatility-scaled BUY sizing returned 1.21% with none. The close and opening checks are discrete, use optimistic same-open fills, and cannot guarantee the drawdown cap. Fees can cause repeated reductions. Automatic SELLs use confidence zero and are rejected by any positive minimum-confidence limit; other risk limits can also block them. These results are assumptions on the same short historical sample, not evidence that a 2% limit or either sizing method is suitable.

To rerun the frozen snapshot (model requests may be charged), choose a new output path:

```powershell
$symbols = (Get-Content docs/backtest-2026-09-28-alpaca/symbols.txt -Raw).Trim()
uv run ft backtest --data-dir docs/backtest-2026-09-28-alpaca --start 2026-09-28 --end 2026-10-27 --symbols $symbols --model jev-1.14.0 --output .\alpaca-rerun.json
```
