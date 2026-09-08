from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    OBSERVATION = "observation"
    DECISION = "decision"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PERTURBATION = "perturbation"
    RECOVERY = "recovery"
    VALIDATION = "validation"
    RUN_FINISHED = "run_finished"
    MODEL = "model"


class PerturbationKind(str, Enum):
    NONE = "none"
    FILE_MOVED = "file_moved"
    FIELD_RENAMED = "field_renamed"
    TOOL_TIMEOUT = "tool_timeout"
    PARTIAL_RESPONSE = "partial_response"


class TraceEvent(StrictModel):
    schema_version: str = "1.0"
    run_id: str
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    sequence: int = Field(ge=0)
    kind: EventType
    actor: str = "aegis"
    elapsed_ms: int = Field(default=0, ge=0)
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    parent_event_ids: list[str] = Field(default_factory=list)
    tool_call_id: str | None = None

    @property
    def id(self) -> str:
        return self.event_id

    @property
    def step(self) -> int:
        return self.sequence

    @property
    def type(self) -> EventType:
        return self.kind


class ResourceContract(StrictModel):
    resource_id: str = "sales_input"
    original_path: str = "sales.csv"
    allowed_scope_prefix: str = ""


class TaskSpec(StrictModel):
    task_id: str = "sales_report_v1"
    version: str = "1.0"
    title: str = "Generate a sales summary"
    instruction: str = (
        "Read sales.csv, compute total revenue and order count, and write report.json."
    )
    resource: ResourceContract = Field(default_factory=ResourceContract)


class ResourceReobservationPolicy(StrictModel):
    enabled: bool = False
    trigger_operation: Literal["read_file"] = "read_file"
    trigger_error: Literal["FILE_NOT_FOUND"] = "FILE_NOT_FOUND"
    discovery_tool: Literal["list_files"] = "list_files"
    scope_source: Literal["task_allowed_resource_scope"] = "task_allowed_resource_scope"
    identity_source: Literal["task_resource_identity"] = "task_resource_identity"
    selection_rule: Literal["exact_unique_match"] = "exact_unique_match"
    max_discovery_calls_per_run: Literal[1] = 1
    max_recovery_read_calls_per_run: Literal[1] = 1
    require_complete_listing: Literal[True] = True
    on_no_match: Literal["fail"] = "fail"
    on_ambiguity: Literal["fail"] = "fail"


class AgentPolicy(StrictModel):
    schema_version: Literal["1"] = "1"
    policy_id: str = Field(default_factory=lambda: str(uuid4()))
    version: int = Field(default=1, ge=1)
    parent_policy_id: str | None = None
    resource_reobservation: ResourceReobservationPolicy = Field(default_factory=ResourceReobservationPolicy)
    policy_hash: str = ""


class ScenarioManifest(StrictModel):
    schema_version: str = "1.0"
    task_id: str
    task_version: str
    task_contract_hash: str = ""
    perturbation: PerturbationKind
    perturbation_version: str = "1.0"
    perturbation_parameters: dict[str, Any] = Field(default_factory=dict)
    fixture_hash: str
    fixture_snapshot: dict[str, str] = Field(default_factory=dict)
    environment_seed: int | None = None
    policy_version: str = "scripted-policy-v1"
    policy_hash: str
    agent_version: str
    validator_version: str = "sales-validator-v1"
    validator_hash: str = "sales-validator-v1"
    environment_version: str = "virtual-files-v2"
    max_steps: int = 50


class RunRequest(StrictModel):
    perturbation: PerturbationKind = PerturbationKind.NONE


class ValidationResult(StrictModel):
    validation_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    name: str
    passed: bool
    expected: Any = None
    actual: Any = None
    details: str = ""
    evidence_event_ids: list[str] = Field(default_factory=list)


class RunRecord(StrictModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    task: TaskSpec = Field(default_factory=TaskSpec)
    perturbation: PerturbationKind = PerturbationKind.NONE
    scenario: ScenarioManifest | None = None
    status: RunStatus = RunStatus.CREATED
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    events: list[TraceEvent] = Field(default_factory=list)
    validations: list[ValidationResult] = Field(default_factory=list)
    score: float = 0.0
    trace_complete: bool = False
    trace_hash: str | None = None
    effective_policy_id: str | None = None
    effective_policy_hash: str | None = None
    replay_of_run_id: str | None = None
    evaluation_id: str | None = None
    evaluation_case: str | None = None
    evaluation_arm: Literal["baseline", "candidate"] | None = None
    regression_case_id: str | None = None
    regression_case_version: int | None = None
    model_execution: dict[str, Any] | None = None


class FailureCategory(str, Enum):
    STATE_ASSUMPTION = "STATE_ASSUMPTION"
    DATA_CONTRACT = "DATA_CONTRACT"
    TOOL_USE = "TOOL_USE"
    RECOVERY = "RECOVERY"
    TASK_LOGIC = "TASK_LOGIC"
    EVIDENCE_GROUNDING = "EVIDENCE_GROUNDING"
    UNKNOWN = "UNKNOWN"


class FailureKind(str, Enum):
    INCORRECT_OUTPUT = "incorrect_output"
    EXECUTION_FAILURE = "execution_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"
    HARNESS_ERROR = "harness_error"


class VerifiedFact(StrictModel):
    fact_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    predicate: str
    operands: dict[str, Any] = Field(default_factory=dict)
    value: Any
    source_event_ids: list[str] = Field(default_factory=list)
    validation_ids: list[str] = Field(default_factory=list)
    extractor_version: str = "fact-extractor-v1"


class FailureObservation(StrictModel):
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    kind: FailureKind
    detected_at_event_id: str
    validation_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    summary: str


class EvidenceRelation(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class EvidenceReference(StrictModel):
    run_id: str
    event_id: str
    fact_id: str
    relation: EvidenceRelation
    json_pointer: str | None = None
    claimed_value: Any = None


class DiagnosticProbe(StrictModel):
    probe_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal["reobserve_state", "retry_once", "validate_schema"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_discriminating_result: str
    max_runs: int = Field(default=1, ge=1, le=3)
    max_steps: int = Field(default=10, ge=1, le=50)


class RootCauseHypothesis(StrictModel):
    hypothesis_id: str = Field(default_factory=lambda: str(uuid4()))
    category: FailureCategory
    candidate_event_ids: list[str] = Field(min_length=1)
    mechanism: str
    supporting_evidence: list[EvidenceReference] = Field(min_length=1)
    contradicting_evidence: list[EvidenceReference] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_basis: str
    limitations: list[str] = Field(default_factory=list)


class DiagnosisStatus(str, Enum):
    SUPPORTED = "supported"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"


class DiagnosisCandidate(StrictModel):
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list, max_length=3)
    selected_hypothesis_id: str | None = None
    earliest_candidate_event_id: str | None = None
    downstream_event_ids: list[str] = Field(default_factory=list)
    probes: list[DiagnosticProbe] = Field(default_factory=list)


class Diagnosis(StrictModel):
    diagnosis_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    trace_hash: str
    observation_ids: list[str]
    hypotheses: list[RootCauseHypothesis]
    selected_hypothesis_id: str | None = None
    earliest_candidate_event_id: str | None = None
    downstream_event_ids: list[str] = Field(default_factory=list)
    probes: list[DiagnosticProbe] = Field(default_factory=list)
    status: DiagnosisStatus
    provider: str
    validation_errors: list[str] = Field(default_factory=list)


class RepairApplicability(StrictModel):
    task_id: str
    task_version: str
    agent_version: str
    resource_id: str
    trigger_error: Literal["FILE_NOT_FOUND"] = "FILE_NOT_FOUND"
    trigger_operation: Literal["read_file"] = "read_file"


class RepairProposal(StrictModel):
    schema_version: Literal["1"] = "1"
    repair_id: str = Field(default_factory=lambda: str(uuid4()))
    diagnosis_id: str
    source_run_id: str
    base_policy_id: str
    base_policy_hash: str
    component: Literal["resource_reobservation"] = "resource_reobservation"
    old_configuration: ResourceReobservationPolicy
    new_configuration: ResourceReobservationPolicy
    supporting_evidence: list[EvidenceReference] = Field(min_length=1)
    applicability: RepairApplicability
    expected_behavior: Literal["recover_missing_resource_by_unique_identity"] = "recover_missing_resource_by_unique_identity"
    evaluation_plan_id: str
    status: Literal["valid", "rejected"] = "valid"
    validation_errors: list[str] = Field(default_factory=list)


class InvariantCheck(StrictModel):
    name: str
    passed: bool
    details: str = ""


class ActivationSummary(StrictModel):
    activated: bool
    count: int = 0
    exposure_verified: bool = False


class RecoverySummary(StrictModel):
    attempted: bool = False
    discovery_calls: int = 0
    recovery_read_calls: int = 0
    terminal_status: str | None = None


class RunComparison(StrictModel):
    schema_version: Literal["1"] = "1"
    comparison_id: str = Field(default_factory=lambda: str(uuid4()))
    baseline_run_id: str
    candidate_run_id: str
    invariant_checks: list[InvariantCheck]
    comparable: bool
    noncomparability_reasons: list[str] = Field(default_factory=list)
    baseline_outcome: RunStatus
    candidate_outcome: RunStatus
    baseline_activation: ActivationSummary
    candidate_activation: ActivationSummary
    policy_diff: dict[str, Any]
    validation_differences: list[dict[str, Any]] = Field(default_factory=list)
    recovery_summary: RecoverySummary
    baseline_tool_call_count: int
    candidate_tool_call_count: int
    tool_call_count_delta: int
    original_failure_resolved: bool | None = None


class GateDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvaluationRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    evaluation_id: str = Field(default_factory=lambda: str(uuid4()))
    repair_id: str
    baseline_policy_id: str
    candidate_policy_id: str
    moved_baseline_run_id: str
    moved_candidate_run_id: str
    clean_baseline_run_id: str
    clean_candidate_run_id: str
    moved_comparison: RunComparison
    clean_comparison: RunComparison
    decision: GateDecision
    reasons: list[str] = Field(default_factory=list)
    causal_statement: str
    suite_snapshot: "EvaluationSuiteSnapshot | None" = None
    case_results: list["CasePairResult"] = Field(default_factory=list)
    metrics: "ReliabilityMetrics | None" = None
    evaluation_complete: bool = True


class CaseOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INVALID = "INVALID"
    INCOMPLETE = "INCOMPLETE"


class PairTransition(str, Enum):
    PRESERVED = "preserved"      # PASS -> PASS
    IMPROVED = "improved"        # FAIL -> PASS
    REGRESSION = "regression"    # PASS -> FAIL
    UNRESOLVED = "unresolved"    # FAIL -> FAIL
    INVALID = "invalid"


class ExpectedCheck(StrictModel):
    check_id: str
    required: bool = True


class ExposureRequirement(StrictModel):
    mode: Literal["none", "must_activate"] = "none"
    perturbation_spec_hash: str | None = None


class RegressionCase(StrictModel):
    schema_version: Literal["1"] = "1"
    case_id: str = Field(default_factory=lambda: str(uuid4()))
    case_version: int = Field(default=1, ge=1)
    supersedes_version: int | None = None
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    source_run_id: str
    scenario_manifest_id: str
    scenario_manifest_hash: str
    initial_fixture_ref: str
    initial_fixture_hash: str
    task_contract_hash: str
    validator_bundle_hash: str
    execution_contract_hash: str
    expected_checks: list[ExpectedCheck] = Field(min_length=1)
    exposure_requirement: ExposureRequirement = Field(default_factory=ExposureRequirement)
    created_at: datetime = Field(default_factory=utcnow)
    case_content_hash: str = ""
    active: bool = True


class SuiteMember(StrictModel):
    case_id: str
    case_version: int = Field(ge=1)
    case_content_hash: str
    role: Literal["current", "clean", "historical"]
    must_pass_candidate: bool = False


class EvaluationSuiteSnapshot(StrictModel):
    schema_version: Literal["1"] = "1"
    suite_id: str = Field(default_factory=lambda: str(uuid4()))
    suite_hash: str = ""
    members: list[SuiteMember] = Field(min_length=1)
    base_policy_id: str
    base_policy_hash: str
    candidate_policy_id: str
    candidate_policy_hash: str
    gate_version: Literal["deterministic-regression-v1"] = "deterministic-regression-v1"
    repetitions_per_arm: Literal[1] = 1


class CasePairResult(StrictModel):
    case_id: str
    case_version: int
    role: Literal["current", "clean", "historical"]
    baseline_run_id: str
    candidate_run_id: str
    baseline_outcome: CaseOutcome
    candidate_outcome: CaseOutcome
    transition: PairTransition
    comparison: RunComparison
    valid_pair: bool
    reasons: list[str] = Field(default_factory=list)


class ReliabilityMetrics(StrictModel):
    scheduled_cases: int
    baseline_valid: int
    candidate_valid: int
    valid_pairs: int
    baseline_passes: int
    candidate_passes: int
    improvement_count: int
    regression_count: int
    unresolved_failure_count: int
    preserved_count: int
    baseline_coverage: float | None = None
    candidate_coverage: float | None = None
    baseline_case_success_rate: float | None = None
    candidate_case_success_rate: float | None = None
    paired_net_change: float | None = None
    invalid_or_incomplete_pairs: int = 0


class Mission04EvaluationRequest(StrictModel):
    current_case_id: str
    clean_case_id: str
    historical_case_ids: list[str] = Field(default_factory=list)


class RegressionCaseCreate(StrictModel):
    source_run_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
