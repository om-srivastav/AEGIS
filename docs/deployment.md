# AEGIS Deployment — Railway Production

## Production URL

**https://aegis-production-2df6.up.railway.app**

AEGIS is deployed as one GitHub-connected Railway service from the repository-root Dockerfile.

## Architecture

1. Node installs the frontend lockfile and builds React/Vite.
2. Python installs FastAPI backend requirements.
3. `frontend/dist` is copied into `backend/app/static`.
4. FastAPI serves the UI and API from one origin.

```text
/            -> AEGIS dashboard
/assets/*    -> Vite assets
/health      -> health endpoint
/api/*       -> AEGIS backend
/docs        -> FastAPI docs
```

The image binds `0.0.0.0`, uses Railway's `PORT`, and starts one Uvicorn worker.

## Hosted verification

The Railway deployment built successfully and became **ACTIVE**.

The public deterministic smoke test verified:

```text
TEST        PASS
STRESS      PASS
FAIL        PASS
DIAGNOSE    PASS
REPAIR      PASS
REPLAY      PASS
REGRESSION  PASS
MEASURE     PASS
```

Final suite result:

```text
Valid pairs:        2/2
Improvements:       1
Regressions:        0
Unresolved:         0
Baseline success:   50%
Candidate success: 100%

Current stress: FAIL -> PASS
Clean case:     PASS -> PASS
Gate:           ACCEPT
```

## Runtime limitation

Current AEGIS history is stored in process memory. Restart, crash, or redeployment resets runs, diagnoses, repairs, evaluations, regression-vault entries, and idempotency records.

This was directly observed during hosted smoke testing: an old browser-held run ID returned `run not found` after process replacement; refreshing showed a clean empty process state.

Use one worker and one replica for this demo. Durable persistence would require a separately scoped persistent store.

## Public-demo policy

Keep the public portfolio deployment deterministic and provider-independent. Do not expose provider credentials through frontend variables, build arguments, source files, or public logs.

## M05 live-model status

Production deployment does not change the M05 live-model result:

- real Gemini Agent Under Test: **verified successful locally**
- live diagnosis: **BLOCKED / UNVERIFIED** after provider HTTP 503
- combined live flow: **UNVERIFIED**

Provider outages are not task regressions.
