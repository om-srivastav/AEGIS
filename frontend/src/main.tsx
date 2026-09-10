import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity,
  AlertTriangle,
  Archive,
  ArrowRight,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  FlaskConical,
  Gauge,
  History,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Wrench,
  XCircle,
} from 'lucide-react';
import './styles.css';

type Perturbation = 'none' | 'file_moved' | 'field_renamed' | 'tool_timeout' | 'partial_response';
type RunStatus = 'passed' | 'failed' | 'running' | 'created';
type DiagnosisStatus = 'supported' | 'inconclusive' | 'rejected';
type GateDecision = 'ACCEPT' | 'REJECT' | 'INCONCLUSIVE';

type TraceEvent = {
  event_id: string;
  sequence: number;
  kind: string;
  actor: string;
  elapsed_ms: number;
  message: string;
  payload: Record<string, unknown>;
};

type Validation = {
  validation_id: string;
  name: string;
  passed: boolean;
  expected: unknown;
  actual: unknown;
  details: string;
  evidence_event_ids: string[];
};

type RunRecord = {
  run_id: string;
  perturbation: Perturbation;
  status: RunStatus;
  score: number;
  trace_complete: boolean;
  trace_hash?: string | null;
  started_at: string;
  finished_at?: string | null;
  events: TraceEvent[];
  validations: Validation[];
  replay_of_run_id?: string | null;
  evaluation_id?: string | null;
  evaluation_case?: string | null;
  evaluation_arm?: 'baseline' | 'candidate' | null;
  model_execution?: Record<string, unknown> | null;
};

type EvidenceReference = {
  run_id: string;
  event_id: string;
  fact_id: string;
  relation: string;
  json_pointer?: string | null;
  claimed_value?: unknown;
};

type Hypothesis = {
  hypothesis_id: string;
  category: string;
  candidate_event_ids: string[];
  mechanism: string;
  supporting_evidence: EvidenceReference[];
  contradicting_evidence: EvidenceReference[];
  confidence: number;
  confidence_basis: string;
  limitations: string[];
};

type Diagnosis = {
  diagnosis_id: string;
  run_id: string;
  trace_hash: string;
  hypotheses: Hypothesis[];
  selected_hypothesis_id?: string | null;
  earliest_candidate_event_id?: string | null;
  downstream_event_ids: string[];
  status: DiagnosisStatus;
  provider: string;
  validation_errors: string[];
};

type RepairProposal = {
  repair_id: string;
  diagnosis_id: string;
  source_run_id: string;
  component: string;
  old_configuration: { enabled: boolean; [key: string]: unknown };
  new_configuration: { enabled: boolean; [key: string]: unknown };
  status: 'valid' | 'rejected';
  validation_errors: string[];
  expected_behavior: string;
};

type RunComparison = {
  baseline_run_id: string;
  candidate_run_id: string;
  comparable: boolean;
  noncomparability_reasons: string[];
  baseline_outcome: RunStatus;
  candidate_outcome: RunStatus;
  original_failure_resolved?: boolean | null;
  baseline_tool_call_count: number;
  candidate_tool_call_count: number;
  tool_call_count_delta: number;
  recovery_summary: {
    attempted: boolean;
    discovery_calls: number;
    recovery_read_calls: number;
    terminal_status?: string | null;
  };
};

type ReliabilityMetrics = {
  scheduled_cases: number;
  valid_pairs: number;
  improvement_count: number;
  regression_count: number;
  unresolved_failure_count: number;
  preserved_count: number;
  baseline_case_success_rate?: number | null;
  candidate_case_success_rate?: number | null;
  paired_net_change?: number | null;
  invalid_or_incomplete_pairs: number;
};

type CasePairResult = {
  case_id: string;
  role: 'current' | 'clean' | 'historical';
  baseline_outcome: string;
  candidate_outcome: string;
  transition: 'preserved' | 'improved' | 'regression' | 'unresolved' | 'invalid';
  valid_pair: boolean;
};

type EvaluationRecord = {
  evaluation_id: string;
  repair_id: string;
  decision: GateDecision;
  reasons: string[];
  causal_statement: string;
  moved_comparison: RunComparison;
  clean_comparison: RunComparison;
  case_results: CasePairResult[];
  metrics?: ReliabilityMetrics | null;
  evaluation_complete: boolean;
};

type RegressionCase = {
  case_id: string;
  case_version: number;
  name: string;
  description: string;
  source_run_id: string;
  active: boolean;
  created_at: string;
  exposure_requirement: { mode: 'none' | 'must_activate' };
};

type Health = { status: string; project: string; version: string };

type BusyAction = 'run' | 'diagnose' | 'repair' | 'evaluate' | 'suite' | 'vault' | null;

const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

const perturbations: { value: Perturbation; label: string; hint: string }[] = [
  { value: 'none', label: 'Baseline', hint: 'Perfect environment' },
  { value: 'file_moved', label: 'Moved resource', hint: 'Expected file changes location' },
  { value: 'field_renamed', label: 'Schema drift', hint: 'Input field is renamed' },
  { value: 'tool_timeout', label: 'Tool timeout', hint: 'First read fails temporarily' },
  { value: 'partial_response', label: 'Partial response', hint: 'Tool returns incomplete data' },
];

const loop = ['TEST', 'STRESS', 'FAIL', 'DIAGNOSE', 'REPAIR', 'REPLAY', 'REGRESSION', 'MEASURE'];

function shortId(value?: string | null) {
  return value ? value.slice(0, 8) : '—';
}

function pct(value?: number | null) {
  return value == null ? '—' : `${Math.round(value * 100)}%`;
}

function human(value: string) {
  return value.split('_').join(' ');
}

function timestamp(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Request failed';
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init);
  if (!response.ok) {
    let detail = `Backend returned ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function App() {
  const [perturbation, setPerturbation] = useState<Perturbation>('none');
  const [run, setRun] = useState<RunRecord | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [repair, setRepair] = useState<RepairProposal | null>(null);
  const [evaluation, setEvaluation] = useState<EvaluationRecord | null>(null);
  const [regressionCases, setRegressionCases] = useState<RegressionCase[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [error, setError] = useState('');

  const selectedHypothesis = useMemo(() => {
    if (!diagnosis?.hypotheses.length) return null;
    return diagnosis.hypotheses.find(item => item.hypothesis_id === diagnosis.selected_hypothesis_id) || diagnosis.hypotheses[0];
  }, [diagnosis]);

  const rootFailure = useMemo(
    () => run?.events.find(event => event.payload?.error_type || event.payload?.error_code),
    [run],
  );

  const providerRows = useMemo(() => {
    if (!run?.model_execution) return [] as { role: string; provider: string; model: string; status: string }[];
    return Object.entries(run.model_execution)
      .filter(([, value]) => value && typeof value === 'object')
      .map(([role, value]) => {
        const meta = value as Record<string, unknown>;
        const models = Array.isArray(meta.resolved_models) ? meta.resolved_models.join(', ') : '';
        return {
          role,
          provider: String(meta.provider || '—'),
          model: String(models || meta.requested_model || '—'),
          status: String(meta.execution_status || meta.validation_status || meta.failure_code || 'recorded'),
        };
      });
  }, [run]);

  const passCount = runs.filter(item => item.status === 'passed').length;
  const failCount = runs.filter(item => item.status === 'failed').length;
  const currentVaultCase = repair
    ? regressionCases.find(item => item.active && item.source_run_id === repair.source_run_id && item.exposure_requirement.mode === 'must_activate')
      ?? regressionCases.find(item => item.active && item.exposure_requirement.mode === 'must_activate')
    : undefined;
  const cleanVaultCase = regressionCases.find(item => item.active && item.exposure_requirement.mode === 'none');
  const suiteReady = Boolean(repair && currentVaultCase && cleanVaultCase);
  const workflowStage = evaluation?.metrics ? 7 : evaluation ? 6 : repair ? 5 : diagnosis ? 4 : run ? (run.status === 'failed' ? 2 : 1) : 0;

  useEffect(() => {
    void refreshOverview();
  }, []);

  async function refreshOverview() {
    try {
      const [history, vault, status] = await Promise.all([
        api<RunRecord[]>('/api/runs'),
        api<RegressionCase[]>('/api/v1/regression-cases'),
        api<Health>('/health'),
      ]);
      setRuns(history);
      setRegressionCases(vault);
      setHealth(status);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function execute() {
    setBusy('run');
    setError('');
    setDiagnosis(null);
    setRepair(null);
    setEvaluation(null);
    try {
      const created = await api<RunRecord>('/api/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ perturbation }),
      });
      setRun(created);
      await refreshOverview();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function diagnose() {
    if (!run) return;
    setBusy('diagnose');
    setError('');
    setRepair(null);
    setEvaluation(null);
    try {
      setDiagnosis(await api<Diagnosis>(`/api/v1/runs/${run.run_id}/diagnoses`, { method: 'POST' }));
      await refreshOverview();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function proposeRepair() {
    if (!diagnosis) return;
    setBusy('repair');
    setError('');
    setEvaluation(null);
    try {
      setRepair(await api<RepairProposal>(`/api/v1/diagnoses/${diagnosis.diagnosis_id}/repairs`, { method: 'POST' }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function evaluateRepair() {
    if (!repair) return;
    setBusy('evaluate');
    setError('');
    try {
      const result = await api<EvaluationRecord>(`/api/v1/repairs/${repair.repair_id}/evaluations`, {
        method: 'POST',
        headers: { 'Idempotency-Key': `dashboard-${repair.repair_id}` },
      });
      setEvaluation(result);
      await refreshOverview();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function evaluateRegressionSuite() {
    if (!repair || !currentVaultCase || !cleanVaultCase) return;
    setBusy('suite');
    setError('');
    try {
      const historicalCaseIds = regressionCases
        .filter(item => item.active && item.case_id !== currentVaultCase.case_id && item.case_id !== cleanVaultCase.case_id)
        .map(item => item.case_id);
      const result = await api<EvaluationRecord>(`/api/v1/repairs/${repair.repair_id}/suite-evaluations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': `dashboard-suite-${repair.repair_id}-${currentVaultCase.case_id}-${cleanVaultCase.case_id}`,
        },
        body: JSON.stringify({
          current_case_id: currentVaultCase.case_id,
          clean_case_id: cleanVaultCase.case_id,
          historical_case_ids: historicalCaseIds,
        }),
      });
      setEvaluation(result);
      await refreshOverview();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function registerRegressionCase() {
    if (!run) return;
    setBusy('vault');
    setError('');
    try {
      await api<RegressionCase>('/api/v1/regression-cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_run_id: run.run_id,
          name: `${perturbations.find(item => item.value === run.perturbation)?.label || human(run.perturbation)} regression case`,
          description: `Captured from AEGIS dashboard run ${shortId(run.run_id)}.`,
        }),
      });
      await refreshOverview();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  function selectRun(item: RunRecord) {
    setRun(item);
    setPerturbation(item.perturbation);
    setDiagnosis(null);
    setRepair(null);
    setEvaluation(null);
    setError('');
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand"><span className="brandMark"><ShieldCheck size={20} /></span><span>AEGIS</span></div>
        <div className="topMeta">
          <span className={`healthDot ${health?.status === 'ok' ? 'online' : ''}`} />
          <span>{health ? `API v${health.version}` : 'API status unknown'}</span>
          <span className="divider" />
          <span>Agent Reliability Laboratory</span>
        </div>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">EVIDENCE-GROUNDED AGENT RELIABILITY</p>
          <h1>Find the failure. Test the fix. Measure the difference.</h1>
          <p className="lede">AEGIS stress-tests an AI agent, records the exact trajectory, grounds diagnosis in verified evidence, replays bounded repairs, and checks for regressions before calling anything an improvement.</p>
        </div>
        <div className="heroMetrics">
          <div className="metric"><span>Recorded runs</span><strong>{runs.length}</strong><small>{passCount} pass · {failCount} fail</small></div>
          <div className="metric"><span>Current score</span><strong>{run ? pct(run.score) : '—'}</strong><small>{run ? human(run.status) : 'No run selected'}</small></div>
          <div className="metric"><span>Regression vault</span><strong>{regressionCases.length}</strong><small>immutable test cases</small></div>
        </div>
      </section>

      <section className="loopBar" aria-label="AEGIS reliability loop">
        {loop.map((item, index) => <React.Fragment key={item}>
          <div className={`loopStep ${index <= workflowStage ? 'active' : ''}`}><span>{String(index + 1).padStart(2, '0')}</span>{item}</div>
          {index < loop.length - 1 && <ArrowRight size={14} className="loopArrow" />}
        </React.Fragment>)}
      </section>

      {error && <div className="globalError"><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError('')}>Dismiss</button></div>}

      <section className="workspace">
        <aside className="sidebar">
          <div className="sectionHeading"><FlaskConical size={17} /><div><strong>Test scenario</strong><small>Controlled perturbation</small></div></div>
          <p className="muted compact">Task: read <code>sales.csv</code>, calculate order count + revenue, write <code>report.json</code>.</p>
          <div className="choices">
            {perturbations.map(item => (
              <button key={item.value} className={`choice ${perturbation === item.value ? 'active' : ''}`} onClick={() => setPerturbation(item.value)} disabled={busy !== null}>
                <span><CircleDot size={14} />{item.label}</span><small>{item.hint}</small>
              </button>
            ))}
          </div>
          <button className="primaryBtn" onClick={execute} disabled={busy !== null}>
            {busy === 'run' ? <RefreshCw className="spin" size={17} /> : <Play size={17} />}
            {busy === 'run' ? 'Running experiment…' : 'Run experiment'}
          </button>

          <div className="historyHeader"><span><History size={15} />Recent runs</span><button onClick={() => void refreshOverview()} title="Refresh runs"><RefreshCw size={14} /></button></div>
          <div className="runList">
            {runs.length === 0 && <div className="emptySmall">No recorded runs yet.</div>}
            {[...runs].reverse().slice(0, 8).map(item => (
              <button key={item.run_id} className={`runItem ${run?.run_id === item.run_id ? 'active' : ''}`} onClick={() => selectRun(item)}>
                <span className={`statusDot ${item.status}`} />
                <span><strong>{human(item.perturbation)}</strong><small>{shortId(item.run_id)} · {timestamp(item.started_at)}</small></span>
                <span className="runScore">{pct(item.score)}</span>
              </button>
            ))}
          </div>
        </aside>

        <div className="content">
          <section className="summaryGrid">
            <article className="panel outcomePanel">
              <div className="panelTitle"><Gauge size={17} /> Deterministic outcome</div>
              {!run ? <div className="emptyState">Run a scenario to establish deterministic ground truth.</div> : <>
                <div className={`outcome ${run.status === 'passed' ? 'ok' : 'bad'}`}>
                  {run.status === 'passed' ? <CheckCircle2 /> : <XCircle />}
                  <div><strong>{run.status.toUpperCase()}</strong><small>{pct(run.score)} validator score · trace {run.trace_complete ? 'complete' : 'incomplete'}</small></div>
                  <span className="runId">RUN {shortId(run.run_id)}</span>
                </div>
                <div className="validators">
                  {run.validations.map(validation => <div className="validation" key={validation.validation_id || validation.name}>
                    {validation.passed ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
                    <div><strong>{validation.name}</strong><small>{validation.details || 'No additional details.'}</small></div>
                  </div>)}
                </div>
                {rootFailure && <div className="evidenceCallout">
                  <span>Earliest visible failure evidence</span>
                  <strong>{String(rootFailure.payload.error_type || rootFailure.payload.error_code || rootFailure.message)}</strong>
                  <small>event {shortId(rootFailure.event_id)} · sequence {rootFailure.sequence}</small>
                </div>}
              </>}
            </article>

            <article className="panel workflowPanel">
              <div className="panelTitle"><Sparkles size={17} /> Diagnosis & repair</div>
              {!run ? <div className="emptyState">Select a run first. AEGIS will never diagnose without trace evidence.</div> : <>
                <div className="actionRow">
                  <button className="secondaryBtn" onClick={diagnose} disabled={busy !== null}>
                    {busy === 'diagnose' ? <RefreshCw className="spin" size={16} /> : <Search size={16} />} Diagnose run
                  </button>
                  <button className="ghostBtn" onClick={registerRegressionCase} disabled={busy !== null}>
                    <Archive size={16} /> Add to vault
                  </button>
                </div>
                {!diagnosis && <p className="muted compact">Diagnosis is checked against trace facts and evidence references before it can support a repair.</p>}
                {diagnosis && <div className="diagnosisBlock">
                  <div className="statusLine"><span className={`pill ${diagnosis.status}`}>{diagnosis.status}</span><small>{diagnosis.provider} · {shortId(diagnosis.diagnosis_id)}</small></div>
                  {selectedHypothesis ? <>
                    <strong className="mechanism">{selectedHypothesis.category}: {selectedHypothesis.mechanism}</strong>
                    <div className="confidence"><span>Confidence</span><strong>{pct(selectedHypothesis.confidence)}</strong></div>
                    <div className="evidenceRefs">
                      {selectedHypothesis.supporting_evidence.slice(0, 3).map(ref => <span key={`${ref.event_id}-${ref.fact_id}`}><ShieldCheck size={13} /> event {shortId(ref.event_id)} · fact {shortId(ref.fact_id)}</span>)}
                    </div>
                  </> : <p className="muted compact">No supported hypothesis was produced. Status remains {diagnosis.status}.</p>}
                  {diagnosis.validation_errors.length > 0 && <div className="validationErrors">{diagnosis.validation_errors.join(' · ')}</div>}
                  {diagnosis.status === 'supported' && <button className="secondaryBtn full" onClick={proposeRepair} disabled={busy !== null}>
                    {busy === 'repair' ? <RefreshCw className="spin" size={16} /> : <Wrench size={16} />} Propose bounded repair
                  </button>}
                </div>}
                {repair && <div className="repairBlock">
                  <div><span>Policy change</span><strong>{repair.component}</strong></div>
                  <div className="policyDiff"><code>enabled: {String(repair.old_configuration.enabled)}</code><ArrowRight size={15} /><code>enabled: {String(repair.new_configuration.enabled)}</code></div>
                  <button className="primaryBtn" onClick={evaluateRepair} disabled={busy !== null || repair.status !== 'valid'}>
                    {busy === 'evaluate' ? <RefreshCw className="spin" size={16} /> : <RotateCcw size={16} />} Replay & evaluate
                  </button>
                </div>}
              </>}
            </article>
          </section>

          {evaluation && <section className="panel evaluationPanel">
            <div className="evaluationTop">
              <div><div className="panelTitle"><ShieldCheck size={17} /> Repair evaluation gate</div><p>{evaluation.causal_statement}</p></div>
              <div className={`decision ${evaluation.decision.toLowerCase()}`}>{evaluation.decision}</div>
            </div>
            <div className="comparisonGrid">
              <ComparisonCard title="Original failure" comparison={evaluation.moved_comparison} />
              <ComparisonCard title="Clean behavior" comparison={evaluation.clean_comparison} />
            </div>
            {evaluation.reasons.length > 0 && <div className="reasonStrip">{evaluation.reasons.map(reason => <span key={reason}><ChevronRight size={13} />{reason}</span>)}</div>}
            {!evaluation.metrics && <div className="suiteAction">
              <div>
                <strong>Regression suite</strong>
                <small>{suiteReady ? 'Stress + clean vault cases are ready for a pinned suite replay.' : 'Add both the source stress run and a clean baseline run to the vault.'}</small>
              </div>
              <button className="secondaryBtn" onClick={evaluateRegressionSuite} disabled={busy !== null || !suiteReady || repair?.status !== 'valid'}>
                {busy === 'suite' ? <RefreshCw className="spin" size={16} /> : <Gauge size={16} />} Run suite & measure
              </button>
            </div>}
          </section>}

          {evaluation?.metrics && <section className="panel metricsPanel">
            <div className="panelTitle"><Gauge size={17} /> Reliability metrics</div>
            <div className="metricsGrid">
              <Metric label="Valid pairs" value={`${evaluation.metrics.valid_pairs}/${evaluation.metrics.scheduled_cases}`} />
              <Metric label="Improvements" value={evaluation.metrics.improvement_count} />
              <Metric label="Regressions" value={evaluation.metrics.regression_count} />
              <Metric label="Unresolved" value={evaluation.metrics.unresolved_failure_count} />
              <Metric label="Baseline success" value={pct(evaluation.metrics.baseline_case_success_rate)} />
              <Metric label="Candidate success" value={pct(evaluation.metrics.candidate_case_success_rate)} />
            </div>
            {evaluation.case_results.length > 0 && <div className="suiteResults">
              {evaluation.case_results.map(item => <div className={`suiteResult ${item.transition}`} key={`${item.case_id}-${item.role}`}>
                <span>{item.role}</span>
                <strong>{item.baseline_outcome} <ArrowRight size={13} /> {item.candidate_outcome}</strong>
                <small>{human(item.transition)} · {item.valid_pair ? 'valid pair' : 'invalid pair'}</small>
              </div>)}
            </div>}
          </section>}

          <section className="detailGrid">
            <article className="panel timelinePanel">
              <div className="panelTitle"><Activity size={17} /> Execution trace</div>
              {!run ? <div className="emptyState">Ordered trace evidence appears here.</div> : <div className="timeline">
                {run.events.map(event => <div className="event" key={event.event_id}>
                  <div className="step">{String(event.sequence).padStart(2, '0')}</div>
                  <div className="eventBody">
                    <div><span className="eventType">{human(event.kind)}</span><strong>{event.message}</strong><small>{event.elapsed_ms} ms · {event.actor}</small></div>
                    {Object.keys(event.payload || {}).length > 0 && <details><summary>payload</summary><code>{JSON.stringify(event.payload, null, 2)}</code></details>}
                  </div>
                </div>)}
              </div>}
            </article>

            <div className="sideStack">
              <article className="panel">
                <div className="panelTitle"><Bot size={17} /> Model execution</div>
                {!run ? <div className="emptyState compactEmpty">No selected run.</div> : providerRows.length === 0 ? <div className="deterministicBadge"><ShieldCheck size={16} /><div><strong>Deterministic mode</strong><small>No model provider metadata on this run.</small></div></div> : <div className="providerList">
                  {providerRows.map(row => <div className="providerRow" key={row.role}><span>{row.role}</span><strong>{row.provider}</strong><small>{row.model}</small><em>{row.status}</em></div>)}
                </div>}
              </article>

              <article className="panel vaultPanel">
                <div className="panelTitle"><Archive size={17} /> Regression vault</div>
                {regressionCases.length === 0 ? <div className="emptyState compactEmpty">No regression cases registered yet.</div> : <div className="vaultList">
                  {[...regressionCases].reverse().slice(0, 6).map(item => <div className="vaultItem" key={`${item.case_id}-${item.case_version}`}>
                    <span className="vaultIcon"><Archive size={14} /></span>
                    <div><strong>{item.name}</strong><small>v{item.case_version} · source {shortId(item.source_run_id)}</small></div>
                    <span className="vaultMode">{item.exposure_requirement.mode === 'must_activate' ? 'stress' : 'clean'}</span>
                  </div>)}
                </div>}
              </article>
            </div>
          </section>
        </div>
      </section>

      <footer><span>Models can propose. Deterministic validators establish facts.</span><span>AEGIS · M06 Reliability Dashboard</span></footer>
    </main>
  );
}

function ComparisonCard({ title, comparison }: { title: string; comparison: RunComparison }) {
  const improved = comparison.baseline_outcome === 'failed' && comparison.candidate_outcome === 'passed';
  const regressed = comparison.baseline_outcome === 'passed' && comparison.candidate_outcome === 'failed';
  return <div className={`comparisonCard ${improved ? 'improved' : regressed ? 'regressed' : ''}`}>
    <div className="comparisonTitle"><span>{title}</span><small>{comparison.comparable ? 'matched run pair' : 'not comparable'}</small></div>
    <div className="transition"><strong>{comparison.baseline_outcome.toUpperCase()}</strong><ArrowRight size={17} /><strong>{comparison.candidate_outcome.toUpperCase()}</strong></div>
    <div className="comparisonFacts"><span>Failure resolved <b>{comparison.original_failure_resolved == null ? '—' : comparison.original_failure_resolved ? 'yes' : 'no'}</b></span><span>Tool delta <b>{comparison.tool_call_count_delta >= 0 ? '+' : ''}{comparison.tool_call_count_delta}</b></span><span>Recovery <b>{comparison.recovery_summary.attempted ? 'attempted' : 'not used'}</b></span></div>
  </div>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metricTile"><span>{label}</span><strong>{value}</strong></div>;
}

createRoot(document.getElementById('root')!).render(<App />);
