# Historical calibration-gate replay, 2026-08-03 to 2026-10-30

This folder preserves a **superseded** comparison. The legacy artifact split a single review between training and test and treated correlated same-week stock decisions as separate time observations. Its reported positive edge interval and certification are not reliable. It also used an artifact created after the replay period, so the comparison is not point-in-time valid.

The historical replay of the recorded 36 decisions produced +1.18% for both the raw and legacy gated arms, versus −2.19% for SPY. Those numbers are retained as a record of what the old code did; they do not demonstrate that the current gate would have been open, establish an edge, or provide a walk-forward comparison. The corrected calibration on the frozen flagship data has only one test review and returns no edge certification; see [the corrected calibration](../calibration-2026-09-28.md).

`replay.py` intentionally reproduces the legacy gate and prints a warning. It does not call a model:

```powershell
uv run python docs/calibration-gate-comparison/replay.py docs/calibration-gate-comparison/benchmark-2026-08-10.json docs/backtest-2026-09-28/calibration.json
```

Do not use the retained legacy artifact with the current analyzer; it lacks the outcome-availability fields now required to prevent using future calibration information.
