# Decision-probability calibration, 2026-09-28 backtest

Post-hoc calibration of the decision model's P(BUY) against realized weekly outcomes, fitted on the [flagship frozen backtest](backtest-2026-09-28/README.md) (25 symbols, model `jev-1.14.0`, 100 recorded decisions). Research only: these mappings are fitted on a retrospective backtest and are not advice.

## Data and split

- Each decision's recorded P(BUY) is paired with the realized move from its execution open to that symbol's next weekly execution open, taken from the frozen, SHA-256-checksummed dataset CSVs.
- 75 usable pairs: 100 decisions minus the 25 final reviews that have no next execution. No pair was dropped for missing price bars.
- 70.67% of the pairs saw the price rise to the next execution open.
- Chronological 75/25 split: 56 training decisions (no training decision is later than any test decision; the boundary falls inside the October 16 review), 19 test decisions, all from that same final review.

## Results

Fitted temperature: **19.999992**, the upper bound of the search range. The likelihood prefers near-total flattening toward 0.5, so temperature scaling here acts as shrinkage, not a genuine fit. Read it as: the raw P(BUY) carries almost no weekly directional information in this dataset.

| Window | Metric | Raw | Temperature | Isotonic |
| --- | --- | --- | --- | --- |
| Train (n=56) | ECE | 0.3868 | 0.2195 | 0.0000 |
| Train (n=56) | Brier | 0.3717 | 0.2522 | 0.1836 |
| Test (n=19) | ECE | 0.3721 | 0.2245 | 0.0689 |
| Test (n=19) | Brier | 0.2452 | 0.2464 | 0.1897 |

Isotonic improves the test Brier (0.2452 to 0.1897) and ECE (0.3721 to 0.0689). Its zero training error is interpolation, not skill, and with 19 test points the numbers are uncertain. The model is overconfident in both directions: low P(BUY) values under-rated symbols that rose.

Reliability on the 19 test pairs, before calibration (5 bins):

| P(BUY) bin | Count | Mean P(BUY) | Rose to next open |
| --- | --- | --- | --- |
| 0.0-0.2 | 5 | 0.126 | 0.20 |
| 0.2-0.4 | 4 | 0.258 | 0.75 |
| 0.4-0.6 | 7 | 0.536 | 1.00 |
| 0.6-0.8 | 2 | 0.660 | 0.50 |
| 0.8-1.0 | 1 | 0.860 | 1.00 |

After temperature scaling, all 19 test predictions fall into the 0.4-0.6 bin (mean 0.493 against a 0.684 realized rate): flatter, and no longer claiming confidence it does not have.

Conformal gate (split-conformal on training residuals, alpha 0.1): quantile 0.5305, test interval coverage 0.9474, and an eligible rate of **0.0**: every test interval contains 0.5, so the gate certifies no BUY at this sample size. That is the honest reading of the data, not a bug.

## Return-aware gate

Direction is not profitability, so the fit also maps raw P(BUY) to the realized next-open move (isotonic on returns, not just the binary direction) and bounds the uncertainty with a split-conformal quantile on training residuals. A BUY is certified only when the return interval's lower bound clears the round-trip cost, 2 x (fee + slippage) = 30 bps at the defaults.

- Train mean move +0.97%, test mean move +1.03%; the isotonic expectation averaged +1.18% on test.
- The return quantile is 0.0750: weekly move uncertainty of 7.5 percentage points against a 0.30% cost bar. Every test interval's lower bound sits far below costs, so the gate certifies **0 of 19** test signals.
- Reading: with 56 training decisions, weekly uncertainty dwarfs any plausible edge. The gate reopens only when the ledger grows or the signal genuinely sharpens; it cannot be loosened honestly from this data alone. Its value is the guarantee it will enforce once either happens: certified trades must clear costs, not merely point up.

## Limitations

- 75 pairs and a 19-pair test window; every number here is uncertain.
- The symbol universe was chosen with hindsight and the model may know the period (see the [backtest limitations](backtest-2026-09-28/README.md)).
- The artifact records `fit_horizon: 60m` and a weekly target; applying it to other horizons through `analyze --calibration` is an extrapolation and is labeled as such in the response.
- Calibration quality is measured against price direction, not profitability.

## Rerun

```powershell
uv run ft calibrate --report .\docs\backtest-2026-09-28\report-measured.json --data-dir .\docs\backtest-2026-09-28 --train-frac 0.75 --alpha 0.1 --output .\docs\backtest-2026-09-28\calibration.json
```

The artifact (`calibration.json`, refuse-to-overwrite) carries the fitted temperature, isotonic blocks, split windows, and conformal quantile; `analyze --calibration` consumes it.
