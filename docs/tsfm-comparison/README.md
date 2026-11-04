# Next-bar forecast comparison

This is an exploratory comparison on the frozen [Alpaca IEX dataset](../backtest-2026-09-28-alpaca/README.md), not evidence of a trading edge. The same 25 symbols, four review dates, 100 decision requests per run, Jev 1.14.0 model, $10,000 starting capital, 5 bps commission and 10 bps slippage were used. The forecast run includes an unvalidated next-hour-bar price forecast from the last 50 completed hourly closes. It is not the five-session relative-to-SPY forecast described by the proposed multi-agent workflow.

| Measure | Baseline | Forecast input |
| --- | ---: | ---: |
| Portfolio return | 2.30% | 2.10% |
| SPY return | -0.17% | -0.17% |
| Maximum drawdown | -1.08% | -1.02% |
| Trading fees | $6.37 | $5.96 |
| Reported input tokens | 80,718 | 98,266 |
| Reported output tokens | 3,930 | 3,944 |
| Model retries | 0 | 0 |

The runs made the same number of requests but did not use the same number of tokens, so they are not necessarily equal in provider cost. Prices and charges were not recorded. Sixteen of the 100 paired actions differed. The model is nondeterministic; a single run per condition cannot isolate forecast effects from run-to-run variation. The short sample has three complete weekly return periods, and the universe was selected with hindsight. The dataset can contain revisions, and the decision model may know later events. This test does not establish model calibration, point-in-time vendor data, statistical significance or future performance.

The model is `amazon/chronos-bolt-tiny` at revision `a0e552de83495b5c28c14c71c374f3e33280b340`, used with `chronos-forecasting` 2.3.2. Its weights' historical availability and training data have not been verified against the test dates; the forecast itself can contain hindsight independently of the Jev model. On the Windows CPU used here, a first inference including model download and initialization took 22.7 seconds and a warm inference took 0.005 seconds; these are single measurements, not a benchmark or resource guarantee. Forecast dependencies and model weights are optional and are downloaded on first use. Outputs are unvalidated and must not be treated as a standalone trading signal.

The full outputs are [baseline.json](baseline.json) and [forecast.json](forecast.json). Their SHA-256 values are `f67e09d101cd7378ee38f51fa2782e685552fa598bf1fe13644f1952f04b7aac` and `66a9551f95d8028e27d2a7d5f00d230c39fc05d5cf58c5c4874d1f29b1535f5b`, respectively. Both record the same dataset manifest hash. Each forecast record includes the weight revision, quantiles, window and horizon; the input is also covered by the decision's state hash.

Re-running incurs model-provider charges and can yield different results. In PowerShell, with the decision-provider credentials configured and new output paths:

```powershell
$symbols = (Get-Content docs/backtest-2026-09-28-alpaca/symbols.txt -Raw).Trim()
uv run --locked --extra forecast ft backtest --data-dir docs/backtest-2026-09-28-alpaca --start 2026-09-28 --end 2026-10-27 --symbols $symbols --model jev-1.14.0 --output baseline-new.json
uv run --locked --extra forecast ft backtest --data-dir docs/backtest-2026-09-28-alpaca --start 2026-09-28 --end 2026-10-27 --symbols $symbols --model jev-1.14.0 --forecast --output forecast-new.json
```
