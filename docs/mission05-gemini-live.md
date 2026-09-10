# Mission 05 Live: Gemini integration

Gemini is an additional `ModelTransport`. Deterministic execution remains the default. Mission 06 is outside this change.

## Provider behavior

`GeminiTransport` uses the official `https://generativelanguage.googleapis.com` origin and the `v1beta` REST endpoints for `generateContent`, `countTokens`, and an explicit model-availability preflight. Keys are sent only in the `x-goog-api-key` header, with TLS verification enabled, environment proxies disabled, redirects disabled, and no retries or provider/model fallback.

The adapter maps text, function calls, and function responses to the existing neutral contract. It sends the existing tool JSON schemas via `parametersJsonSchema` and the existing diagnosis schema via `responseJsonSchema`. Server-side schema hints do not replace local validation.

Gemini may omit function-call IDs. Such calls receive a local `gemini_<uuid>_<part-index>` correlation ID. These IDs are not claimed as provider-issued IDs. Provider-issued IDs, response IDs, request IDs when supplied, and model versions are preserved. Tool-result names and optional provider IDs are matched to their originating call.

Opaque thought signatures and the original response-part boundaries are retained in a transport-local, weakly referenced cache keyed by the actual returned `Message` object. This supports M05's existing in-memory conversation loop and concurrent sessions without adding Gemini fields to the neutral contracts. Thinking summaries are excluded from normalized task text; wire content remains in the existing audited HTTP trace. Copied/restored tool messages without their opaque state fail closed, and changed messages/model names cannot reuse it. This does not introduce durable conversation resumption.

Token admission calls `countTokens` with the complete generation input, including system instructions, tools, schema, and continuation content. Reported prompt tokens count once, including cached tokens. Reported candidate and thinking tokens both count toward output and total limits. Missing or inconsistent accounting fails closed. The existing session marks uncertain generation usage as unknown.

HTTP payloads and responses are capped at 262,144 bytes in addition to the existing core request, tool-result, step, token, and time limits. Each HTTP operation has a whole-operation deadline as well as HTTP timeouts. Unsupported response content cannot cause a tool dispatch. Blocked prompts that omit a completion/usage become explicit provider/harness failures; candidate refusals and output truncation use existing finish semantics.

## Preserved authority boundaries

- `ModelTransport`, `ModelSession`, and `AnthropicTransport` are unchanged.
- The existing controller enforces `list_files`, `read_file`, and `write_report` at dispatch, including argument, virtual-path, duplicate-ID, and parallel-call rejection.
- The runner does not compute or repair a model-produced answer. The existing deterministic report validator can fail a completed execution.
- Model diagnosis receives only the existing grounded evidence projection. Its existing schema decoder and `DiagnosisValidator` remain authoritative.
- Actual mocked Gemini HTTP outages are checked through the runner and regression pair classifier: provider/harness failures yield invalid comparison evidence rather than genuine regressions.
- Agent and diagnostic providers, models, keys, and limits remain independent. No role borrows another role's settings.
- No API key comes from an API request or source file. Configuration metadata and traces use the existing safe boundaries; the Gemini HTTP audit additionally scrubs its own key.

## Verification on 2026-09-09

Canonical repository: `C:\Users\omsri\Downloads\aegis_mission03_repair_replay\aegis`

Base commit: `c6b6109914b66ff4ca9587a69966b66772264556`, branch `main`.

Python: **3.12.10**.

Before this change: **68 passed, 2 deselected, 1 warning in 1.23s**.

After this change:

```text
158 passed, 3 deselected, 1 warning in 3.24s
```

Command: `.\.venv\Scripts\python.exe -m pytest backend/tests -m 'not live' -q --tb=short`, with `PYTHONPATH=backend` and live execution disabled. This includes 90 new deterministic cases, including mocked execution of the opt-in live wiring.

The existing Starlette/AnyIO `BlockingPortal` deprecation warning is unchanged. Windows denied access to the existing system pytest temp directory; the successful run used a fresh workspace directory through `PYTEST_DEBUG_TEMPROOT`. No tests were excluded to work around that problem.

Explicit disabled-live check: **3 skipped in 0.28s**, using `.\.venv\Scripts\python.exe -m pytest backend/tests/live -q -rs --tb=short` with `AEGIS_RUN_LIVE_TESTS=0`.

`git diff --check` passed. The PowerShell launcher's syntax check reported zero errors. Its credential prompts and real network execution were not run.

## Live verification: UNVERIFIED

Neither `AEGIS_AUT_API_KEY` nor `AEGIS_DIAG_API_KEY` was present in the implementation session. No Gemini model lookup, token count, generation, agent run, or diagnosis was executed against a real API key. All observed model responses in tests were mocked.

`gemini-3.7-flash` is only the requested candidate, not a claimed account entitlement. Before each Gemini live role starts, the test performs a credential-scoped `models.get` check for that exact model and requires `generateContent` support. A missing model, authorization failure, quota failure, or unsupported request stops the check; it never chooses another model. A successful metadata lookup still does not prove that generation will succeed or establish billing/free-tier eligibility.

The launcher executes agent-only, diagnosis-only, then combined verification, stopping on the first failure. The combined test requires a separate opt-in. The agent check requires a real submitted report and the existing deterministic validation results. The diagnostic check requires successful provider metadata and accepted grounding; a valid empty hypothesis set may be inconclusive. Provider failure and rejected grounding cannot pass it.

Expected outcomes, not observed live results: the agent writes a report accepted by the deterministic validator; diagnosis returns a locally valid, grounded candidate; the combined run retains both roles' metadata. Model nondeterminism, unavailable models, refusals, budget limits, quotas, or API compatibility may prevent those outcomes. There is no retry-until-success.

## Safe local execution

Open PowerShell and run:

```powershell
Set-Location -LiteralPath 'C:\Users\omsri\Downloads\aegis_mission03_repair_replay\aegis'
& .\scripts\mission05_gemini_live.ps1
```

The script reruns deterministic verification, asks for each role's key using a hidden prompt, assigns process-only `AEGIS_AUT_API_KEY` and `AEGIS_DIAG_API_KEY`, checks each exact requested model with that role's key, and runs the live checks sequentially. It clears the keys and disables live execution in `finally`. The same legitimate key can be entered at both separate prompts. Do not put a real key in a command literal, `.env`, Git, a screenshot, or chat.

An explicitly chosen alternative model can be supplied using `-AgentModel 'MODEL_ID' -DiagnosticModel 'MODEL_ID'`. Each still undergoes its own key-scoped check; no alternative is selected automatically.

Live evidence is saved under `artifacts/m05-live/<unique-session>/`, which is ignored by Git. Evidence includes the model preflight, run records, real report values recorded by the validator, tool/provider traces, and diagnosis when produced. Failed runs are saved before success assertions. A directory containing evidence does not itself mean the tests passed.

The tests directly instantiate the selected roles, so application mode defaults remain deterministic. To enable the application separately after verification, explicitly set `AEGIS_AGENT_ADAPTER=model` and/or `AEGIS_REASONING_PROVIDER=model` in the backend process and supply the corresponding role's environment variables. AEGIS does not automatically load `.env` files.

## API references consulted

- [GenerateContent API and usage metadata](https://ai.google.dev/api/generate-content)
- [Token counting API](https://ai.google.dev/api/tokens)
- [Model metadata and availability API](https://ai.google.dev/api/models)
- [Function calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Thinking and signature semantics](https://ai.google.dev/gemini-api/docs/thinking)
