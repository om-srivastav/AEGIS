# AEGIS Architecture — Locked Core

## Core loop

Agent Under Test -> Controlled Environment -> Trace Recorder -> Deterministic Validators -> Failure Diagnosis -> Repair Proposal -> Replay -> Regression Gate -> Reliability Report

## Design rule

AEGIS is model-agnostic. Astra may be used heavily during development and can be plugged in as a diagnostic/provider or as an Agent Under Test, but no core subsystem depends on Astra specifically.

## Current first task

`SalesReportTask`: read a small sales CSV and create an exact JSON report.

Why start here: the task has deterministic ground truth, so AEGIS can prove whether its tracing and perturbation machinery works before we add an LLM. The demo will later graduate to a model-backed workflow agent while keeping the same adapter interfaces.

## Event model

Each run records ordered events: run_started, observation, decision, tool_call, tool_result, perturbation, validation, run_finished.

The trace is the evidence substrate used later by the diagnostic engine.

## Mission 02: grounded diagnosis contracts

AEGIS separates three evidence levels:

1. **Executed Fact** — created by deterministic code from trace events, environment state, or validators.
2. **Model Inference** — a bounded hypothesis produced by a `ReasoningProvider` and linked to verified facts.
3. **Experimental Causal Evidence** — only available after a controlled fresh replay/intervention changes measured behavior.

Therefore an initial diagnosis uses the term **earliest supported causal candidate**, not proven root cause. A perturbation trigger (for example, a moved file) is also kept separate from the agent failure category (for example, recovery failure).

The current Mission 02 pipeline is:

```text
RunRecord -> FactExtractor -> VerifiedFact + FailureObservation
          -> ReasoningProvider (MockProvider for now)
          -> DiagnosisCandidate
          -> DiagnosisValidator
          -> supported / inconclusive / rejected Diagnosis
```

`DiagnosisValidator` checks run scope, event/fact existence, fact-source alignment, claimed values, chronology, validation references, allowed evidence bundles, confidence bounds (schema), and trace completeness for absence claims.

No repair or replay logic is included in this phase.

## Mission 03: intervention-supported policy effect

Mission 03 adds one versioned `AgentPolicy` component: `resource_reobservation`. The baseline and candidate configurations are behaviorally identical except for `enabled: false -> true`.

The task contract owns the logical resource identity (`sales_input`). When the original path is moved, the environment preserves that identity on the moved file. Recovery may discover only within the task's allowed scope and may select only one exact unique identity match. Filename similarity, CSV schema similarity, and content inspection are not recovery criteria.

The moved-file perturbation activates semantically on the first read of the declared original path. The trace therefore contains a trusted activation event before the corresponding `FILE_NOT_FOUND` result. Candidate recovery must occur after this verified exposure.

Fresh evaluation executes paired baseline/candidate runs for both moved and clean cases. The gate has three outcomes:

- `ACCEPT`: moved baseline fails, moved candidate passes after verified exposure and bounded recovery, both clean arms pass, and clean candidate does not recover.
- `REJECT`: a valid comparable experiment shows candidate failure, clean regression, or policy-bound violation.
- `INCONCLUSIVE`: the experiment is not trustworthy enough to compare, such as a manifest mismatch or missing perturbation activation.

The scoped causal statement is:

> Enabling the bounded resource re-observation policy changed the moved-resource scenario from failure to success under matched deterministic conditions; the clean case remained correct.

This is **intervention-supported policy evidence**. It does not establish a unique internal reasoning failure and does not imply broad reliability beyond the pinned evaluation cases.
