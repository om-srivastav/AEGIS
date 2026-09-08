# Mission 05 — First model-backed agent and grounded model diagnosis

Mission 05 adds real-model integration boundaries without replacing the deterministic AEGIS core.

## What is implemented

AEGIS now has two independent model roles:

1. **Agent Under Test** — `ModelAgent` uses a provider-neutral `ModelTransport` and can call only `list_files`, `read_file`, and `write_report` through a bounded controller.
2. **Diagnostic ReasoningProvider** — `ModelReasoningProvider` receives only an allowlisted grounded evidence projection and returns the existing `DiagnosisCandidate` type. The Mission 02 `DiagnosisValidator` remains authoritative before a repair can be created.

The deterministic scripted agent and `MockProvider` remain the default configuration, so existing runs and tests require no API credentials.

## Security / authority boundaries

- No shell tool.
- No arbitrary host filesystem access.
- No arbitrary network tool exposed to the Agent Under Test.
- The model never chooses the report destination; `write_report` always commits to the existing virtual `report.json` location.
- The model can write an incorrect report, but it cannot change the deterministic validator. Execution completion and task correctness are separate outcomes.
- Unknown tools, malformed arguments, duplicate call IDs, parallel calls, invalid virtual paths, and budget exhaustion fail closed before unapproved sandbox actions.
- Provider credentials are read from environment variables only and are not accepted through API requests.
- Diagnostic model input excludes scenario fixture snapshots and other hidden environment state. Model/provider trace events are excluded from fact extraction so generated diagnoses cannot recursively become facts.

## Provider-neutral contracts

Core interfaces live in:

- `backend/app/providers/model_contracts.py`
- `backend/app/providers/model_session.py`
- `backend/app/agents/adapter.py`

The first production transport implementation is `AnthropicTransport`. Provider-specific wire formats stay inside that transport; the model-agent loop and diagnosis provider depend only on `ModelTransport`.

This implementation does **not** make AEGIS dependent on any one provider. Additional transports can be added without changing deterministic validation, repair, replay, or regression-gate logic.

## Configuration

Copy values from `.env.example` into your deployment environment. AEGIS does not automatically load `.env` files.

Defaults:

```text
AEGIS_AGENT_ADAPTER=deterministic
AEGIS_REASONING_PROVIDER=deterministic
```

To enable the model Agent Under Test, set the `AEGIS_AUT_*` variables. To enable model diagnosis, set the `AEGIS_DIAG_*` variables. The two roles use independent model names, credentials, limits, and transport instances.

## Budgets

`Limits` enforces:

- model steps;
- tool calls;
- provider requests;
- total token accounting;
- output-token cap;
- request/result byte caps;
- wall-clock deadline;
- per-provider-request timeout.

Timed-out or cancelled generation can leave provider usage uncertain. AEGIS records `usage_unknown` instead of treating unobserved usage as zero.

## Trace integration

Model/provider activity is stored using the existing `TraceEvent` schema with `kind="model"` and a namespaced event name in the event message/payload. Native virtual-filesystem tool events remain separate evidence of the actual sandbox action.

Key event families include:

- `agent.started`
- `model.request.prepared`
- `model.request`
- `provider.operation.*`
- `provider.http.*`
- `model.response`
- `agent.tool.proposed`
- `agent.tool.dispatched`
- native `tool_call` / `tool_result`
- `agent.tool.returned`
- `agent.completed` / `agent.failed`
- `diagnosis.model.*`

## Deterministic verification

Mission 04 baseline before changes:

```bash
PYTHONPATH=backend pytest -q backend/tests
```

Result before Mission 05 changes: **43 passed**.

Mission 05 deterministic suite:

```bash
PYTHONPATH=backend pytest -q backend/tests -m "not live"
```

Verified result: **68 passed, 2 deselected**.

Full test collection with live tests disabled by configuration:

```bash
PYTHONPATH=backend pytest -q backend/tests
```

Verified result: **68 passed, 2 skipped**.

Offline coverage includes tool-boundary rejection, duplicate IDs, parallel calls, budgets, deterministic task authority, real Mission 04 diagnosis-schema round trips, hidden-truth projection checks, mocked provider HTTP behavior, independent role configuration, and provider/harness regression invalidation.

## Optional live tests

Live tests are opt-in and require explicit provider credentials:

```text
AEGIS_RUN_LIVE_TESTS=1
```

Then configure `AEGIS_AUT_*` and/or `AEGIS_DIAG_*` and run the tests marked `live`.

No live provider test was executed in the verified Mission 05 build environment, so this checkpoint makes **no claim** about an actual external model run, model quality, or live provider compatibility.

## Frontend verification limitation

The frontend source was not materially changed in Mission 05. An attempted dependency installation/build (`npm install --no-audit --no-fund && npm run build`) timed out in the execution environment before a frontend build result was available. No frontend build success is claimed for this checkpoint.

## Causal / reliability language

Mission 05 introduces model-backed execution plumbing; it does not by itself establish new reliability statistics. Deterministic validators and regression gates remain authoritative. Provider outages are treated as invalid infrastructure evidence rather than genuine task regressions.
