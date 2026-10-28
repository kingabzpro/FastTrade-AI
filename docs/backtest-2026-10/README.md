# October 2026 backtest inputs

This example uses AAPL, MSFT, NVDA and SPY, with a September 28–October 23, 2026 hourly warmup and test snapshot downloaded from Yahoo Finance through yfinance with `auto_adjust=False`. The October 5–23 test made six model decisions; the first execution was October 12. Session times follow the [NYSE 2026 calendar](https://www.nyse.com/publicdocs/nyse/ICE_NYSE_2026_Yearly_Trading_Calendar.pdf).

Yahoo's daily data reported no dividends or stock splits for these symbols between September 28 and the latest available trading day when fetched; see `actions-audit.json`. This is a provider check, not an independently audited corporate-action history. Yahoo may apply historical split adjustments or revise data even when `auto_adjust=False`; the manifest is an attestation, not proof that all inputs are point-in-time raw. The sample and period were chosen retrospectively, and the model may have learned information about them. Do not describe this example as leak-free or evidence of a live edge.

The report marks positions at the final close without liquidation costs. The strategy and SPY baseline both include 5 bps commission and 10 bps slippage on executed trades; the simulation omits market impact, partial fills, financing, taxes and portfolio risk checks.
