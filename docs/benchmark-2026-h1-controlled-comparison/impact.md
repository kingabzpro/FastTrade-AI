# H1 controlled-snapshot impact replay

This replays the recorded `new-report.json` decisions on the checksummed adjusted-hourly snapshot, without provider calls. It is a different run and snapshot from the original H1 daily-price ledger, so it does not reconstruct that ledger's impact costs. Its recorded no-cost portfolio return is 3.72%, versus 8.73% for SPY; the replay verifies both original ending balances and every recorded execution price before applying costs.

| Commission and impact multiplier | Portfolio return | SPY return |
| --- | ---: | ---: |
| 0.5× | +3.49% | +8.70% |
| 1× | +3.26% | +8.67% |
| 2× | +2.79% | +8.60% |

The model uses 5 bps commission at 1× and the same uncalibrated square-root impact assumptions described in the [flagship replay](../backtest-2026-09-28/README.md). All 189 fills, including SPY, hit the 1 bps impact floor before scaling. The shortest available history had 19 valid sessions. Some input windows contained up to two incomplete sessions: missing days and returns crossing them are excluded, not interpolated. These cost assumptions omit spread and partial fills; the floor and coefficient are not observed execution costs, and the cap can understate large orders.

Sessions use the pinned `exchange-calendars` XNYS schedule, including early closes; this is a software calendar, not independent proof of historical trading. The February 2 execution occurred at 13:30 New York time because the earlier common bars were missing; the replay uses that recorded time. Prices are Yahoo-adjusted while volume is not adjusted by the same process, so inferred shares and historical volume may have different adjustment bases. Data may be revised. The model decisions and universe are retrospective, and these numbers do not establish a trading edge. The original H1 ledger does not preserve its matching historical volume, so its requested impact replay remains unavailable from the archived ledger alone.

Reproduce with `uv run --locked python docs/replay_impact_costs.py --h1`. The report SHA-256 is `c1ebeb9224ed60c3c75c0c536a65df39c10ae7ddc3f80a0c9331b7f9294a2141`; the snapshot manifest SHA-256 is `f68a48316e232a93332d07ce4351c4d5bd09759eea383add7e5705160ff9df68`. The command verifies every snapshot file against that manifest and emits per-fill liquidity inputs and costs.
