# Calibration gate comparison, 2026-08-03 to 2026-10-30

Controlled comparison of the same benchmark run with and without the calibration gate (v3, aggregate certification). Both arms replay the identical 36 recorded decisions (AAPL, MSFT, NVDA, weekly, model `jev-1.14.0`) at the identical execution prices; only the gate differs, so no model calls are re-run. The ungated replay reproduces the engine's return exactly, which validates the replay rules. The benchmark was run twice (2026-10-30 and 2026-10-31) and produced identical decisions and returns.

| Arm | Return | vs SPY (-2.19%) | Trades | BUYs blocked |
| --- | --- | --- | --- | --- |
| Raw decisions (engine and replay) | **+1.18%** | +3.37 pp | 13 | 0 |
| v3 gate (aggregate certification) | **+1.18%** | +3.37 pp | 13 | 0 |
| v2 gate (per-trade conformal, prior revision) | 0.00% | +2.19 pp | 0 | 12 |

The artifact ([docs/backtest-2026-09-28/calibration.json](../backtest-2026-09-28/calibration.json)) certifies aggregate net-of-cost edge on its test window (mean +2.90% per BUY decision, 95% bootstrap CI [+1.64%, +4.45%], n=10), so the v3 gate is open and the gated arm captures the full +1.18% that the v2 per-trade gate forfeited. The refusal mechanism is unchanged for uncertified models: an artifact whose test-window CI lower bound is at or below zero blocks every BUY and holds cash (pinned by tests).

Directional accuracy was 45.45%, yet the strategy beat SPY by 3.37 points: the accuracy proxy and returns disagree, which is why certification targets net excess return rather than direction.

## Reading and caveats

- v2 asked "can this individual trade be certified?" and the honest answer in weekly equity markets is never: single-stock weekly moves deviate about 7.5% at the 90th percentile against a 0.30% cost bar, at any sample size. v3 asks the certifiable question: "does the mean net excess return per BUY decision beat zero across the test window?" That CI shrinks as the ledger grows.
- The certification and this replay window overlap (the artifact's test window is the October 16 review, inside August-October), so this is a mechanism demonstration, not a walk-forward proof. A walk-forward version needs a ledger that predates the traded window; the daily-candle benchmark is the path there.
- Certification says the measured edge on the frozen flagship dataset is distinguishable from zero after costs, with 10 test BUY decisions from a hindsight-selected universe. It is the strongest honest claim that data supports, not a promise of persistence.

## Files and rerun

- `benchmark-2026-08-10.json`: the benchmark report (36 decisions with probabilities and prices).
- `replay.py`: both arms from one report; no model calls.

```powershell
uv run ft benchmark --start 2026-08-03 --end 2026-10-30 --symbols AAPL,MSFT,NVDA
uv run python docs/calibration-gate-comparison/replay.py docs/calibration-gate-comparison/benchmark-2026-08-10.json docs/backtest-2026-09-28/calibration.json
```

The benchmark makes one paid model request per decision (36 here). The replay makes none.
