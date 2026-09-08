import React, { useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ShieldCheck, FlaskConical, Play, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';
import './styles.css';

type Perturbation = 'none' | 'file_moved' | 'field_renamed' | 'tool_timeout' | 'partial_response';

type TraceEvent = {
  id: string;
  step: number;
  type: string;
  message: string;
  payload: Record<string, unknown>;
};

type Validation = {
  name: string;
  passed: boolean;
  details: string;
};

type RunRecord = {
  run_id: string;
  perturbation: Perturbation;
  status: 'passed' | 'failed' | 'running' | 'created';
  score: number;
  events: TraceEvent[];
  validations: Validation[];
};

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const perturbations: {value: Perturbation; label: string; hint: string}[] = [
  { value: 'none', label: 'Baseline', hint: 'Perfect environment' },
  { value: 'file_moved', label: 'Moved Resource', hint: 'Expected file changes location' },
  { value: 'field_renamed', label: 'Schema Drift', hint: 'Input field is renamed' },
  { value: 'tool_timeout', label: 'Tool Timeout', hint: 'First read fails temporarily' },
  { value: 'partial_response', label: 'Partial Response', hint: 'Tool returns incomplete data' },
];

function App() {
  const [perturbation, setPerturbation] = useState<Perturbation>('none');
  const [run, setRun] = useState<RunRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const passed = run?.status === 'passed';
  const rootFailure = useMemo(() => run?.events.find(e => e.payload?.error_type), [run]);

  async function execute() {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${API}/api/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ perturbation }),
      });
      if (!response.ok) throw new Error(`Backend returned ${response.status}`);
      setRun(await response.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Run failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand"><ShieldCheck size={24}/><span>AEGIS</span></div>
        <div className="tag">Agent Reliability Laboratory</div>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">TEST · STRESS · DIAGNOSE · IMPROVE</p>
          <h1>Find out why an AI agent fails.</h1>
          <p className="lede">Run an agent in a controlled environment, inject a safe disturbance, capture its full trajectory, and verify the outcome against deterministic ground truth.</p>
        </div>
        <div className="scoreCard">
          <span>Reliability score</span>
          <strong>{run ? `${Math.round(run.score * 100)}%` : '—'}</strong>
          <small>{run ? `${run.status.toUpperCase()} · ${run.perturbation}` : 'No run yet'}</small>
        </div>
      </section>

      <section className="grid two">
        <article className="panel">
          <div className="panelTitle"><FlaskConical size={18}/> Test scenario</div>
          <p className="muted">Agent task: read sales.csv, calculate total revenue + order count, and write report.json.</p>
          <div className="choices">
            {perturbations.map(item => (
              <button key={item.value} className={`choice ${perturbation === item.value ? 'active' : ''}`} onClick={() => setPerturbation(item.value)}>
                <span>{item.label}</span><small>{item.hint}</small>
              </button>
            ))}
          </div>
          <button className="runBtn" onClick={execute} disabled={loading}>
            <Play size={17}/>{loading ? 'Running…' : 'Run experiment'}
          </button>
          {error && <div className="error"><AlertTriangle size={16}/>{error}</div>}
        </article>

        <article className="panel">
          <div className="panelTitle"><Activity size={18}/> Outcome</div>
          {!run && <div className="empty">Run a baseline or stress scenario to see evidence.</div>}
          {run && <>
            <div className={`outcome ${passed ? 'ok' : 'bad'}`}>
              {passed ? <CheckCircle2/> : <XCircle/>}
              <div><strong>{passed ? 'PASS' : 'FAIL'}</strong><small>{Math.round(run.score * 100)}% validator score</small></div>
            </div>
            <div className="validators">
              {run.validations.map(v => <div className="validation" key={v.name}>
                {v.passed ? <CheckCircle2 size={16}/> : <XCircle size={16}/>}
                <div><strong>{v.name}</strong><small>{v.details}</small></div>
              </div>)}
            </div>
            {rootFailure && <div className="rootCause">
              <span>Observed failure evidence</span>
              <strong>{String(rootFailure.payload.error_type)}</strong>
              <small>{String(rootFailure.payload.error)}</small>
            </div>}
          </>}
        </article>
      </section>

      <section className="panel timelinePanel">
        <div className="panelTitle"><Activity size={18}/> Execution trace</div>
        {!run ? <div className="empty">The ordered evidence timeline will appear here.</div> :
          <div className="timeline">
            {run.events.map(event => <div className="event" key={event.id}>
              <div className="step">{String(event.step).padStart(2, '0')}</div>
              <div className="eventBody"><div><span className="eventType">{event.type}</span><strong>{event.message}</strong></div>
              {Object.keys(event.payload || {}).length > 0 && <code>{JSON.stringify(event.payload)}</code>}</div>
            </div>)}
          </div>
        }
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
