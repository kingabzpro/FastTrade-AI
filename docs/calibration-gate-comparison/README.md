# Calibration gate comparison, 2026-08-03 to 2026-10-30

Controlled comparison of the same benchmark run with and without the calibration gate. Both arms replay the identical 36 recorded decisions (AAPL, MSFT, NVDA, weekly, model `jev-1.14.0`) at the identical execution prices; only the gate differs, so no model calls were re-run and nothing but certification changed. The ungated replay reproduces the engine's return exactly, which validates the replay rules.

| Arm | Return | vs SPY (-2.19%) | Trades | BUYs blocked |
| --- | --- | --- | --- | --- |
| Raw decisions (engine) | **+1.18%** | +3.37 pp | 13 | - |
| Raw decisions (replay, sanity check) | +1.18% | +3.37 pp | 13 | 0 |
| Calibration gate on | **0.00%** | +2.19 pp | 0 | 12 (12 direction, 0 return) |

Directional accuracy was 45.45%, yet the raw arm beat SPY by 3.37 points: the accuracy proxy and returns disagree again, as expected for long-only positioning in a falling window.

## Reading

- On this window the gate did not improve returns; it traded nothing and earned nothing. It blocked all 12 BUYs at the direction step: with 56 training decisions, the direction quantile (0.5305) exceeds any calibrated deviation from 0.5, so no signal can be certified. The return gate never got a chance to discriminate.
- Across the three published windows the raw strategy made +2.31%, +1.18%, and +6.60% versus SPY's -0.15%, -2.19%, and +9.35%. The gate is equivalent to holding cash: it beats SPY when SPY falls and forgoes the raw strategy's gains when it wins. It is a floor, not an edge.
- The comparison's real output is the mechanism: certification currently requires more calibration data (or a sharper signal) than the ledger holds. When either arrives, the gate opens trade by trade, and the return gate insists each certified BUY clear round-trip costs, not merely point up.

## Files and rerun

- `benchmark-2026-08-10.json`: the fresh benchmark report (36 decisions with probabilities and prices).
- `replay.py`: both arms from one report; no model calls.

```powershell
uv run ft benchmark --start 2026-08-03 --end 2026-10-30 --symbols AAPL,MSFT,NVDA
uv run python docs/calibration-gate-comparison/replay.py docs/calibration-gate-comparison/benchmark-2026-08-10.json docs/backtest-2026-09-28/calibration.json
```

The benchmark makes one paid model request per decision (36 here). The replay makes none.
