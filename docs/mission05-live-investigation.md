# M05 Live investigation and continuation — 2026-09-10

**M05 Live: BLOCKED. Fresh external-provider verification: UNVERIFIED. M06 has not started.**

Work continued from the supplied `AEGIS_M05_Gemini_Live_Astra_Handoff.zip`, after reading `ASTRA_HANDOFF_M05_LIVE.md` and inspecting the repository. No replacement project was created. Original archive SHA-256: `786062610ff4c18a6625eee2a6b1fb2ba634b44a2a1869357c9934d515df983b`.

## Engineering conclusion

The two saved diagnosis failures are explicitly provider-reported overload at `generateContent`: HTTP 503, status `UNAVAILABLE`, with a high-demand message. The evidence supports transient provider unavailability as the observed failure, rather than an established transport or configuration defect. It cannot prove that unavailability was the only issue: neither diagnosis completed generation, so exact-model structured-output compatibility and grounded output remain unverified.

No transport, prompt, schema, model identifier, budget, or retry change is justified by this evidence. Preserve both failed attempts. A later operator-initiated verification is a new recorded attempt, with one execution of each stage and a stop on the first failure; it must not be automated into repeated attempts until a pass occurs.

Google documents 503 as service unavailability and distinguishes it from malformed requests and unavailable model identifiers. Its general retry advice does not override this project's no-retry verification policy. [Troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting), [API errors](https://ai.google.dev/gemini-api/docs/api-errors).

## What the supplied external-provider evidence establishes

These are historical records supplied in the ZIP, not fresh provider calls made during this continuation.

| Saved session under `artifacts/m05-live/` | Agent deterministic result | Diagnosis token count | Diagnosis generation | Combined |
| --- | --- | --- | --- | --- |
| `1de9c882-5f19-436f-b8ff-154d88074498` | PASS | HTTP 200; 2,368 tokens | HTTP 503 / UNAVAILABLE | No record |
| `f22551c3-37a2-4e99-8ba2-365d29df0144` | PASS | HTTP 200; 2,363 tokens | HTTP 503 / UNAVAILABLE | No record |

Both roles' saved credential-scoped `models.get` calls returned:

- Exact resource: `models/gemini-3.7-flash`.
- Metadata version: `3.7-flash-08-2026`.
- Methods: `generateContent`, `countTokens`, `createCachedContent`, `batchGenerateContent`.
- Limits: 1,048,576 input tokens and 65,536 output tokens.
- Successful agent responses resolved to `gemini-3.7-flash`.

Thus this identifier is established by supplied account-scoped evidence, not guessed from a model catalog. Current entitlement and availability must still be checked using each role's current key. `models.get` does not itself certify structured-output support or successful generation. [Models API](https://ai.google.dev/api/models).

Each failed diagnosis generation payload was 8,822 bytes using the transport's actual serializer. It used `v1beta`, `systemInstruction`, text-only grounded evidence, `candidateCount=1`, `maxOutputTokens=4096`, `responseMimeType=application/json`, and the canonical `responseJsonSchema`. It contained no tools or thought-signature continuation. Its schema matches the unchanged local `DiagnosisCandidate` schema exactly, with no `$ref` sibling conflicts. Schema SHA-256: `2d14a6b1f74de1f0c6d15b12ca303c8cd12af4dd2acde12095ef707c28d036b3`.

The API reference documents these generation fields and JSON Schema constraints. A successful `countTokens` call does not establish that generation accepts every feature. There is no recorded 400 schema error, 404 model error, local request-size rejection, or transport timeout to justify rewriting this request. [GenerateContent reference](https://ai.google.dev/api/generate-content#GenerationConfig).

Diagnosis metadata correctly records `provider_harness`, `provider_http_503`, `validation_status=provider_failure`, and `usage_unknown=true`. Zero recorded generation usage is not a claim that the failed call used zero tokens. The diagnostic source run was already a deterministic moved-file task failure; the outage did not create that task failure.

## Bounded change and every changed/added repository file

| File | Change |
| --- | --- |
| `backend/tests/live/test_mission05_live.py` | One-line guard: explicitly enabled live verification with incomplete role configuration fails, rather than silently skipping with a successful pytest exit. Disabled live tests still skip. |
| `backend/tests/mission05/test_gemini_live_wiring.py` | Mocked coverage for missing provider/model/key in both roles; count-time and generation-time 503s in diagnosis-only and combined wiring; exact request counts/no retries; independent model paths; client cleanup; saved failure evidence; redaction; preservation of the successful agent result. |
| `backend/tests/mission05/test_gemini_integration.py` | Two mocked HTTP cases prove diagnostic generation 503 preserves clean/moved task status, score, and validator results, and invalidates an otherwise comparable regression pair. |
| `docs/mission05-live-investigation.md` | This investigation, verification record, and exact local continuation instructions. |

No files were deleted. All application source under `backend/app/`, Anthropic support, provider-neutral contracts, existing frontend, local launcher, original handoff, and supplied live evidence remain byte-identical to the ZIP. The runner still does not compute, repair, or substitute the model's report. The deterministic task validator and `DiagnosisValidator` retain authority. Existing hidden-truth projection, role isolation, and invalid-regression coverage pass.

## Verification performed here

Python 3.12.10 in an isolated environment created from the repository's unchanged requirements.

| Check | Result |
| --- | --- |
| Unmodified canonical offline baseline | 158 passed, 3 deselected, 1 warning |
| Focused new coverage before the guard fix | 29 passed, 7 skipped: the skips reproduced the missing-config behavior |
| Focused coverage after the guard fix | 36 passed; 0 skipped/failures/errors |
| Final complete offline suite | **169 passed, 3 deselected, 1 warning**; 0 failures/errors/skips |
| Final full suite, live disabled | **169 passed, 3 skipped, 1 warning**; 0 failures/errors |
| Existing PowerShell launcher syntax | 0 errors |

All three final full-suite skips are explicitly disabled live-provider tests. The single warning is the existing Starlette/AnyIO `BlockingPortal` deprecation. The initial baseline command had 151 passes and 7 setup errors because its temporary-directory parent was missing; creating that parent resolved all errors without changing source or excluding tests.

Final commands, with `PYTHONPATH=backend` and both live flags disabled:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -m 'not live' -q --tb=short --basetemp=.pytest_cache/final-offline-temp -o cache_dir=.pytest_cache/offline
.\.venv\Scripts\python.exe -m pytest backend/tests -q -rs --tb=short --basetemp=.pytest_cache/final-full-temp -o cache_dir=.pytest_cache/full
```

All provider responses generated in these tests were mocked. No fresh Gemini lookup, token-count, generation, diagnosis, combined execution, or Anthropic call was made here. Credential-presence checks found no `AEGIS_AUT_API_KEY`, `AEGIS_DIAG_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY` in the process. Credentials were not searched for in other projects or secret files.

## Exact local continuation

Use the included canonical `aegis` directory, or apply the accompanying patch to the exact original ZIP's `aegis` directory. Do not apply it to an unrelated project. The ZIP already includes the patch changes; do not apply the patch again to that ZIP.

For the patch route, place the patch beside the original `aegis` folder and run these commands from inside that folder. Per-command Git settings preserve the ZIP's original mixed line endings without changing your Git configuration:

```powershell
git -c core.autocrlf=false -c core.whitespace=cr-at-eol apply --check ..\AEGIS_M05_Live_Verification.patch
if ($LASTEXITCODE -ne 0) { throw 'Patch does not match this repository; stop.' }
git -c core.autocrlf=false -c core.whitespace=cr-at-eol apply ..\AEGIS_M05_Live_Verification.patch
if ($LASTEXITCODE -ne 0) { throw 'Patch application failed; stop.' }
```

From the canonical repository root, create its environment only if it is absent:

```powershell
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed.' }
    & .\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& .\scripts\mission05_gemini_live.ps1 -AgentModel 'gemini-3.7-flash' -DiagnosticModel 'gemini-3.7-flash'
```

The existing launcher is included unchanged and syntax-checked. It reruns the offline suite, obtains both keys through separate hidden local prompts, and uses process-only role-specific configuration. Do not enter keys in chat, command literals, files, or a transcript. The launcher clears both role keys in `finally`.

It checks each exact model using that role's key and runs:

1. Real model Agent Under Test, then deterministic task validation.
2. Real model diagnosis of the controlled moved-file failure, then schema decoding and `DiagnosisValidator`.
3. Only after both checks pass: combined real agent, deterministic validation, real diagnosis, and `DiagnosisValidator`, preserving independent execution metadata.

Each stage runs once. Any failed stage stops the launcher; inspect its new `artifacts/m05-live/<session>/` evidence. A later rerun requires a deliberate new operator decision and retains the earlier failures. Never add an automatic loop, swap models silently, weaken schemas/validators, or count a skipped check as a pass.

For 503, retain `provider_harness` and stop. For model lookup/authorization errors, resolve access to the explicitly selected model before a new attempt. For a future explicit 400 or schema error, preserve the request/response evidence and make only a demonstrated compatibility fix. A successful generation must still pass both local schema validation and grounded diagnosis validation. The existing valid-empty-hypothesis outcome remains an honest inconclusive diagnosis, not proof of a supported cause.

M05 Live becomes PASS only after all three real checks pass in the intended sequence. No such result is claimed in this report. M06 remains gated. No commit or push was performed.
