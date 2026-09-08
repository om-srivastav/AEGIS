# Mission 04 Execution Report

## Scope
Mission 04 adds immutable regression-case registration, frozen evaluation-suite snapshots, fresh paired execution for every suite member, deterministic transition classification, regression-aware gating, and execution-derived reliability metrics.

## Verification
Command:

```bash
PYTHONPATH=backend pytest -q backend/tests
```

Result: **43 passed**.

A real three-case deterministic suite was executed with:

- current moved-resource failure
- clean sales-report case
- historical field-renamed failure

Observed transitions:

- current: `FAIL -> PASS` = `improved`
- clean: `PASS -> PASS` = `preserved`
- historical: `FAIL -> FAIL` = `unresolved`

Gate decision: **ACCEPT**.

Metrics for that exact suite:

- scheduled cases: 3
- valid pairs: 3/3
- baseline passes: 1/3
- candidate passes: 2/3
- improvements: 1
- regressions: 0
- unresolved failures: 1
- preserved: 1
- paired net change: +1/3

## Regression-rejection feasibility result
Mission 04 gate logic includes a tested `PASS -> FAIL = regression -> REJECT` path. However, a genuine end-to-end rejection was **not fabricated** because the current bounded re-observation policy does not create a pass-to-fail transition on any existing deterministic perturbation.

Verified built-in perturbation sweep:

- none: pass -> pass
- file_moved: fail -> pass
- field_renamed: fail -> fail
- tool_timeout: fail -> fail
- partial_response: fail -> fail

Therefore the current safe candidate has no legitimate regression case in the existing deterministic domain. A future mission can add a separately justified candidate or task contract that can genuinely regress; the existing controller was not weakened to manufacture a demo.

## Causal language
The accepted claim remains scoped to the pinned deterministic suite. Mission 04 does not establish population reliability, statistical significance, or a unique root cause.
