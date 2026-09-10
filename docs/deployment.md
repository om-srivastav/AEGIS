# AEGIS deployment

AEGIS is deployed as one web service.

The production image uses a two-stage Docker build:

1. Node builds the React/Vite dashboard.
2. The compiled `frontend/dist` output is copied into `backend/app/static`.
3. FastAPI serves `/`, the Vite `/assets/*` bundle, `/health`, and `/api/*` from the same origin.

This keeps the production browser on one origin and avoids a separate frontend/backend CORS configuration.

## Build locally with Docker

From the repository root:

```bash
docker build -t aegis .
```

Run:

```bash
docker run --rm -p 8000:8000 aegis
```

Then verify:

```text
http://127.0.0.1:8000/
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
```

The container honors a platform-provided `PORT` environment variable and otherwise uses port `8000`.

## Production configuration

The deterministic AEGIS path is the safe deployment default and requires no provider secret.

Model-backed roles are opt-in. Configure role/provider/model settings and API keys only through the deployment platform's secret environment-variable controls. Never commit provider keys to the repository or expose them to the frontend.

## Smoke-test expectations

A production deployment is ready only after all of the following are verified against the deployed URL:

- `/health` returns `status: ok`.
- `/` renders the AEGIS dashboard.
- a baseline run completes.
- a moved-resource run fails deterministically.
- diagnosis returns a supported grounded result.
- the bounded repair can be proposed.
- replay/evaluation accepts the demonstrated repair.
- clean and moved-resource cases can be placed in the regression vault.
- suite evaluation reports the expected measured improvement with no fabricated results.

External live-model verification is tracked separately from deterministic product deployment. A provider outage must not be represented as an AEGIS task regression.
