# AEGIS Architecture — Final M07 Overview

## Core loop

```text
TEST -> STRESS -> FAIL -> DIAGNOSE -> REPAIR -> REPLAY -> REGRESSION TEST -> MEASURE
```

AEGIS is an engineering laboratory for evaluating and improving autonomous-agent reliability under controlled, replayable conditions.

## Governing rule

**Models may reason; deterministic systems establish facts.**

A model may interpret a trajectory, propose hypotheses, or suggest a bounded repair. It cannot fabricate tool results, environment state, trace events, validator outcomes, regression results, or measured improvement.

## System flow

```text
Scenario / Perturbation
        |
        v
   Agent Under Test
        |
        v
  Tool / Environment
        |
        v
 Ordered Trace + Artifacts
        |
        +----------------------+
        |                      |
        v                      v
Deterministic Validator   Evidence Projection
        |                      |
        |                      v
        |               ReasoningProvider
        |                      |
        |                      v
        |              DiagnosisValidator
        |                      |
        +-----------+----------+
                    |
                    v
            Constrained Repair
                    |
                    v
               Fresh Replay
                    |
                    v
            Regression Suite
                    |
                    v
           Reliability Metrics
```

## Mission summary

**M01 — Deterministic foundation.** FastAPI, virtual file environment, scripted Sales Report Agent, ordered traces, controlled perturbations, and deterministic validators.

**M02 — Grounded diagnosis.** Executed facts, failure observations, evidence references, `ReasoningProvider`, and `DiagnosisValidator`. AEGIS reports an **earliest supported causal candidate**, not causal proof.

**M03 — Constrained repair + fresh replay.** The current bounded repair changes:

```text
resource_reobservation.enabled: false -> true
```

Recovery is limited to one file listing and one exact logical-resource retry. Fresh baseline/candidate runs are compared under matched conditions.

**M04 — Regression vault + deterministic gate.** Immutable/versioned cases, frozen suite snapshots, fresh re-execution, and transition classification:

- `PASS -> PASS`: preserved
- `FAIL -> PASS`: improved
- `PASS -> FAIL`: regression
- `FAIL -> FAIL`: unresolved

**M05 — Provider-neutral model boundary.** Independent model Agent Under Test and model-backed diagnostic ReasoningProvider behind provider-neutral transports. Deterministic validation remains authoritative. The real Gemini Agent Under Test live path succeeded; live diagnosis and combined live flow remain **BLOCKED / UNVERIFIED** after external HTTP 503 responses.

**M06 — Reliability dashboard.** React/TypeScript/Vite UI for the full reliability loop against the real FastAPI backend.

**M07 — Production delivery.** Single Docker-built Railway service. Node builds Vite, compiled assets are copied into `backend/app/static`, and FastAPI serves `/`, `/assets/*`, `/health`, and `/api/*` from one origin.

## State model

Runs, diagnoses, repairs, evaluations, regression cases, and idempotency records are currently process-local.

Therefore:

- one Uvicorn worker and one Railway replica are required;
- restart/redeploy resets history;
- old browser-held IDs may return `run not found` after process replacement;
- this is a bounded portfolio/demo deployment, not a durable multi-user service.

## Final verified state

- Backend full suite with live tests disabled: **169 passed, 3 skipped, 1 existing warning**
- Frontend production build: **PASS**
- Public Railway deployment: **ACTIVE**
- Deployed deterministic loop: **8/8 stages verified**
- Deployed suite: **2/2 valid pairs, 1 improvement, 0 regressions, 0 unresolved**
- Baseline success: **50%**
- Candidate success: **100%**

These metrics apply only to the pinned deterministic two-case experiment.
