# Decision-probability calibration, 2026-09-28 backtest

The earlier calibration report and its `calibration.json` artifact are **superseded**. They split the October 16 review across the training and test sets and bootstrapped individual stocks from the same review as if they were separate time observations. The previously reported positive confidence interval and edge certification are not reliable. The original artifact is retained for audit; the current analyzer rejects it because it lacks outcome-availability metadata and will not accept its legacy edge certification.

## Corrected replay

The corrected calculation uses the recorded decisions in [report-measured.json](backtest-2026-09-28/report-measured.json) and the frozen CSVs in that directory. It groups decisions by New York calendar week, keeps each week on one side of the split, and purges training labels whose exit price was not yet available at the first test decision. Only exact execution-open timestamps are accepted. No model requests are made.

| Data | Count |
| --- | ---: |
| Decisions with a following execution open | 75 |
| Training decisions, October 2 review | 25 |
| Purged training decisions, October 9 review | 25 |
| Test decisions, October 16 review | 25 |

The 75-pair ledger has a 70.67% up rate. The fitted temperature reaches its upper bound, 19.999992. On the single test review, raw versus temperature-scaled Brier scores are 0.2226 and 0.2439; isotonic is 0.2619. These are descriptive results from one test week, not evidence of an out-of-sample improvement. The test contains 12 BUY decisions but only **one** BUY review period, so the corrected edge gate reports insufficient periods and no confidence interval or certification. The full 37-BUY ledger spans only three review periods and is also insufficient.

The revised edge bootstrap resamples entire weekly review clusters, weighted by the number of BUY decisions in each cluster, and requires at least ten distinct BUY review periods before returning a verdict. Ten is a screening minimum, not a guarantee of statistical power. The bootstrap assumes the sampled periods are suitable for this method; it does not correct for selection among variants, retrospective universe selection, or model training leakage.

## Rerun

Choose a new output path; the command refuses to overwrite the historical artifact:

```powershell
uv run ft calibrate --report .\docs\backtest-2026-09-28\report-measured.json --data-dir .\docs\backtest-2026-09-28 --train-frac 0.75 --alpha 0.1 --output .\calibration-corrected.json
```

The report and data files are committed with a dataset manifest; the calibration command does not itself verify that manifest. The data vendor may revise prices or corporate actions, and the model may know the historical period. The corrected result does not establish a live edge.
