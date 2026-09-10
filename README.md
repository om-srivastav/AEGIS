# AEGIS

**Agent Evaluation, Diagnosis, Improvement & Guardrail System**

> **Don't just build agents. Find out why they fail — and make improvement measurable.**

AEGIS is an engineering laboratory for evaluating and improving the reliability of autonomous AI agents. It executes an Agent Under Test in a controlled environment, injects bounded perturbations, records an evidence trace, diagnoses supported failure causes, proposes constrained repairs, replays the same failure, regression-tests the candidate, and measures whether reliability actually improved.

## Core reliability loop

```text
TEST -> STRESS -> FAIL -> DIAGNOSE -> REPAIR -> REPLAY -> REGRESSION TEST -> MEASURE
```

AEGIS is intentionally built around one rule:

**Models may reason; deterministic systems establish facts.**

Model output never gets to fabricate tool results, environment state, validator outcomes, benchmark results, or improvement claims.

## What is implemented

- **FastAPI backend** with run, diagnosis, repair, replay, regression-vault, and suite-evaluation APIs.
- **React + TypeScript + Vite dashboard** for the complete reliability loop.
- **Deterministic virtual file environment** and scripted Sales Report Agent.
- Controlled perturbations: baseline, moved resource, schema drift, tool timeout, and partial response.
- **Ordered trace/evidence capture** for each execution.
- **Deterministic task validators** that remain authoritative even when a model is used.
- **Evidence-grounded diagnosis** with exact run/trace/validation references.
- **Constrained repair** through bounded policy changes rather than arbitrary source rewriting.
- **Fresh replay** from reconstructed environment state.
- **Immutable/versioned regression cases** and deterministic transition classification.
- **Reliability metrics** comparing baseline and candidate behavior.
- Provider-neutral **ModelTransport**, model AgentAdapter, Anthropic support, and Gemini transport.
- Independent model configuration for the Agent Under Test and diagnostic reasoning role.
- Provider/harness failures remain distinct from genuine task regressions.

## Demo scenario

The default task asks an agent to:

1. read `sales.csv`,
2. compute order count and total revenue,
3. write `report.json`.

A useful demo is the **Moved Resource** perturbation:

```text
Baseline policy:  FAIL
Bounded repair:   enable resource re-observation
Candidate replay: PASS
Clean case:       PASS -> PASS
Stress case:      FAIL -> PASS
Gate:             ACCEPT
```

In the verified M06 dashboard flow, the regression suite produced:

```text
Valid pairs:        2 / 2
Improvements:       1
Regressions:        0
Unresolved:         0
Baseline success:   50%
Candidate success: 100%
```

The dashboard exposes the evidence behind those results rather than presenting the score as a model opinion.

## Architecture and trust boundaries

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
        +--------------------+
        |                    |
        v                    v
Deterministic Validator   Evidence Projection
        |                    |
        |                    v
        |             ReasoningProvider
        |                    |
        |                    v
        |            DiagnosisValidator
        |                    |
        +---------+----------+
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

The model-backed path is deliberately provider-neutral. AEGIS can use deterministic/mock providers for reproducible testing or independently configured production model providers without changing the validator authority or regression semantics.

## Grounded diagnosis

AEGIS records versioned run/scenario metadata, extracts deterministic `VerifiedFact` records, creates structured failure observations, and sends only a bounded evidence projection to a `ReasoningProvider`.

A proposed diagnosis is not trusted automatically. `DiagnosisValidator` checks evidence existence, run scope, source-event support, claimed values, chronology, validation provenance, and trace completeness. Unsupported diagnoses are rejected.

AEGIS distinguishes:

**Executed Fact != Model Inference != Experimental Causal Evidence**

Before intervention, AEGIS reports the **earliest supported causal candidate**. A successful controlled replay can strengthen that into an **intervention-supported policy effect**, but not proof of a unique universal cause.

## Constrained repair and fresh replay

The current bounded repair for a supported moved-resource failure changes exactly one policy field:

```text
resource_reobservation.enabled: false -> true
```

The recovery controller may list visible files once and retry one read only when an exact logical resource identity is found. It does not guess filenames from contents, silently rewrite the task, or let the runner solve the task for the agent.

Repair evaluation reconstructs fresh environments and compares baseline and candidate runs under invariant task, fixture, perturbation, validator, agent/environment versions, and execution limits.

## Regression vault

Discovered failures can be registered as immutable/versioned regression cases. Suite evaluation classifies paired outcomes as:

- `preserved` — PASS -> PASS
- `improved` — FAIL -> PASS
- `regression` — PASS -> FAIL
- `unresolved` — FAIL -> FAIL
- `invalid` — incomparable or provider/harness-invalid pair

Provider outages cannot be counted as task regressions.

## Model-backed execution

AEGIS supports two independent model roles:

- **Agent Under Test** — bounded to `list_files`, `read_file`, and `write_report`.
- **Diagnostic ReasoningProvider** — receives only the allowlisted grounded evidence projection and must still pass `DiagnosisValidator`.

Provider/model selection and credentials are role-specific. The deterministic path remains the default.

### M05 live-verification status

M05 offline/model-backed integration is verified. A real Gemini Agent Under Test live run completed successfully in the local verification environment. The diagnosis path subsequently reached the provider but received explicit HTTP `503 UNAVAILABLE` / high-demand responses during generation, so the diagnosis and combined live checks remain **BLOCKED / UNVERIFIED**, not passed.

AEGIS records this as a `provider_harness` failure and does not reinterpret it as a task regression. No automatic retry-until-green or silent provider fallback is used.

See:

- `docs/mission05-gemini-live.md`
- `docs/mission05-live-investigation.md`
- `docs/mission05-model-backed-agents.md`

## Verified status

Current local verification on Python 3.12:

```text
Backend offline suite:          169 passed, 3 deselected
Backend full suite, live off:   169 passed, 3 skipped
Frontend production build:      PASS
M06 end-to-end dashboard flow:  PASS (8/8 stages)
```

The three full-suite skips are the explicitly disabled live-provider tests. The remaining warning is an existing Starlette/AnyIO deprecation warning.

## Run locally

### Backend

From the repository root:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
$env:PYTHONPATH='backend'
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
export PYTHONPATH=backend
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Health check:

```text
GET http://127.0.0.1:8000/health
```

FastAPI docs are available at `/docs`.

### Frontend

In another terminal:

```bash
npm --prefix frontend install
npm --prefix frontend run dev -- --host 127.0.0.1
```

Open:

```text
http://127.0.0.1:5173/
```

Production build:

```bash
npm --prefix frontend run build
```

## Tests

Offline suite:

```powershell
$env:PYTHONPATH='backend'
python -m pytest backend\tests -m 'not live' -q
```

Full suite with live tests disabled:

```powershell
$env:PYTHONPATH='backend'
python -m pytest backend\tests -q -rs
```

Live-provider checks are opt-in and require explicit role configuration. Do not put provider keys in commands, source files, transcripts, or frontend requests.

## Mission checkpoints

| Mission | Result |
| --- | --- |
| M01 — deterministic foundation | Complete |
| M02 — grounded diagnosis | Complete |
| M03 — constrained repair + fresh replay | Complete |
| M04 — regression vault + gate/metrics | Complete |
| M05 — model-backed execution | Offline complete; live diagnosis/combined blocked by external 503 |
| M06 — reliability dashboard | Functional flow complete and locally verified |
| M07 — deployment + final docs/demo/release | In progress |

## Current limitation

AEGIS currently demonstrates reliability engineering on a deliberately bounded sales-report task and a small perturbation set. A successful repair should be interpreted as measured improvement on the evaluated cases, not a claim of universal agent reliability.

## Project status

M01–M06 are implemented in the canonical repository. M07 focuses on deployment, release documentation, demo assets, final smoke testing, and packaging.
