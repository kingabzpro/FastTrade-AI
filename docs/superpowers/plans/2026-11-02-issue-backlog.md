# Issue Backlog Plan

**Goal:** Finish existing issues in small, verifiable increments while keeping the service single-agent and signal-only.

1. Verify existing dashboard work on the default branch against #17 and #18, including mobile, keyboard and reduced-motion checks. Close only issues whose acceptance criteria are met.
2. Add CI for the existing locked Python and chart tests as an independently deliverable part of #4; complete deployment work after the security foundation is ready.
3. Prioritize #1 security and cost controls before network exposure, then #2 performance and #3 batch work. Audit their descriptions against current Alpaca and batch code before implementation.
4. Address dashboard CSV input #20 and optional local history #22; preserve data validation, privacy and no-order behavior.
5. Evaluate #8–#11 research using reproducible data and methods before making performance claims. Defer new providers #19 and #21 until their value and API requirements are clear.
6. Leave multi-agent #5 closed and its dependent research #14 last. Revisit #12 and #13 only if their API, compute and evidence requirements justify the work.

Each issue stays open until its acceptance criteria are verified on the default branch. No additional paid API calls are required for the CI work.
