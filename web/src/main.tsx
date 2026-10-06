import React from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlow, Background, Controls, MiniMap, Handle, Position, useReactFlow, useNodesInitialized, type NodeProps, type Node, type Edge, type NodeChange } from '@xyflow/react';
import { GitBranch, ArrowUpRight, Play, Square, Code2, Box, ShieldCheck, ChevronRight, RotateCcw } from 'lucide-react';
import '@xyflow/react/dist/style.css';
import './style.css';

import type { State, TreeNode, RunEvent, Observation } from './contracts';
// Domain viewers narrow the schema's intentionally generic domain payload.
type WorldState = State & { payload: Record<string, any> };
type FutureNode = Omit<TreeNode, 'state' | 'actual'> & { state: WorldState; actual: (Observation & { state: WorldState }) | null };
type Event = Omit<RunEvent, 'data'> & { data: Record<string, any> };
type Run = { id: string; status: string; config: { task?: {title: string}; request?: { domain: string; mode: string; task?: string } }; result: Record<string, any> };
const pct = (x: number | null | undefined) => x == null ? 'Unknown' : `${Math.round(x * 100)}%`;

type ReliabilityStats = {
  n: number; context_n: number; weight: number; brier: number; risk_brier: number;
  drift: boolean; risk_drift: boolean;
};
type TrustSnapshot = { context: string; engines: Record<string, ReliabilityStats> };

function decisionTrust(events: Event[], stateId?: string, nodeId?: string) {
  const start = events.findIndex(e => e.kind === 'observed' && e.data.state.id === stateId);
  if (start < 0) return { before: null, after: null };
  const end = events.findIndex((e, index) => index > start && e.kind === 'observed');
  const round = events.slice(start, end < 0 ? undefined : end);
  const before = round.find(e => e.kind === 'trust_snapshot')?.data as TrustSnapshot | undefined;
  const outcome = round.findIndex(e => e.kind === 'outcome' && e.data.node_id === nodeId);
  const after = outcome < 0 ? undefined : round.slice(outcome + 1).find(e => e.kind === 'trust_updated')?.data as TrustSnapshot | undefined;
  return { before: before ?? null, after: after ?? null };
}

function EngineTrust({ snapshots, predictions }: { snapshots: ReturnType<typeof decisionTrust>; predictions: FutureNode['predictions'] }) {
  const { before, after } = snapshots;
  if (!before && !after) return null;
  const engines = new Map(predictions.map(p => [`${p.engine_id}@${p.engine_version}`, p]));
  const number = (value?: number) => value == null ? 'Unknown' : value.toFixed(3);
  const stats = (value?: ReliabilityStats) => value ? <>
    <b>Weight {pct(value.weight)}</b>
    <span>{value.n} labels · {value.context_n} in this context</span>
    <span>Success Brier {number(value.brier)}</span>
    <span>Risk Brier {number(value.risk_brier)}</span>
    {(value.drift || value.risk_drift) && <span className="trust-drift">Drift detected</span>}
  </> : <span>Unknown · no recorded statistics</span>;
  return <div className="engine-trust">
    <h4>Engine reliability</h4>
    <p className="muted">{after ? 'Decision input → after observed action' : 'Decision input. This branch has no observed trust update.'}</p>
    {engines.size > 0 ? <table className="trust-table" aria-label="Recorded engine reliability for this decision">
      <thead><tr><th>Engine</th><th>Decision input</th><th>After action</th></tr></thead>
      <tbody>{[...engines].map(([key, prediction]) => <tr key={key}>
        <th scope="row" title={key}>{prediction.engine_id}<small>Version {prediction.engine_version.slice(0, 12)}</small></th>
        <td>{stats(before?.engines[key])}</td>
        <td>{after ? stats(after.engines[key]) : <span>No observed update</span>}</td>
      </tr>)}</tbody>
    </table> : <p className="muted">No measured engine reliability is available for this decision.</p>}
    {engines.size > 0 && <p className="muted">Brier includes a conservative prior; lower values indicate less prediction error.</p>}
    <details><summary>Calibrated engine trust <span>recorded snapshots</span></summary><pre>{JSON.stringify(snapshots, null, 2)}</pre></details>
  </div>;
}

function stateTitle(state: WorldState) {
  const p = state.payload;
  if (state.domain === 'physical' && Array.isArray(p.object)) return `Cube at (${p.object.map((v: number) => v.toFixed(2)).join(', ')}) m`;
  return p.task_title || 'Software workspace';
}
function stateSummary(state: WorldState) {
  const p = state.payload;
  return `Stage: ${p.stage || 'observed'} · ${state.domain === 'software' ? Object.keys(p.files || {}).join(', ') || 'runtime state' : p.held ? 'object held' : 'object released'}`;
}

function FutureCard({ data }: NodeProps<Node<{ future: FutureNode; hiddenChildren?: number }>>) {
  const n = data.future, e = n.evaluation;
  return <div className={`future-card ${n.status}`}>
    <Handle type="target" position={Position.Left}/>
    <div className="node-eyebrow"><span className="node-dot"/>{n.action ? n.status : n.kind==='outcome' ? 'POSSIBLE OUTCOME' : 'CURRENT STATE'}<span>{n.action ? `STEP ${n.depth}` : n.kind==='outcome' ? pct(n.outcome_probability?.value) : 'OBSERVED'}</span></div>
    <strong>{n.action?.name || (n.label || (stateTitle(n.state)))}</strong>
    {n.action ? <><div className="probability"><b>{pct(e?.success)}</b><span>action success estimate</span></div>
      <div className="node-metrics"><span>Risk <b>{pct(e?.risk_upper)}</b></span><span>Uncertainty <b>{pct(e?.uncertainty)}</b></span></div>
      <div className="node-footer"><span>{n.predictions.length} engine results</span><span>{e && e.disagreement > 0 ? '△ Disagreement' : n.actual ? '● Outcome observed' : 'Imagined future'}</span></div>{!!data.hiddenChildren && <div className="node-branches">Later futures · expand in inspector</div>}</> :
      <p>{stateSummary(n.state)}</p>}
    <Handle type="source" position={Position.Right}/>
  </div>;
}
const nodeTypes = { future: FutureCard };
function AutoFit({ count }: { count: number }) {
  const { fitView } = useReactFlow();
  const initialized = useNodesInitialized();
  React.useEffect(() => { if (!initialized) return; const timer = setTimeout(() => { void fitView({ padding: .16, duration: 180 }); }, 100); return () => clearTimeout(timer); }, [count, initialized, fitView]);
  return null;
}

function MeasuredTree({ nodes, edges, onSelect }: { nodes: Node<{ future: FutureNode }>[], edges: Edge[], onSelect: (id: string) => void }) {
  const [dimensions, setDimensions] = React.useState<Record<string, {width:number;height:number}>>({});
  const measuredNodes = React.useMemo(() => nodes.map(n => ({...n, measured:dimensions[n.id]})), [nodes,dimensions]);
  const onNodesChange = React.useCallback((changes: NodeChange[]) => {
    setDimensions(previous => {
      let next = previous;
      for (const change of changes) {
        if (change.type !== 'dimensions' || !change.dimensions) continue;
        const size = change.dimensions, old = previous[change.id];
        if (old?.width === size.width && old?.height === size.height) continue;
        if (next === previous) next = {...previous};
        next[change.id] = size;
      }
      return next;
    });
  }, []);
  return <ReactFlow nodes={measuredNodes} edges={edges} onNodesChange={onNodesChange} nodeTypes={nodeTypes} fitView fitViewOptions={{padding:.2}} minZoom={.25} maxZoom={1.3} nodesDraggable={false} onNodeClick={(_,node)=>onSelect(node.id)}><AutoFit count={nodes.length}/><Background gap={20} size={1} color="#293343"/><Controls showInteractive={false}/><MiniMap nodeColor={n=>n.data.future && (n.data.future as FutureNode).status==='executed'?'#beec83':'#405675'} maskColor="#10151dba"/></ReactFlow>;
}

export function reconstruct(events: Event[]) {
  const map = new Map<string, FutureNode>();
  for (const e of events) {
    if (e.kind === 'node' || e.kind === 'node_updated') map.set(e.data.node.id, e.data.node);
    if (e.kind === 'decision' && e.data.node_id) { const n = map.get(e.data.node_id); if (n) map.set(n.id, { ...n, status: 'selected' }); }
    if (e.kind === 'outcome') { const n = map.get(e.data.node_id); if (n) map.set(n.id, { ...n, status: 'executed', actual: e.data.observation }); }
  }
  return [...map.values()];
}

function layout(futures: FutureNode[], rootId: string, collapsed: Set<string>) {
  const visible: FutureNode[] = [], ids = new Set([rootId]);
  for (const n of futures) if (ids.has(n.id) || (n.parent_id && ids.has(n.parent_id) && !collapsed.has(n.parent_id))) { visible.push(n); ids.add(n.id); }
  let row = 0;
  const positions = new Map<string, number>();
  function visit(id: string): number {
    const children = visible.filter(n => n.parent_id === id);
    const y = children.length ? children.map(n => visit(n.id)).reduce((a, b) => a + b, 0) / children.length : row++ * 230;
    positions.set(id, y); return y;
  }
  visit(rootId);
  const layers = new Map<string, number>(); visible.forEach(n => layers.set(n.id, n.parent_id ? (layers.get(n.parent_id) || 0) + 1 : 0));
  const nodes: Node<{future: FutureNode; hiddenChildren?: number}>[] = visible.map(n => ({ id: n.id, type: 'future', position: { x: (layers.get(n.id) || 0) * 320, y: positions.get(n.id) || 0 }, data: { future: n, hiddenChildren: collapsed.has(n.id) ? futures.filter(child => child.parent_id === n.id).length : 0 } }));
  const edges: Edge[] = visible.filter(n => n.parent_id && ids.has(n.parent_id)).map(n => ({ id: n.id, source: n.parent_id!, target: n.id, type: 'smoothstep', animated: n.status === 'selected', style: { stroke: n.status === 'executed' || n.status === 'selected' ? '#beec83' : n.status === 'rejected' ? '#724548' : '#425267', strokeWidth: 1.5 } }));
  return { nodes, edges };
}

function PhysicalView({ state }: { state: WorldState }) {
  const p = state.payload, project = (v: number[]) => [180 + v[0] * 390, 115 - v[1] * 200 - v[2] * 100];
  if (!p.object) return null;
  const [x, y] = project(p.object), [tx, ty] = project(p.target);
  const boxes = [{center:p.obstacle, half:p.obstacle_half || [.07,.11,.1]}, ...(p.extra_obstacles || [])].filter(b => Array.isArray(b.center));
  return <svg viewBox="0 0 360 200" role="img" aria-label="Measured cube position, obstacle and trajectory" className="world-view">
    <defs><pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" fill="none" stroke="#273243" strokeWidth=".5"/></pattern></defs>
    <rect width="360" height="200" fill="url(#grid)"/>
    {boxes.map((box, i) => { const [ox,oy] = project(box.center), w = box.half[0]*390, h = box.half[1]*200+box.half[2]*100; return <g key={i}><rect x={ox-w} y={oy-h} width={2*w} height={2*h} rx="3" fill="#b7797240" stroke="#b77972"/><text x={ox} y={oy+h+12} textAnchor="middle" fill="#b77972" fontSize="9">KEEP OUT {i+1}</text></g>; })}
    <circle cx={tx} cy={ty} r="16" fill="#beec8315" stroke="#beec83" strokeDasharray="3 3"/>
    <polyline points={(p.trajectory || []).map((v: number[]) => project(v).join(',')).join(' ')} fill="none" stroke="#7ea9eb" strokeWidth="2"/>
    <rect x={x-8} y={y-8} width="16" height="16" rx="3" fill="#beec83"/>
    <text x="12" y="186" fill="#7e8da4" fontSize="10">{state.provenance} · meters · projected x/y/z</text>
  </svg>;
}

function App() {
  const [domain, setDomain] = React.useState<'software'|'physical'>('software');
  const [softwareTask, setSoftwareTask] = React.useState<'default'|'release'>('default');
  const [starting, setStarting] = React.useState(false);
  const [baseline, setBaseline] = React.useState(false), [events, setEvents] = React.useState<Event[]>([]);
  const [run, setRun] = React.useState<Run | null>(null), [runs, setRuns] = React.useState<Run[]>([]);
  const [error, setError] = React.useState(''), [health, setHealth] = React.useState<Record<string, any>>({});
  const [selected, setSelected] = React.useState<string | null>(null), [round, setRound] = React.useState(0);
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());
  const [cursor, setCursor] = React.useState<number | null>(null);
  const [openedArchive, setOpenedArchive] = React.useState(false);
  const busy = starting || run?.status === 'running' || run?.status === 'queued';
  const refresh = async () => { try { const response = await fetch('/api/runs'); if (!response.ok) throw new Error(); setRuns(await response.json()); } catch { setError('Run history is unavailable. Check the API connection.'); } };
  React.useEffect(() => { fetch('/api/health').then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(setHealth).catch(() => { setHealth({status:'unavailable'}); setError('Start the PreAct API to connect.'); }); void refresh(); }, []);
  React.useEffect(() => {
    if (!run?.id) return;
    let closed = false;
    const source = new EventSource(`/api/runs/${run.id}/stream`);
    source.onerror = () => { if (!closed && ['queued','running'].includes(run.status)) { setHealth(previous => ({...previous,status:'unavailable'})); setError('Connection lost. Showing last recorded events.'); } };
    source.onmessage = (message) => { if (closed) return; const event: Event = JSON.parse(message.data); setEvents(previous => previous.some(e => e.seq === event.seq) ? previous : [...previous, event].sort((a,b) => a.seq-b.seq)); };
    const timer = setInterval(async () => {
      try {
      const response = await fetch(`/api/runs/${run.id}`);
      if (!response.ok) throw new Error();
      const record = await response.json();
      if (!closed) { setHealth(previous => ({...previous,status:'ok'})); setError(previous => previous === 'Connection lost. Showing last recorded events.' ? '' : previous); }
      if (!closed) setRun(record);
      if (!['queued','running'].includes(record.status)) {
        // Status polling can finish before SSE's final flush. Reconcile the durable
        // archive before closing, so completed rounds/outcomes cannot disappear.
        const archiveResponse = await fetch(`/api/runs/${run.id}/events`);
        if (!archiveResponse.ok) throw new Error();
        const archive: Event[] = await archiveResponse.json();
        if (!closed) setEvents(archive);
        source.close(); clearInterval(timer); if (!closed) await refresh();
      }
      } catch { if (!closed) { setHealth(previous => ({...previous,status:'unavailable'})); setError('Connection lost. Showing last recorded events.'); } }
    }, 600);
    return () => { closed = true; source.close(); clearInterval(timer); };
  }, [run?.id]);
  const visibleEvents = events.filter(e => cursor == null || e.seq <= cursor);
  const futures = reconstruct(visibleEvents), roots = futures.filter(n => !n.parent_id);
  // Keep first actions readable while retaining the actual deeper search for inspection.
  const collapsed = new Set(futures.filter(n => n.action && !expanded.has(n.id)).map(n => n.id));
  const root = roots[Math.min(round, roots.length-1)];
  const graph = root ? layout(futures, root.id, collapsed) : { nodes: [], edges: [] };
  const detail = futures.find(n => n.id === selected) || graph.nodes.find(n => n.data.future.status === 'executed')?.data.future;
  const branchGate = [...visibleEvents].reverse().find(e => e.kind === 'gate_preview' && e.data.node_id === detail?.id);
  const searchSelection = [...visibleEvents].reverse().find(e => e.kind === 'search_selected' && e.data.node_id === detail?.id);
  const branchStop = [...visibleEvents].reverse().find(e => e.kind === 'search_pruned' && e.data.node_id === detail?.id);
  const errors = visibleEvents.filter(e => e.kind === 'prediction_error' && (!detail || e.data.node_id === detail.id));
  const trust = decisionTrust(visibleEvents, root?.state.id, detail?.id);
  async function start() {
    setOpenedArchive(false); setStarting(true); setRun(null); setError(''); setEvents([]); setSelected(null); setRound(0); setCursor(null); setExpanded(new Set());
    try {
      const response = await fetch('/api/runs', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ domain, mode: baseline ? 'direct' : 'preact', seed: runs.length, task: domain === 'software' ? softwareTask : 'default' }) });
      if (!response.ok) throw new Error(await response.text());
      const { id } = await response.json();
      setEvents([]);
      setRun(await (await fetch(`/api/runs/${id}`)).json());
    } catch (e) { setError(String(e)); } finally { setStarting(false); }
  }
  async function open(record: Run) { if (record.config.request?.domain === 'physical' || record.config.request?.domain === 'software') setDomain(record.config.request.domain); setOpenedArchive(true); setEvents(await (await fetch(`/api/runs/${record.id}/events`)).json()); setRun(record); setRound(0); setSelected(null); setCursor(null); setExpanded(new Set()); }
  return <div className="app">
    <header><a className="brand" href="/" aria-label="PreAct home"><GitBranch size={23}/><b>PreAct</b><span>COUNTERFACTUAL RUNTIME</span></a><div className={`connection ${health.status || 'connecting'}`}><i/>{health.status === 'unavailable' ? 'Disconnected' : health.status !== 'ok' ? 'Connecting…' : health.mode === 'local' ? 'Local execution lab' : health.mode === 'local-model' ? 'Local NVIDIA model lab' : health.mode === 'cloud' ? 'Cloud runtime' : 'Connected runtime'}<span>v0.1</span></div></header>
    <main><section className="intro"><div><p className="eyebrow">A LITTLE FORESIGHT. A BETTER NEXT MOVE.</p><h1>See what could happen.<br/><em>Then decide what should.</em></h1><p className="subtitle">Explore possible futures, verify the consequential ones,<br/>and learn from what actually happens.</p></div><div className="loop"><span>Observe</span><ChevronRight/><span>Imagine</span><ChevronRight/><span className="active">Verify</span><ChevronRight/><span>Act</span><div><RotateCcw size={13}/> Compare reality. Calibrate trust.</div></div></section>
      <section className="workspace"><div className="toolbar"><div className="tabs"><button className={domain==='software'?'active':''} onClick={()=>setDomain('software')}><Code2 size={16}/>Software World</button><button className={domain==='physical'?'active':''} onClick={()=>setDomain('physical')}><Box size={16}/>Physical World</button></div>{domain==='software' && <select className="task-select" aria-label="Software task" value={softwareTask} onChange={e=>setSoftwareTask(e.target.value as 'default'|'release')}><option value="default">Checkout repair</option><option value="release">Migration + release</option></select>}<label className="baseline"><input type="checkbox" checked={baseline} onChange={e=>setBaseline(e.target.checked)}/>Direct Agent baseline</label><button className="start" onClick={start} disabled={busy}><Play size={14}/>{busy?'Exploring futures…':'Run experiment'}</button>{busy && run && <button className="stop" onClick={()=>fetch(`/api/runs/${run!.id}`,{method:'DELETE'})} aria-label="Stop run"><Square size={14}/></button>}</div>
      <div className="experiment-heading"><div><span className="eyebrow">{run?.config.request?.mode==='direct'?'DIRECT AGENT':'PREACT'} / {run?.config.request?.domain || domain}</span><h2>{root?.state.payload.task_title || run?.config.task?.title || ((run?.config.request?.domain || domain)==='software'?run?.config.request?.task==='release'?'Repair, migrate, configure and verify a release.':'Repair a bug without creating a regression.':'Move the cube without crossing the obstacle.')}</h2></div><span className={`run-status ${run?.status}`}>{run?.status || 'Ready to explore'}</span></div>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="tree-area"><div className="tree-label"><GitBranch size={14}/> FUTURE TREE <span>One shared Core. Two different worlds.</span></div>{roots.length>1 && <select className="round-select" aria-label="Decision round" value={round} onChange={e=>{setRound(Number(e.target.value));setSelected(null);}}>{roots.map((n,i)=><option key={n.id} value={i}>Decision {i+1}</option>)}</select>}
      {root ? <MeasuredTree key={root.id} nodes={graph.nodes} edges={graph.edges} onSelect={setSelected}/> : <div className="empty-tree"><div className="empty-branch"><span>Current state</span><i/><span>Possible future A</span><span>Possible future B</span><span>Possible future C</span></div><p>Run an experiment to build a tree from real predictions and verification.</p></div>}
      <div className="legend"><span><i className="imagined"/>Imagined</span><span><i className="verified"/>Verified</span><span><i className="rejected"/>Rejected</span><span><i className="executed"/>Executed & observed</span></div></div>
      {events.length>0 && <div className="replay"><span>{cursor == null && !openedArchive ? 'EVENT TIMELINE' : 'RECORDED REPLAY'}</span><input aria-label="Event replay" type="range" min="1" max={events.length} value={cursor || events.length} onChange={e=>setCursor(Number(e.target.value))}/><button onClick={()=>setCursor(null)}>Latest</button><span>{visibleEvents.length} / {events.length}</span></div>}
      <div className="evidence-area"><section><p className="eyebrow">BRANCH INSPECTOR</p><h3>{detail?.action?.name || 'Select a future to inspect its evidence.'}</h3>{detail ? <><p className="muted">{detail.action?.rationale || detail.state.provenance}</p>{searchSelection && <details><summary>Why this first action?</summary><pre>{JSON.stringify(searchSelection.data,null,2)}</pre></details>}{branchStop && <p className="muted">Descendant search stopped: {branchStop.data.reason}</p>}{futures.some(n=>n.parent_id===detail.id) && <button className="branch-toggle" onClick={()=>setExpanded(previous=>{ const next=new Set(previous); if(next.has(detail.id)) next.delete(detail.id); else next.add(detail.id); return next; })}>{expanded.has(detail.id)?'Collapse':'Expand'} future branch</button>}{branchGate && <div className="gate-reasons"><b>Decision Gate · {branchGate.data.gate.decision.replaceAll('_',' ')}</b><p>{branchGate.data.gate.reasons.join(' · ')}</p></div>}<div className="checks">{Object.entries(detail.evaluation?.checks || {}).map(([k,v])=><span key={k} className={v===true?'pass':v===false?'fail':''}>{v===true?'✓':v===false?'×':'?'} {k}</span>)}</div>{detail.evaluation && Object.keys(detail.evaluation.disagreement_by_claim || {}).length > 0 && <div className="disagreement-detail"><b>Engine disagreement · {pct(detail.evaluation.disagreement)}</b><p className="muted">{branchGate?.data.gate.decision === 'execute' ? 'Qualified measurements resolve the decision; earlier differences remain visible.' : 'Differences remain visible. The Decision Gate explains whether evidence is sufficient.'}</p>{Object.entries(detail.evaluation.disagreement_by_claim || {}).map(([claim,value])=><span key={claim}>{claim.replaceAll('_',' ')} <b>{pct(value)}</b></span>)}</div>}{detail.predictions.map(p=><details key={p.id}><summary><ShieldCheck size={14}/><b>{p.engine_id}</b><span>{p.evidence}</span><ArrowUpRight size={13}/></summary><p>{p.assumptions.join(' · ')}</p><pre>{JSON.stringify({success:p.success,risk:p.risk,checks:p.mandatory_checks,provenance:p.raw,refines:p.refines_engine_ids,rollouts:p.sample_count},null,2)}</pre>{Object.entries(p.artifacts).map(([name,digest])=><div key={name}><a href={`/api/artifacts/${digest}`} target="_blank" rel="noreferrer">{name} · {digest.slice(0,12)}</a>{name.includes('video') && <video controls preload="metadata" src={`/api/artifacts/${digest}`} aria-label={`${p.engine_id} generated future video`}/>}</div>)}</details>)}{detail.state.domain==='software' && <pre className="source">{String(detail.action?.payload.source || Object.entries(detail.action?.payload.files || detail.state.payload.files || {}).map(([name,source]) => `${name}\n${source}`).join('\n\n'))}</pre>}</> : <p className="muted">Every estimate retains its engine, assumptions, uncertainty and measurement scope.</p>}</section>
      <section><p className="eyebrow">PREDICTION → REALITY</p><h3>{detail?.actual ? detail.actual.unsafe?'An unsafe outcome was observed.':'The selected action was executed.' : 'Reality closes the loop.'}</h3>{detail?.actual?.state.domain==='physical' && <><PhysicalView state={detail.actual.state}/>{detail.actual.state.payload.camera_video && <video controls src={`/api/artifacts/${detail.actual.state.payload.camera_video}`} aria-label="Observed camera video"/>}</>}<p className="muted">{detail?.actual ? `Goal ${detail.actual.success?'complete':'still in progress'} · ${detail.actual.state.provenance}` : 'Only executed branches receive outcome labels. Unchosen futures stay imagined.'}</p>{errors.map(e=><div className="error-row" key={e.seq}><span>{e.data.engine_id}<small>Predicted {pct(e.data.predicted)} · observed {pct(e.data.observed)}</small></span><b>{e.data.brier.toFixed(4)}<small>Brier error</small></b></div>)}<EngineTrust snapshots={trust} predictions={detail?.predictions || []}/></section></div>
      <div className="run-summary">{[['Task success',run?.result.success==null?'—':run.result.success?'Yes':'No'],['Unsafe outcome',run?.result.unsafe==null?'—':run.result.unsafe?'Observed':'None observed'],['Engine calls',run?.result.calls ?? '—'],['Latency',run?.result.latency_ms ? `${(run.result.latency_ms/1000).toFixed(2)}s`:'—']].map(([label,value])=><div key={label}><span>{label}</span><b>{value}</b></div>)}</div></section>
      <section className="history"><p className="eyebrow">EXPERIMENT HISTORY</p>{runs.length ? runs.slice(0,8).map(r=><button key={r.id} onClick={()=>open(r)}><span>{r.config.request?.domain || 'experiment'} / {r.config.request?.mode || 'preact'}</span><code>{r.id.slice(0,8)}</code><span>{r.status}</span><ArrowUpRight size={14}/></button>) : <p className="muted">Experiments and prediction errors are saved between sessions.</p>}</section>
    </main><footer><span>PreAct explores. Engines predict. Evidence decides.</span><span>{health.evidence_scope || 'Connecting…'}</span></footer>
  </div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
