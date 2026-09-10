# AEGIS deployment

Recommend **Railway Hobby**, using the GitHub-connected root Dockerfile as one web service. This keeps the existing FastAPI/Vite architecture and all provider-independent reliability semantics.

M07 container build and hosted deployment are **UNVERIFIED** until the platform build and deployed-URL smoke test succeed. M05 live diagnosis/combined remain **BLOCKED / UNVERIFIED** because the saved external provider attempts returned HTTP 503. Deterministic deployment verification does not change that status.

## Railway versus Koyeb for this repository

Reviewed against official documentation on 2026-09-10. Prices are published plan/instance prices, not measured AEGIS bills.

| Concern | Railway | Koyeb |
| --- | --- | --- |
| Existing GitHub + multi-stage Dockerfile | Detects the root `Dockerfile`; no separate frontend service needed. | Supports GitHub Dockerfile builds and multi-stage images; choose the Dockerfile builder. |
| Network configuration | Supplies `PORT`, which the image reads; configure `/health` and generate a public domain. | Configure an exposed HTTP port and route `/` to it; align the process `PORT`, for example 8000. |
| Idle behavior and AEGIS history | Keep Serverless disabled and one replica. | The free instance scales to zero after one idle hour, losing this app's process-memory state. Paid instances can be configured to stay running. |
| Health checks | `/health` gates activation of a new deployment; it is not continuous health monitoring. | Supports ongoing TCP/HTTP health checks; use HTTP `/health` rather than only the default TCP check. |
| Cost | Hobby has a $5/month minimum including $5 of resource usage; total charges can exceed $5. | A free instance exists; paid `eco-micro` is listed at $2.68/month for 512 MB. That instance price is not a complete bill or an AEGIS capacity guarantee. |

**Decision:** Railway Hobby is the simplest fit for this single-service portfolio deployment: root Dockerfile detection and the existing `PORT`/health contract match directly. Koyeb paid hosting is also technically valid and may cost less; its free tier is a poor fit for preserving this demo's history between visits. This recommendation is about configuration fit, not a claim that Railway has better measured uptime or performance.

Sources: [Railway Dockerfiles](https://docs.railway.com/builds/dockerfiles), [Railway pricing](https://docs.railway.com/pricing/plans), [Railway health checks](https://docs.railway.com/deployments/healthchecks), [Railway Serverless](https://docs.railway.com/deployments/serverless), [Koyeb Git builds](https://www.koyeb.com/docs/build-and-deploy/build-from-git), [Koyeb instances and free limits](https://www.koyeb.com/docs/reference/instances), [Koyeb health checks](https://www.koyeb.com/docs/run-and-scale/health-checks).

## Production architecture

1. The Node stage installs the committed lockfile with `npm ci` and builds Vite.
2. The Python stage installs the backend requirements and copies the compiled `frontend/dist` into `backend/app/static`.
3. FastAPI serves `/`, fingerprinted `/assets/*`, `/health`, and `/api/*` from one origin.

There is no production Vite server and no additional CORS requirement. Leave `VITE_API_URL` unset: the frontend defaults to relative API URLs. Vite configuration is compiled into the browser bundle, so runtime variables do not change an already-built frontend. Never put provider keys in `VITE_*` values or Docker build arguments.

The existing conditional `/assets` mount is correct: the image includes assets before FastAPI imports the app, while backend-only development remains supported. The tracked fallback HTML is replaced by the compiled index during the image build. The dashboard does not currently require SPA path fallback routing; unknown API routes must remain 404.

The image binds `0.0.0.0`, reads `${PORT:-8000}`, and uses an explicit single worker. `EXPOSE 8000` documents the default; it does not publish the port or override `PORT`. The shell expands the port, then `exec` replaces it with Uvicorn so container termination reaches the server. [Dockerfile reference](https://docs.docker.com/reference/dockerfile/), [Uvicorn worker settings](https://uvicorn.dev/settings/#production).

The lockfile resolves Vite 8.2.2 and React plugin 6.1.1, both requiring Node `^20.19.0 || >=22.12.0`; the Node 22 build image must resolve to a supported patch version. Keep the lockfile. There is no demonstrated reason to replace the build system or upgrade application dependencies in this review.

## State and runtime constraints

Runs, diagnoses, repairs, evaluations, regression cases, and idempotency records are held in process-local dictionaries. This has concrete deployment consequences:

- Use **one Uvicorn worker, one replica, and one region**. Multiple workers or replicas have independent stores and can return missing IDs for another process's records. The Docker command fixes workers to one even if `WEB_CONCURRENCY` is set.
- Disable sleeping/scale-to-zero. History still resets on restart, replacement deployment, or crash. Re-run the demo after a deployment; an old open browser may hold IDs from the previous process.
- A volume alone would not persist Python dictionaries. This deployment does not add a database, authentication, or a multi-user state model.
- Public visitors share the one demo store. Keep the public portfolio deployment deterministic with no provider credentials. Memory grows with retained runs; measure actual memory use and refresh the demo process when needed rather than claiming durable or unlimited history.

This is the current bounded demo contract. Persistence or multi-user operation would require a separate, explicitly scoped change.

## Railway setup

No service was created or published as part of this review.

1. Create a project from the canonical GitHub repository and select `main` once the reviewed changes have been committed/pushed by the owner.
2. Set the source root to the folder containing `Dockerfile`, `frontend/`, and `backend/`. For this repository that is the repository root, not `backend/` or `frontend/`.
3. Confirm the build logs identify the root Dockerfile. Leave build/start overrides empty so the image's build stages and `CMD` remain authoritative; the final stage is `runtime`.
4. Configure a health-check path of `/health`, one replica in one region, and Serverless disabled.
5. Set `AEGIS_AGENT_ADAPTER=deterministic` and `AEGIS_REASONING_PROVIDER=deterministic`. Leave both live-test flags disabled/unset, and leave all provider keys and `VITE_API_URL` unset for the public demo.
6. Generate a public domain under Networking. Use the application's supplied `PORT` as the target port. If explicitly setting `PORT=8000`, match the public target port to 8000 as well.
7. Inspect build/start logs and run the deployed-URL checks below. A green `/health` alone does not validate the frontend or AEGIS loop.

The platform can build from GitHub without Docker on this Windows machine. That first remote Linux image build remains required evidence; the local frontend build does not substitute for it.

## Build locally when Docker is available

From the repository root:

```bash
docker build -t aegis .
docker run --rm -p 8000:8000 aegis
```

To verify platform-style port selection:

```bash
docker run --rm -e PORT=8080 -p 8080:8080 aegis
```

Use `http://127.0.0.1:8000/` for the default command and port 8080 for the second command. Test shutdown with `docker stop` and confirm graceful Uvicorn shutdown in the logs. Actual Linux signal handling has not been tested here.

`.dockerignore` excludes root and nested real environment files, local artifacts, dependencies, and caches from the relevant build context; example environment files remain allowed. Keep credentials in runtime secret controls only for a separately intended model-enabled deployment. Both model roles continue to have independent provider/model/key/budget settings, with Gemini and Anthropic support unchanged.

## Deployed-URL smoke test

After each successful hosted build, verify all of the following against its public origin:

- `/health` returns `status: ok`.
- `/` loads the compiled dashboard, and its referenced JavaScript/CSS asset URLs return 200 with the correct content types.
- Missing assets and unknown API endpoints return 404.
- A baseline run passes deterministic task validation.
- A moved-resource run fails deterministically and retains its trace/validation evidence.
- Deterministic diagnosis returns supported grounding, and the bounded repair can be proposed.
- Replay accepts the demonstrated repair.
- Clean and moved cases enter the regression vault; suite evaluation reports 2/2 valid pairs, 1 improvement, 0 regressions, and 50% to 100% case success for this two-case experiment.
- Run, diagnosis, and evaluation IDs can be fetched again from the same service.

These expected values are specific to the deterministic two-case demo. They are not a fabricated reliability score or a real-model verification claim.

## Review verification at base commit dbb8c03

- Canonical checkout identified on `main`; working tree was clean before review.
- Backend offline suite: **169 passed, 3 deselected, 1 existing warning**. Full suite: **169 passed, 3 skipped, 1 existing warning**. Both had zero failures/errors; all live-provider tests were disabled.
- Frontend: fresh `npm ci` from the committed lockfile and production build passed on Windows Node 24.18.1/npm 11.16.0. The Linux Node 22 image has not been built here.
- A temporary copy of tracked source plus compiled `dist` was served through a real local Uvicorn process. HTML and both fingerprinted assets matched build bytes, content types were correct, and missing assets, traversal attempts, and unknown API routes returned 404.
- The full deterministic API flow passed over local HTTP: **8/8 stages**, **2/2 valid pairs**, **1 improvement**, **0 regressions**. Repeat lookups succeeded. This exercised production file layout, not a container or a new browser/UI verification.
- A POSIX shell argument probe checked default/empty/custom port expansion and quoting. The actual local server ran with `--workers 1` while `WEB_CONCURRENCY=4` was present.
- **Docker build, container startup/shutdown, and hosted deployment: UNVERIFIED.** Docker/Podman are not installed here. No external model calls were made.

Review changes are limited to `Dockerfile` (process replacement, quoted port, one worker), `.dockerignore` (nested environment files and local artifacts), and this deployment guide. All backend/frontend application code, provider adapters, validators, tests, and dependency files remain unchanged. No commit or push was performed.
