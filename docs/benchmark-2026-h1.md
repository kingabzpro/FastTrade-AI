# Weekly benchmark: January–June 2026

Run on 28 October 2026 using `jev-1.14.0`: 25 retrospectively selected stocks, reviewed at the end of each trading week from 2 January to 26 June (650 decisions). [Download the decision and trade ledger](benchmark-2026-h1.csv).

| Metric | Method |
| --- | --- |
| Portfolio | Start with $10,000 split into 25 independent $400 stock allocations. A BUY invests the allocation's available cash at the next trading day's open; SELL liquidates that allocation's shares at the next open; HOLD keeps its existing cash or position. Each allocation stays assigned to its stock. Fractional shares are allowed, cash earns zero, and remaining positions are valued at the 30 June close. The investment period is 5 January–30 June 2026. |
| SPY | Invest $10,000 at the 5 January open and value it at the 30 June close. Yahoo's adjusted prices were $681.219228 and $744.920227: $10,935.10. This baseline uses the same investment dates as the portfolio. |
| Prices | The model received historical unadjusted Yahoo 60-minute candles through the last trading day of each week. Execution and valuation use Yahoo daily adjusted prices, an approximation that accounts for corporate actions and assumes reinvested distributions. Actual fills can differ. |
| Directional accuracy | A BUY is correct if the stock's adjusted price rises from the next open to the next weekly execution open, and SELL is correct if it falls; the final week uses the 30 June close. Zero returns are incorrect and HOLDs are excluded. SELL means reducing an existing long position in the app, so this is a directional proxy rather than a trade-success rate. |
| Speed | Model request latency was measured during concurrent local CSV analysis (up to six workers), including network time and excluding market-data download. |
| Cost | 533,415 input tokens across 650 successful decisions and two invalid responses retried successfully, estimated at [TypeSafe's published rate](https://typesafe.ai/) of $0.042 per million input tokens. Actual billing can differ. |

The 650 recorded decisions have a median model-request latency of **858 ms** and a 95th percentile of **1,004 ms**. The recorded local CSV analysis timing was **914 ms** at the median and **1,072 ms** at the 95th percentile. These use the `model_ms` and `total_ms` columns in the linked ledger, the median and nearest-rank 95th percentile, respectively; the timing may exclude failed attempts before a retry. The run included network calls to the model and up to six concurrent workers; it excluded live market-data fetching and is not a production API load test. Provider conditions and hardware can change these numbers.

Replaying the recorded decisions with flat simulated costs changes the hypothetical results as follows. Commission and slippage are each charged per executed side; SPY pays entry costs and neither strategy pays a hypothetical final liquidation cost. The adjusted-price approximation and all other limitations remain. Reproduce without model calls with `uv run --locked python docs/replay_h1_costs.py`.

| Commission / slippage per side | Portfolio return | SPY return |
| --- | ---: | ---: |
| None (original) | +6.60% | +9.35% |
| 2.5 / 5 bps | +6.02% | +9.27% |
| 5 / 10 bps | +5.44% | +9.19% |
| 10 / 20 bps | +4.28% | +9.02% |

These are fixed-decision replays with flat assumptions, not market-impact estimates. They do not model spreads, liquidity, partial fills, taxes, or decisions changing with portfolio conditions.

## Controlled two-variant comparison

A separate [frozen adjusted-hourly dataset](benchmark-2026-h1-controlled-comparison/manifest.json) contains two controlled Jev 1.14.0 runs: a baseline and a version with two additional market features. They share the same dates, symbols, model and execution rules, use retrospectively adjusted hourly prices, and omit commissions and slippage. Their results are not the original daily-price benchmark above. Using the 25 complete execution-to-execution weeks, the mean portfolio-minus-SPY return was −0.159 percentage points per week for the baseline and −0.162 for the new variant. A joint, centered stationary-bootstrap White reality check over these **two recorded variants** gives a family p-value of 0.799 (2,000 draws, seed 0, mean block length 3 weeks). The 95% marginal intervals are −0.574 to +0.291 and −0.583 to +0.285 percentage points per week, respectively; they are not family-adjusted intervals. The final partial period is excluded.

The test does not establish that these were all variants ever considered, and thus does not correct for the full historical search. The universe was selected retrospectively, the model may know the test period, and the bootstrap depends on its time-series assumptions. This result does not establish live performance. Reproduce it without provider calls using `uv run --locked python docs/benchmark-2026-h1-controlled-comparison/reality_check.py`; the script checks the frozen snapshot hashes and reconstructs both portfolios before comparing them. See [White's original paper](https://users.ssc.wisc.edu/~behansen/718/White2000.pdf) for the family test.

The original hypothetical results exclude commissions, spreads, slippage, taxes and financing. Decisions are model preferences, not the app's risk-checked actionable signals: the simulation applies its own allocation rule and does not feed positions into the model. Without portfolio and risk inputs, the app displays the model preference as signal only. News and company details were omitted. The model was evaluated after the test period, so prior knowledge or model changes may affect results. The limited retrospective sample does not establish live accuracy or profitability, and historical prices may be revised.
