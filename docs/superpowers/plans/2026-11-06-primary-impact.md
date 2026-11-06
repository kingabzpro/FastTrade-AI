# Impact-based primary backtest implementation plan

> Execute inline with test-driven development and independent financial review.

**Goal:** Make square-root impact the default backtest execution model, with explicit flat-mode compatibility and reproducible sensitivity comparisons for #11.

**Architecture:** Use one shared fill calculator for the primary simulation and impact replay. Compute liquidity from completed sessions at the decision cutoff, validate it before paid model requests, and record per-fill assumptions. Preserve flat execution via `--slippage-model flat`; keep both sensitivity replays and label which model drives the main result. Update archived-replay commands to select flat explicitly.

**Tech stack:** Python, pandas, existing impact estimator and backtest engine; no local Docker or provider requests.

**Spec:** [Issue #11](https://github.com/kingabzpro/FastTrade-AI/issues/11).

## Constraints and review focus

- Default changes are explicit and documented; legacy flat behavior remains selectable and historical artifacts stay unchanged.
- Reject unavailable required liquidity before any model request rather than silently using zero cost.
- Both BUY and SELL, SPY, commissions, dividend receivables, equity curve and significance use the selected primary model.
- Buy quantity and impact must be solved together; cap/floor and cost sensitivity must use the same implementation as the primary fills.
- Preserve report consumers with explicit slippage-model metadata and avoid presenting the flat comparison rate as the primary impact rate.
- Verify unchanged flat results, primary impact versus 1× replay, temporal isolation, missing data, both sides, and remote CI.
- Impact parameters remain uncalibrated assumptions; no partial fills, spread or live-order claims.

## Tasks

1. Add failing tests for default impact, explicit flat compatibility, missing-liquidity preflight and matching 1× replay; implement shared fill calculations.
2. Update callers, archived offline replays, documentation and tests for the default change; check primary ledgers, SPY and confidence intervals.
3. Run full tests and independent financial review, fix findings and run remote CI. Update #11 without closing while half-Kelly and original H1 evidence remain incomplete.
