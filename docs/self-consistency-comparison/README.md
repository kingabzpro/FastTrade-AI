# Self-consistency sampling vs single call, frozen flagship backtest

Same frozen dataset, same 100 decisions, same model (`jev-1.14.0`), same rules as the [measured report](../backtest-2026-09-28/README.md); the only change is `--samples 5`. Both runs are committed: the [single-call report](../backtest-2026-09-28/report-measured.json) and [report-samples5.json](report-samples5.json).

## Portfolio outcome

| | Single call | Samples 5 |
| --- | --- | --- |
| Return | +2.31% | **+2.31%** |
| Ending balance | $10,230.73 | $10,230.73 |
| Max drawdown | -1.08% | -1.08% |
| Total fees | $6.37 | $6.37 |
| Identical actions | - | 99 of 100 (the one difference is portfolio-neutral) |

## Speed and cost (the answer to "what does it cost")

| | Single call | Samples 5 | Ratio |
| --- | --- | --- | --- |
| Model requests | 100 | 500 | 5.00x |
| Input tokens | 81,703 | 408,515 | 5.00x |
| Estimated cost at TypeSafe's published $0.042/M input tokens | $0.00343 | $0.01716 | 5.00x |
| Cost per 1,000 decisions | $0.0343 | $0.1716 | 5.00x |
| Mean model time per decision (sequential implementation, as measured) | 599 ms | 4,171 ms | ~7x (5 requests plus slower per-request latency during the batch: 834 ms vs 599 ms) |

Sampling now runs its votes concurrently: wall time for a 5-vote decision measured 1.37 s live (2026-10-31, dashboard sample mode) against 4.17 s sequential, i.e. the slowest of the five requests plus overhead rather than their sum. The remaining gap to a sub-second 5-vote decision is provider inference time (single-request p50 is 858 ms, p95 1,004 ms on the H1 ledger), which client-side parallelism cannot compress.

From this comparison onward, `mean_model_latency_ms` in reports is per request (divided by each decision's sample count); the committed samples-5 report predates that normalization and holds per-decision sums.

Non-model overhead (data fetch, indicators, risk) measured on the H1 ledger: 54 ms of a 915 ms decision, about 6%. The model call is effectively all of the time and all of the cost.

## Finding

On this provider configuration the model is nearly deterministic: five votes on the same snapshot almost never disagree (99/100 identical actions, identical portfolio to the cent, probability means within 0.068 of the single call). For a deterministic decision model that is the expected outcome: identical input implies identical output, and this run's role is to document it empirically so nobody re-pays for the premise. Self-consistency sampling therefore bought nothing here and multiplied cost by exactly 5 and decision latency by about 7. Keep the implementation: it is opt-in, one flag, and becomes valuable only with a model configuration that has real sampling temperature, where vote dispersion carries signal. For this one, the single call is the right default, which is what the flag default already encodes.

## Where speed and cost can actually improve

- The model request is about 94% of per-decision time and 100% of marginal cost, so client-side tricks cap out fast: caching candle fetches and indicators saves the remaining ~6% on the CLI path (more for repeated identical dashboard requests); async execution and workers raise throughput under load, not single-decision latency.
- The real lever is request shape at the provider: TypeSafe's [fan-out pattern](https://docs.typesafe.ai/patterns/fan-out) packs many typed questions into one System One call, and their cookbook measures 13 questions in one call at 11.5x cheaper and 9.6x faster than 13 separate calls. That is the design the multi-agent phase ([roadmap Phase 5](../../ROADMAP.md)) should use: one batched specialist call, not seven requests.
- Sampling cost scales linearly with N; if a nondeterministic model configuration is ever used, N=3 is the cheapest version worth testing first.

## Rerun

```powershell
uv run ft backtest --data-dir .\docs\backtest-2026-09-28 --start 2026-09-28 --end 2026-10-27 --symbols (Get-Content .\docs\backtest-2026-09-28\symbols.txt) --model jev-1.14.0 --samples 5 --output .\docs\self-consistency-comparison\report-samples5.json
```

500 paid model requests, about $0.017 at the published pricing.
