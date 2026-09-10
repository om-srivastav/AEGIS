# AEGIS — 60–120 Second Demo Guide

## Production URL

**https://aegis-production-2df6.up.railway.app**

Refresh immediately before recording because hosted state is process-local.

## Script and click sequence

**Intro — 10 sec**

> AEGIS is an AI-agent reliability lab. It stress-tests an agent, records what happened, grounds diagnosis in evidence, proposes a bounded repair, replays the failure, and regression-tests whether reliability actually improved.

**Baseline — 10 sec**

Select **Baseline** -> `Run experiment`.

Show `PASSED`, `100% validator score`, trace complete. Add the clean run to the vault.

**Failure — 10–15 sec**

Select **Moved resource** -> `Run experiment`.

Show `FAILED`, `0%`, and `FileNotFoundError`.

**Diagnosis — 10 sec**

Click `Diagnose run`.

Show `SUPPORTED`, evidence references, and the grounded diagnosis.

**Repair + replay — 15 sec**

Click `Propose bounded repair`.

Show:

```text
resource_reobservation
enabled: false -> true
```

Click `Replay & evaluate` and show `ACCEPT`.

**Regression + measure — 15–20 sec**

Add the moved-resource case to the vault.

Run the suite and finish on:

```text
2/2 valid pairs
1 improvement
0 regressions
0 unresolved
50% baseline success
100% candidate success
FAIL -> PASS
PASS -> PASS
```

**Closing line — 5 sec**

> AEGIS doesn't ask a model whether the agent improved. It re-executes the evidence, checks regressions deterministically, and measures the difference.

## Do not claim

- universal reliability;
- causal proof beyond the scoped intervention;
- that live model diagnosis passed;
- that a provider outage is an AEGIS task regression;
- durable history across restarts.
