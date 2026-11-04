# Time-series forecast implementation plan

> Execute inline with the executing-plans skill; verify each behavior with a failing test first.

**Goal:** Evaluate a pinned local time-series model as an optional, unvalidated decision input on the frozen backtest (#13).

**Architecture:** Chronos-Bolt Tiny runs on CPU using the last 50 validated completed closes and returns next-bar price quantiles. The single-decision state carries the model revision, window, horizon and quantiles, and the backtest records that input and runs matching forecast-on and forecast-off decisions. The separate five-session relative-to-SPY specialist in the multi-agent design remains unavailable until that workflow exists; a next-bar price forecast is not a substitute.

**Tech stack:** Python, optional pinned `chronos-forecasting`, PyTorch, frozen Alpaca IEX dataset.

**Spec:** [Issue #13](https://github.com/kingabzpro/FastTrade-AI/issues/13) and `docs/superpowers/plans/2026-10-28-multi-agent-trading-research.md`.

## Global constraints

- Default analysis behavior and dependencies remain unchanged; forecast use is explicit.
- Pin both package version and model weight revision; never silently download a newer revision.
- Only completed candles available at the decision cutoff enter the 50-bar window.
- Label output unvalidated, include the next-bar horizon and record it in the report and state hash.
- Do not combine forecast-augmented inputs with existing calibration artifacts.
- Keep the user-requested local Docker shutdown; CI may test containers remotely.

## Review focus

- Future candles and a different amount of earlier history must not change the forecast input at the cutoff.
- Missing dependencies, unavailable weights and malformed or crossed quantiles must fail explicitly.
- The optional model must not alter default calls, and calibration must not be applied to changed inputs.
- Reported forecast provenance must describe the exact weights and window actually used.
- Paired experiments must use the same dataset, model, decision times, execution assumptions and request counts; report token usage and cost differences honestly rather than assume equal billing.

## Tasks

1. Add tests for optional forecast input, cutoff isolation, validation and calibration rejection. Implement a CPU adapter using pinned `amazon/chronos-bolt-tiny` revision `a0e552de83495b5c28c14c71c374f3e33280b340`, then run a real local inference and document measured resource usage.
2. Add tests for forecast opt-in through CLI and backtest, state hashes and recorded provenance; implement the smallest integration that preserves the existing default path. Verify the complete Python suite.
3. Run matched forecast-on and forecast-off experiments on the checksummed frozen dataset, with identical decision-model and execution settings. Publish commands, reports, token counts, model version, wall time and limitations; do not claim a causal result from an unmatched or failed run.
4. Independently review, run remote CI, merge the tested change, and update #13. Close only if the issue's acceptance criteria, including the multi-agent specialist requirement, are genuinely met.
