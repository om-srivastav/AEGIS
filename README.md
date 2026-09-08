# AEGIS

**Agent Evaluation, Diagnosis, Improvement & Guardrail System**

AEGIS is an AI-agent reliability laboratory. It runs an Agent Under Test in a controlled task environment, injects safe perturbations, records the complete execution trace, diagnoses failures, proposes constrained recovery strategies, replays the failure, and regression-tests whether reliability actually improved.

## Current vertical slice

- FastAPI backend
- deterministic virtual file-task environment
- scripted Agent Under Test
- event/trace recorder
- baseline execution
- four perturbation types
- deterministic outcome validators
- pass/fail reliability score

This first slice intentionally uses a simple agent so the AEGIS infrastructure can be objectively validated before a model-backed agent is added.

## Local backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `/docs` for the FastAPI interactive API.

## Grounded diagnosis foundation (Mission 02)

AEGIS now records versioned run/scenario metadata, extracts deterministic `VerifiedFact` records, creates structured failure observations, and passes only a bounded evidence bundle to a model-independent `ReasoningProvider` interface. The current provider is a deterministic `MockProvider` used to verify grounding before connecting a paid reasoning model.

A model-produced diagnosis is not trusted automatically. It is validated against the actual run for evidence existence, run scope, source-event support, claimed fact values, chronology, validation provenance, and trace completeness. Unsupported output is rejected.

AEGIS intentionally distinguishes:

**Executed Fact != Model Inference != Experimental Causal Evidence**

The phrase **earliest supported causal candidate** is used until a later controlled intervention/replay provides stronger causal support.

## Constrained repair + fresh replay (Mission 03)

Mission 03 closes AEGIS's first intervention loop without arbitrary code rewriting. A supported moved-resource diagnosis can produce exactly one constrained repair: enable bounded resource re-observation after `FILE_NOT_FOUND` for the declared task resource.

The recovery controller is not allowed to guess filenames or infer semantic equivalence from CSV contents. It calls `list_files` once, requires a complete listing, and selects only an exact unique match on the task's opaque logical `resource_id`. Zero or multiple matches fail safely. A successful candidate performs at most one recovery read.

Evaluation always executes four fresh runs from the same pre-perturbation fixture:

1. moved-resource + baseline policy
2. moved-resource + candidate policy
3. clean + baseline policy
4. clean + candidate policy

Only the `resource_reobservation.enabled` policy field differs between baseline and candidate. Task, fixture, perturbation, validator, agent version, environment version, and execution limits are checked as comparison invariants.

A repair is accepted only when the moved baseline reproduces failure, the moved candidate is exposed to the same perturbation and passes, and both clean arms pass without unnecessary recovery. The strongest claim produced by this phase is an **intervention-supported policy effect**, not a definitive unique root cause or proof of general reliability.

### Verification

Run:

```bash
PYTHONPATH=backend pytest -q backend/tests
```

At the Mission 03 checkpoint the full suite reports **33 passed**.

## Mission 04 checkpoint

AEGIS now includes a deterministic regression vault and suite evaluator. Historical cases are immutable/versioned, suite membership is frozen before execution, every selected case is replayed fresh under baseline and candidate policies, and transitions are classified as preserved, improved, regression, or unresolved.

Current verified test status: **43 passing tests**. The current bounded recovery policy improves the moved-resource case, preserves the clean case, and leaves the field-renamed historical failure unresolved. No real pass-to-fail regression exists in the current deterministic perturbation set, so AEGIS does not fabricate a rejection demonstration.

## Mission 05 checkpoint — model-backed execution boundary

AEGIS now supports two independently configurable model roles while keeping deterministic behavior as the default:

- a bounded **model Agent Under Test** behind a provider-neutral `ModelTransport`, with only `list_files`, `read_file`, and `write_report`;
- a **model-backed diagnostic ReasoningProvider** that receives only an allowlisted grounded evidence projection and still must pass the existing Mission 02 `DiagnosisValidator`.

The runner does not secretly solve the task for the model. A successful `write_report` means execution completed; the unchanged deterministic sales validator separately decides whether the report is correct. Provider/harness failures are not admitted as genuine task regressions.

Mission 05 deterministic verification:

```bash
PYTHONPATH=backend pytest -q backend/tests -m "not live"
```

Verified result: **68 passed, 2 deselected**. Running the complete test tree with live provider tests disabled yields **68 passed, 2 skipped**.

No live external model/provider execution was performed for this checkpoint. See `docs/mission05-model-backed-agents.md` and `docs/mission05_execution_report.md` for the exact boundaries and verification status.
