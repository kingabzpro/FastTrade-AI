# Volatility sizing implementation plan

> Execute inline with test-driven development and independent final review.

**Goal:** Add an opt-in, risk-checked fixed-versus-volatility sizing replay of a backtest's recorded decisions, and publish a reproducible frozen-data comparison for #11.

**Architecture:** A separate deterministic replay preserves the existing backtest and its flat/impact reports. Both variants use the same recorded model decisions, execution bars, flat costs, per-symbol cash buckets and explicitly supplied portfolio limits. The targeted variant scales an equal-weight target by `min(1, target_vol / annualized_daily_vol)` using only completed prior sessions; missing inputs make the comparison unavailable. Every proposed trade is clipped to cash, position and exposure limits, then checked by `risk.evaluate` before any simulated fill. Each evaluated sizing state and outcome is recorded with a hash; no orders are placed.

**Tech stack:** Python, pandas, existing risk engine and frozen backtest datasets. No local Docker or new provider requests.

**Spec:** [Issue #11](https://github.com/kingabzpro/FastTrade-AI/issues/11).

## Constraints and review focus

- Default backtests and their report fields remain unchanged unless sizing is explicitly enabled.
- Require explicit position, exposure, daily-loss, trade-count and confidence limits; do not infer user risk settings.
- Use only decision-time information for volatility and cash dividends; execution prices affect fills, not the earlier volatility estimate.
- Carry cash, positions, cash-dividend receivables, fees and slippage consistently; reject or report missing data, never assume zero.
- SELL may only reduce a long position; HOLD changes nothing; no leverage or negative cash. Risk rejection wins over sizing.
- Show both runs' assumptions and audit states. The published replay uses illustrative limits, not a live-trading recommendation.
- Drawdown throttle, calibrated half-Kelly, impact as the primary simulation and the original H1 ledger remain separate outstanding work.

## Tasks

1. Write failing tests for target weights, validation, temporal isolation and risk-blocked trades; implement the deterministic replay and state hash.
2. Add explicit CLI configuration and an opt-in backtest report block. Test the disabled default and missing/invalid configuration.
3. Replay the frozen flagship decisions offline under identical fixed and targeted limits. Verify both and document costs, input history and limitations.
4. Run the full suite and independent financial review, then remote CI. Update #11 without closing it while remaining tasks are incomplete.
