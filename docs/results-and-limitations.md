# AEGIS — Results and Limitations

## Final deterministic result

| Metric | Result |
| --- | ---: |
| Valid pairs | 2/2 |
| Improvements | 1 |
| Regressions | 0 |
| Unresolved | 0 |
| Baseline success | 50% |
| Candidate success | 100% |
| Stress transition | FAIL -> PASS |
| Clean transition | PASS -> PASS |
| Repair gate | ACCEPT |

Bounded policy change:

```text
resource_reobservation.enabled: false -> true
```

This supports the claim that the bounded policy improved the pinned moved-resource case under matched deterministic conditions while preserving the clean case.

It does **not** establish universal agent reliability or a unique internal causal explanation.

## Verification

```text
Backend:  169 passed, 3 skipped, 1 warning
Frontend: production build PASS
Hosted deployment: PASS
Hosted deterministic flow: 8/8 stages PASS
```

The three skips are the explicitly disabled live-provider tests. The warning is the existing Starlette/AnyIO deprecation warning.

## Evidence rule

```text
Executed Fact != Model Inference != Experimental Causal Evidence
```

Deterministic validators establish outcomes. Model output cannot overwrite executed evidence.

## Known limitations

- **In-memory state:** history resets on restart/redeploy.
- **Single process:** current deployment is intentionally one worker / one replica.
- **Bounded benchmark:** current proof centers on a small sales-report task and limited perturbation set.
- **Bounded repair:** no unrestricted source-code rewriting.
- **Live provider uncertainty:** Gemini Agent live path succeeded, but live diagnosis and combined flow remain BLOCKED / UNVERIFIED after provider 503 responses.
- **No fabricated regression:** no genuine deterministic `PASS -> FAIL` regression has been observed in the current perturbation set.

## AI-assisted development disclosure

AEGIS was developed with AI-assisted engineering support for architecture review, code generation, debugging, test design, documentation, and deployment review.

Working claims are based on executed tests, Git-reviewed changes, runtime behavior, and deployed smoke testing — not on AI output alone.
