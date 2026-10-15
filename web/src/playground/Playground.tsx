import React from 'react';
import { ReactFlow, Background, Handle, Position, type NodeProps, type Node, type Edge } from '@xyflow/react';
import { ArrowLeft, ArrowRight, Check, GitBranch, Pause, Play, RotateCcw, SkipForward } from 'lucide-react';
import type { PlaygroundSession, PlaygroundSnapshot } from '../playground-contracts';
import { decisionStart, modules, reconstructEvidence, type ModuleId, type PlaygroundEvent } from './evidence';
import { cognitiveCycle, cycleStops, firstWorkingCycle } from './cycle';
import CognitiveLoop, { actionName, moduleSummary } from './CognitiveLoop';
import ArchitectureWorld, { architectureStops } from './ArchitectureWorld';
import ModuleImplementation from './ModuleImplementation';
import './playground.css';
import './architecture.css';

const scenarios = { stable_high: '機械が速いまま安定', stable_low: '機械が遅いまま安定', high_to_low: '途中で機械が遅くなる', low_to_high: '途中で機械が速くなる' } as const;
const terminal = new Set(['completed', 'stopped', 'failed', 'interrupted', 'abstained', 'failed_task', 'complete']);
const statusNames: Record<string, string> = { preparing: '正式な準備実行中', running: '認知 round を実行中', pausing: 'この round の確定後に停止', paused: '一時停止・次の一手を待機', stopping: '実行中の round の確定待ち', stopped: '停止済み', completed: '観測ウィンドウ完了', failed: '処理失敗・記録を確認', interrupted: '中断済み・再実行しません', recorded: '独立監査済み保存記録', abstained: '実行を見送り・証拠を確認', failed_task: 'タスク未達・実記録を確認', complete: 'Goal達成' };
type Evidence = ReturnType<typeof reconstructEvidence>;
type CandidateData = { label?: string; candidate?: any; qualification?: string; selected?: boolean; actual?: any; value?: any; onClick: () => void };

function CandidateCard({ data }: NodeProps<Node<CandidateData>>) {
  const c = data.candidate;
  return <button className={`pg-future ${data.selected ? 'chosen' : ''} ${data.qualification || ''}`} onClick={data.onClick}>
    <Handle type="target" position={Position.Left}/>
    <span>{c ? data.selected ? '選択Action' : '候補Action' : '正式な観測'}</span>
    <strong>{c?.action.name || data.label}</strong>
    {c && <><b>{data.value ? `価値 ${Number(data.value.value).toFixed(2)}` : '予測値なし'}</b><small>{c.basis === 'fixed_prior_fallback' ? '固定事前 fallback' : data.qualification === 'qualified' ? '検証済み候補' : data.qualification === 'rejected' ? 'Gateで除外' : '検証結果待ち'} · {c.paths?.length ? `H${c.paths[0].queue.length} 指標` : c.action.kind === 'probe_service' ? 'Probe・未来値なし' : '予測なし / unknown'}</small>
      {data.actual && <em><Check size={12}/>Actual · Tick {data.actual.state.payload.tick}</em>}</>}
    {!c && <Handle type="source" position={Position.Right}/>}
  </button>;
}
const nodeTypes = { candidate: CandidateCard };

function Futures({ evidence, onSelect }: { evidence: Evidence; onSelect: (fingerprint: string) => void }) {
  const trace = evidence.trace;
  const nodes: Node<CandidateData>[] = trace ? [{ id: 'observed', type: 'candidate', position: { x: 0, y: (trace.simulation.candidates.length - 1) * 68 }, data: { label: `Tick ${trace.state.payload.tick} · Queue ${trace.state.payload.queue}`, onClick: () => onSelect('') } },
    ...trace.simulation.candidates.map((c: any, i: number) => ({ id: c.fingerprint, type: 'candidate', position: { x: 220, y: i * 136 }, data: { candidate: c, selected: c.fingerprint === evidence.selected, qualification: evidence.qualifications.find(q => q.action && q.action.id === c.action.id)?.status, actual: c.fingerprint === evidence.selected ? evidence.actual : null, value: trace.scores[i], onClick: () => onSelect(c.fingerprint) } }))] : [];
  const edges: Edge[] = trace ? trace.simulation.candidates.map((c: any) => ({ id: `edge-${c.fingerprint}`, source: 'observed', target: c.fingerprint, type: 'smoothstep', style: { stroke: c.fingerprint === evidence.selected ? '#beec83' : '#54769b', strokeWidth: 2 } })) : [];
  return <div className="pg-graph" aria-label="保存された候補Actionと実行結果">
    {trace ? <ReactFlow key={trace.run_id} nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: .12 }} nodesDraggable={false} nodesConnectable={false} panOnDrag={false} zoomOnScroll={false} minZoom={.3} maxZoom={1.2} proOptions={{ hideAttribution: true }}><Background color="#28364a" gap={20}/></ReactFlow>
      : <div className="pg-empty">正式な候補比較の記録を待っています。<small>未実行の未来や成功は補いません。</small></div>}
  </div>;
}

function QueueWorld({ evidence }: { evidence: Evidence }) {
  const p = evidence.state?.payload;
  if (!p) return <div className="pg-world-empty">機械と仕事の状態を待っています。<span>Liveは「実験を始める」から開始。保存した実験は自動で読み込みます。</span></div>;
  const pending = (p.pending || []).flatMap((batch: any) => Array.from({ length: batch.amount }, (_, i) => ({ due: batch.due, slot: i })));
  const metrics = evidence.lastOutcome?.metrics;
  const arrived = evidence.lastArrived;
  const processed = metrics?.processed || 0;
  return <svg className="pg-world" viewBox="0 0 900 310" role="img" aria-label={`実測World Tick ${p.tick} 待機${p.queue} 完了${p.delivered}`}>
    <defs><pattern id="pg-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#314155" strokeWidth=".5"/></pattern></defs>
    <rect width="900" height="310" fill="url(#pg-grid)"/>
    <path d="M65 160H835" stroke="#354961" strokeWidth="3"/>
    <path d="m245 153 8 7-8 7m245-14 8 7-8 7m245-14 8 7-8 7" fill="none" stroke="#88a5c8" strokeWidth="2"/>
    <g className="pg-svg-label"><text x="125" y="42">受付済み・運搬中</text><text x="350" y="42">処理を待つ仕事</text><text x="580" y="42">処理する機械</text><text x="795" y="42">終わった仕事</text></g>
    <rect x="46" y="74" width="160" height="145" rx="14" fill="#152337" stroke="#405c7e"/>
    <rect x="272" y="74" width="160" height="145" rx="14" fill="#172232" stroke="#526278"/>
    <rect x="508" y="100" width="145" height="111" rx="18" fill="#223339" stroke="#8dbfae"/>
    <rect x="720" y="74" width="150" height="145" rx="14" fill="#223225" stroke="#5f8050"/>
    {pending.slice(0, 12).map((unit: any, i: number) => <g className="pg-unit" key={`${unit.due}-${unit.slot}`} style={{ transform: `translate(${77 + (i % 4) * 30}px, ${107 + Math.floor(i / 4) * 35}px)` }}><rect width="19" height="19" rx="4" fill="#8dbbf2"/><title>作業1単位・到着予定Tick {unit.due}</title></g>)}
    {Array.from({ length: Math.min(12, p.capacity) }, (_, i) => <rect key={i} x={294 + (i % 4) * 30} y={101 + Math.floor(i / 4) * 34} width="20" height="20" rx="4" stroke="#526278" fill={i < p.queue ? '#f0c984' : '#1a2636'} className="pg-queue-unit"/>)}
    {Array.from({ length: Math.min(12, p.delivered) }, (_, i) => <rect key={i} x={741 + (i % 4) * 28} y={101 + Math.floor(i / 4) * 34} width="18" height="18" rx="4" fill="#beec83" opacity={.45 + Math.floor(i / 4) * .2}/>)}
    <text x="580" y="146" textAnchor="middle" fill="#d6f2e7" fontSize="31">{metrics ? processed : '—'}</text>
    <text x="580" y="179" textAnchor="middle" fill="#a1baaf" fontSize="14">直近に終えた量</text>
    <g className="pg-svg-count"><text x="125" y="249">{pending.length} 単位</text><text x="350" y="249">{p.queue} / {p.capacity}</text><text x="795" y="249">累計 {p.delivered}</text></g>
    <text x="450" y="274" textAnchor="middle" fill="#9db2c9" fontSize="13">機械の今の能力は見えません。{evidence.probe ? `最後の測定：時間${evidence.probe.tick}に${evidence.probe.value}単位` : 'まだ測定していません'}</text>
    <text x="450" y="299" textAnchor="middle" fill="#8798b0" fontSize="12">四角1個 = 仕事1単位 · 受付から到着まで時間が{p.arrival_delay}進む · 1回に処理できる量の範囲は{p.service_bounds.join('〜')}単位</text>
    {evidence.lastOutcome && <g key={evidence.lastOutcome.receipt} aria-hidden="true">{Array.from({ length: Math.min(3, processed) }, (_, i) => <circle className="pg-processed-motion" key={i} cx="580" cy={126 + i * 24} r="8" fill="#beec83" style={{ animationDelay: `${i * .08}s` }}/>) }{Array.from({ length: Math.min(3, arrived) }, (_, i) => <circle className="pg-arrival-motion" key={`a${i}`} cx="126" cy={126 + i * 24} r="8" fill="#f0c984" style={{ animationDelay: `${i * .08}s` }}/>)}</g>}
  </svg>;
}

function ForecastPlot({ prediction, actual }: { prediction: any; actual: any }) {
  const vectors = prediction?.vectors;
  if (!vectors?.queue?.length) return <p>このActionには数値未来の予測がありません。Probeの測定値は実行後にだけ分かります。</p>;
  const branches = prediction.raw?.branches || [];
  const horizon = vectors.queue.length;
  const max = Math.max(1, ...branches.flatMap((b: any) => b.queue), ...vectors.queue, actual?.state.payload.queue || 0);
  const point = (value: number, i: number) => `${35 + i * 255 / Math.max(1, horizon - 1)},${120 - value / max * 95}`;
  return <div className="pg-plot"><svg viewBox="0 0 320 154" aria-label="保存予測の待機キュー分布と選択ActionのH1実測">
    {branches.map((b: any, i: number) => <polyline key={i} points={b.queue.map(point).join(' ')} fill="none" stroke="#6584ac" opacity={b.weight ? .5 : .18} strokeDasharray={b.weight ? undefined : '3 3'}/>)}
    <polyline points={vectors.queue.map(point).join(' ')} fill="none" stroke="#88bfff" strokeWidth="3"/>
    {actual && <circle cx="35" cy={120 - actual.state.payload.queue / max * 95} r="5" fill="#beec83"/>}
    {vectors.queue.map((_: number, i: number) => <text key={i} x={35 + i * 255 / Math.max(1, horizon - 1)} y="146" textAnchor="middle" fill="#96a5ba" fontSize="11">+{i + 1} tick</text>)}
    <text x="5" y="22" fill="#96a5ba" fontSize="11">{max}</text><text x="5" y="124" fill="#96a5ba" fontSize="11">0</text>
  </svg><p>青：予測平均 / 細線：保存済み数値経路 / 緑：選択ActionのH1 Actual</p><p>H{horizon}は以降の介入なし。支持範囲は較正済み安全確率ではありません。</p></div>;
}

async function jsonResponse(response: Response) {
  if (!response.ok) throw new Error((await response.text()).slice(0, 240));
  return response.json();
}

export default function Playground() {
  const [catalog, setCatalog] = React.useState<PlaygroundSession[]>([]);
  const [id, setId] = React.useState(''), [session, setSession] = React.useState<PlaygroundSession | null>(null);
  const [events, setEvents] = React.useState<PlaygroundEvent[]>([]), [artifacts, setArtifacts] = React.useState<Record<string, any>>({});
  const [mode, setMode] = React.useState<'live' | 'replay'>('replay');
  const [scenario, setScenario] = React.useState<keyof typeof scenarios>('stable_high');
  const [pipeline, setPipeline] = React.useState<'native' | 'local_llm'>('native');
  const [configurations, setConfigurations] = React.useState<any[]>([]);
  const [cursor, setCursor] = React.useState(0), [playing, setPlaying] = React.useState(false);
  const [technical, setTechnical] = React.useState(false), [moduleId, setModuleId] = React.useState<ModuleId>('perception');
  const [candidateId, setCandidateId] = React.useState(''), [error, setError] = React.useState(''), [connected, setConnected] = React.useState(true);
  const [working, setWorking] = React.useState(false), [kiosk, setKiosk] = React.useState(false);
  const firstLoad = React.useRef(true), currentId = React.useRef('');
  const visitorSelected = React.useRef(false);
  const refresh = React.useCallback(async () => {
    const result = await fetch('/api/playground/catalog').then(jsonResponse);
    setCatalog(result.sessions); setConfigurations(result.module_configurations || []);
    if (result.replay_errors.length) setError(`Replayの検証に失敗：${result.replay_errors.join(', ')}`);
    return result.sessions as PlaygroundSession[];
  }, []);
  function choose(row: PlaygroundSession) { currentId.current = row.id; firstLoad.current = true; setId(row.id); setSession(row); setEvents([]); setCursor(0); setPlaying(false); setMode(row.mode === 'live' ? 'live' : 'replay'); setScenario(row.scenario); setPipeline(row.provenance.pipeline === 'local_llm' ? 'local_llm' : 'native'); setCandidateId(''); setError(''); }
  React.useEffect(() => {
    let disposed = false;
    refresh().then(rows => { if (disposed || visitorSelected.current) return; const row = rows.find(r => r.mode === 'recorded' && r.scenario === 'stable_high' && r.provenance.audit_status === 'passed') || rows[0]; if (row) choose(row); else setMode('live'); }).catch(() => setError('APIに接続できません。localhostの起動状態を確認してください。'));
    fetch('/api/health').then(jsonResponse).then(h => { if (!disposed) setKiosk(h.kiosk); }).catch(() => {});
    return () => { disposed = true; };
  }, [refresh]);
  React.useEffect(() => {
    if (!id) return;
    let disposed = false, timer: ReturnType<typeof setTimeout>, collected: PlaygroundEvent[] = [];
    const poll = async () => {
      try {
        const last = collected.at(-1);
        const query = new URLSearchParams({ after: String(last?.index || 0), ...(last ? { reference: last.reference } : {}) });
        const data: PlaygroundSnapshot = await fetch(`/api/playground/sessions/${id}?${query}`).then(jsonResponse);
        if (disposed || currentId.current !== id) return;
        collected = data.reset ? data.events : [...collected, ...data.events];
        setSession(data.session); setEvents(collected); setArtifacts(data.artifacts); setConnected(true);
        if (firstLoad.current && collected.length) { setCursor(data.session.mode === 'recorded' ? firstWorkingCycle(collected) || decisionStart(collected) : collected.length); firstLoad.current = false; }
        if (data.session.mode === 'recorded' && terminal.has(data.session.status)) void refresh();
      } catch { if (!disposed) { setConnected(false); setPlaying(false); } }
      if (!disposed) timer = setTimeout(poll, 850);
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [id, refresh]);
  React.useEffect(() => {
    if (!playing || mode !== 'replay') return;
    if (cursor >= events.length) { setPlaying(false); return; }
    const timer = setTimeout(() => setCursor(c => technical ? Math.min(c + 1, events.length) : architectureStops(events).find(e => e.index > c)?.index || events.length), technical ? 550 : 1200);
    return () => clearTimeout(timer);
  // Polling unchanged records must not restart a visitor playback interval.
  }, [playing, mode, cursor, events.length, technical]);
  React.useEffect(() => {
    if (mode === 'live' && session?.mode === 'recorded' && terminal.has(session.status)) {
      setMode('replay'); setCursor(events.length); setPlaying(false);
    }
  }, [session?.mode, session?.status, mode, events.length]);
  const visible = mode === 'live' ? events : events.slice(0, cursor);
  const evidence = React.useMemo(() => reconstructEvidence(visible), [events, mode, cursor]);
  const cycle = React.useMemo(() => cognitiveCycle(visible), [events, mode, cursor]);
  const stops = React.useMemo(() => cycleStops(events), [events]);
  const navigationStages = mode === 'replay' ? cognitiveCycle(events.filter(e => e.event.run_id === cycle.run)).stages : cycle.stages;
  const p = evidence.state?.payload, checkpoint = evidence.checkpoint;
  const selectedModule = modules.find(m => m.id === moduleId)!;
  const source = moduleId === 'perception' ? cycle.deliberation || cycle.observed : evidence.moduleEvidence[moduleId];
  const moduleSource = moduleId === 'memory' || source?.event.run_id === cycle.run ? source : undefined;
  const summary = moduleSummary(cycle, moduleId);
  const candidate = evidence.trace?.simulation.candidates.find((c: any) => c.fingerprint === candidateId)
    || evidence.trace?.simulation.candidates.find((c: any) => c.fingerprint === evidence.selected)
    || evidence.trace?.simulation.candidates[0];
  const prediction = candidate?.records?.[0]?.prediction;
  const candidateActual = candidate?.fingerprint === evidence.selected ? evidence.actual : null;
  const live = session?.mode === 'live' && !terminal.has(session.status);
  async function command(value: 'step' | 'auto' | 'pause' | 'stop') {
    if (!session || working) return;
    setWorking(true); setError('');
    try {
      const data: PlaygroundSnapshot = await fetch(`/api/playground/sessions/${session.id}/control`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: value, revision: session.revision }) }).then(jsonResponse);
      setSession(data.session);
    } catch (e) { setError(`操作を受理できませんでした。最新状態を確認してください。 ${String(e)}`); }
    finally { setWorking(false); }
  }
  async function startLive() {
    setWorking(true); setError('');
    try {
      const result = await fetch('/api/playground/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenario, seed: 11, pipeline }) }).then(jsonResponse);
      const view: PlaygroundSnapshot = await fetch(`/api/playground/sessions/${result.id}`).then(jsonResponse);
      choose(view.session); void refresh();
    } catch (e) { setError(`開始できませんでした。 ${String(e)}`); }
    finally { setWorking(false); }
  }
  async function newSession() {
    visitorSelected.current = true;
    setPlaying(false);
    if (live) { await command('stop'); return; }
    currentId.current = ''; setId(''); setSession(null); setEvents([]); setCursor(0); setMode('live'); void refresh();
  }
  function selectLive() {
    visitorSelected.current = true;
    setPlaying(false);
    if (!live) { currentId.current = ''; setId(''); setSession(null); setEvents([]); setCursor(0); }
    setMode('live');
  }
  function changeScenario(value: keyof typeof scenarios) {
    visitorSelected.current = true;
    if (mode === 'live') { setScenario(value); return; }
    const recorded = catalog.find(row => row.scenario === value && row.provenance.audit_status === 'passed' && row.provenance.strategy === 'learned');
    if (recorded) choose(recorded);
    else setError('このシナリオの監査済み記録がありません。Liveで新しいWorldを開始できます。');
  }
  const phase = checkpoint?.phase;
  const phaseName = ({ monitor: '経験を監視中', collect: '経験蓄積・再学習準備中', await_promotion: '独立評価合格・明示昇格待ち', rejected: '候補を棄却' } as Record<string, string>)[phase] || '経験蓄積中';
  return <div className={`pg ${technical ? '' : 'pg-exhibit'}`}>
    <header className="pg-header"><a href="#playground" className="pg-brand"><GitBranch size={27}/><b>PreAct</b><span>INTERACTIVE COGNITIVE PLAYGROUND</span></a><div>{!kiosk && <a href="#"><ArrowLeft size={14}/>Future Tree</a>}<span className={connected ? 'pg-connected' : 'pg-offline'}>{connected ? '● localhost / Queue simulation' : '● 接続断・最後の実記録を表示'}</span></div></header>
    <main className="pg-main"><section className="pg-intro"><div><p className="pg-eyebrow">OSC 2026 TOKYO / FALL · INTERACTIVE WORLD MODEL</p><h1>世界を捉え、内部で未来を試し、選んで動かす。</h1><p>PreActは、世界モデルの機能をモジュール化して再現し、認知・予測・シミュレーション・意思決定・実行を統合するアーキテクチャです。</p></div><div className="pg-mode-label" data-testid="mode-label">{mode === 'live' && live ? 'LIVE · 実際のCPUシミュレーション' : mode === 'live' && !session ? 'LIVE · 開始待ち' : 'RECORDED REPLAY · 保存実行記録'}<small>{mode === 'replay' ? '閲覧操作は実行・学習・昇格を起こしません' : '環境は開始時に設定・実行中の直接編集なし'}</small></div></section>
      <section className="pg-controls" aria-label="展示操作"><div className="pg-mode-switch"><button className={mode === 'live' ? 'selected' : ''} onClick={selectLive}>Live</button><button className={mode === 'replay' ? 'selected' : ''} disabled={!events.length} onClick={async () => { if (live) await command('pause'); setMode('replay'); setCursor(events.length); }}>Recorded Replay</button></div>
        <select aria-label="シナリオ" value={scenario} disabled={!!live || (mode === 'replay' && !catalog.length)} onChange={e => changeScenario(e.target.value as keyof typeof scenarios)}>{Object.entries(scenarios).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
        <select aria-label="実装構成" value={pipeline} disabled={!!live || mode === 'replay'} onChange={e => setPipeline(e.target.value as 'native' | 'local_llm')}><option value="native">数理モデル + シミュレータ</option><option value="local_llm" disabled={!configurations.some(c => c.id === 'local_llm' && c.available)}>LLM提案 + 数理モデル + シミュレータ</option></select>
        {!technical && mode === 'replay' && <button className="pg-primary" disabled={!cycle.observed} onClick={() => { setCursor(cycle.observed!.index); setPlaying(true); }}>この循環を再生<Play size={16}/></button>}
        {(technical || mode === 'live') && <button className="pg-primary" disabled={working || (mode === 'live' && !!live) || (mode === 'replay' && !events.length)} onClick={() => { if (mode === 'live') void startLive(); else { if (cursor === events.length) setCursor(0); setPlaying(true); } }}><Play size={16}/>{technical ? 'Start' : '実験を始める'}</button>}
        <button disabled={working || (mode === 'live' && !live)} onClick={() => mode === 'live' ? void command('pause') : setPlaying(false)}><Pause size={16}/>{technical ? 'Pause' : '一時停止'}</button>
        <button className={!technical && mode === 'replay' ? 'pg-primary' : ''} disabled={working || (mode === 'live' ? !live || session?.status !== 'paused' : cursor >= events.length)} onClick={() => { setPlaying(false); if (mode === 'live') void command('step'); else setCursor(c => technical ? Math.min(c + 1, events.length) : architectureStops(events).find(e => e.index > c)?.index || events.length); }}><SkipForward size={16}/>{technical ? 'Next Step' : mode === 'live' ? '次の判断を見る' : '次の処理を見る'}</button>
        <button className={(mode === 'live' ? session?.auto : playing) ? 'selected' : ''} disabled={working || (mode === 'live' ? !live : !events.length)} onClick={() => { if (mode === 'live') void command('auto'); else { if (cursor >= events.length) setCursor(0); setPlaying(true); } }}><Play size={16}/>{technical ? 'Auto Play' : '自動で進める'}</button>
        <button disabled={working || session?.status === 'stopping'} onClick={() => void newSession()}><RotateCcw size={16}/>{technical ? live ? 'Reset / 停止' : 'New Session' : live ? '実験を止める' : '新しい実験'}</button>
        <label className="pg-technical"><input type="checkbox" checked={technical} onChange={e => setTechnical(e.target.checked)}/>技術者向け詳細</label>
      </section>
      {error && <p className="pg-error" role="alert">{error}</p>}{session?.error && <p className="pg-error" role="alert">処理を継続できませんでした：{session.error}。保存記録を確認し、{technical ? 'New Session' : '新しい実験'}でやり直してください。</p>}
      {!connected && <p className="pg-error" role="alert">接続を再試行しています。表示は最後に取得した記録です。復帰しても過去のActionは再実行しません。</p>}
      <div className="pg-session-line"><span>{session ? `${technical ? session.label : scenarios[session.scenario]} · ${!technical && session.status === 'recorded' ? '保存した実際の実験を表示' : statusNames[session.status] || session.status}` : 'シナリオを選んでStart、または保存記録を選択'}</span><span>{technical ? mode === 'live' ? 'Next Step = 1認知round（確定後に停止）' : 'Next Step = 1保存イベント' : mode === 'live' ? `次の判断で環境を実際に動かします · LLM ${configurations.some(c => c.id === 'local_llm' && c.available) ? '接続済み' : '未接続'}` : '観測は世界から内部へ。選んだ行動は内部から世界へ。'}</span></div>
      {technical && <section className="pg-stats" aria-label="実測指標">{[['TICK', p?.tick ?? '—'], ['処理済み / GOAL', p ? `${p.delivered} / ${p.target}` : '—'], ['累積 REWARD', evidence.receipts ? evidence.reward.toFixed(2) : '—'], ['OVERFLOW', !evidence.lastOutcome ? '未実行' : evidence.lastOutcome.unsafe ? '検出' : 'なし（直前実測）'], ['確定 RECEIPTS', evidence.receipts], ['経験MANIFEST収録', evidence.learned]].map(([label, value]) => <div key={label}><span>{label}</span><b>{value}</b></div>)}</section>}
      {!technical && <ArchitectureWorld cycle={cycle} state={evidence.state} active={evidence.active} moduleId={moduleId} onModule={setModuleId} candidateId={candidateId} onCandidate={setCandidateId} last={visible.at(-1)} checkpoint={checkpoint} reward={evidence.reward}/>}
      <section hidden={!technical} className="pg-cycle-nav" aria-label="一手の認知サイクル">
        <div><b>{cycle.observed ? `Tick ${cycle.observed.event.data.state.payload.tick} → ${cycle.outcome?.event.data.observation.state.payload.tick ?? '実行待ち'}` : '認知サイクル'}</b><small>同じ一手の入力・未来・実行結果をたどる</small></div>
        <div className="pg-cycle-stages">{navigationStages.map(stage => <button key={stage.id} disabled={!stage.source} aria-pressed={moduleId === stage.id} onClick={() => { setModuleId(stage.id); setCandidateId(''); if (mode === 'replay' && stage.source) { setPlaying(false); setCursor(stage.source.index); } }}>{stage.label}{stage.source && <Check size={11}/>}</button>)}</div>
        <button disabled={mode === 'live' || !stops.some(e => e.index < (cycle.observed?.index || 0))} onClick={() => { setPlaying(false); setCursor(stops.filter(e => e.index < (cycle.observed?.index || 0)).at(-1)!.index); }}><ArrowLeft size={14}/>前の一手</button>
        <button disabled={mode === 'live' || !stops.some(e => e.index > cursor)} onClick={() => { setPlaying(false); setCursor(stops.find(e => e.index > cursor)!.index); }}>次の認知サイクル<ArrowRight size={14}/></button>
      </section>
      <div hidden={!technical} className="pg-stage"><section className="pg-panel pg-world-panel"><div className="pg-panel-title"><h2>Queue World</h2><span>実際の環境 · 正式な観測と確定結果</span></div><QueueWorld evidence={evidence}/>
        <div hidden={!technical} className="pg-world-cycle" data-testid="world-cycle"><span>この一手で何が起きた？</span><strong>{actionName(cycle.selected?.action)}<ArrowRight size={17}/>{cycle.outcome ? `実処理 ${cycle.outcome.event.data.observation.metrics.processed} 単位` : 'Actual未確定'}</strong>
          <p>{cycle.observed ? `観測した待機数 ${cycle.observed.event.data.state.payload.queue}` : '観測待ち'} → {cycle.outcome ? `実行後 ${cycle.outcome.event.data.observation.state.payload.queue}` : '実行待ち'} · {cycle.update ? '再観測して次の認知へ' : '経験への反映待ち'}</p>
          <small>投入には2 tickの遅延があります。いま処理された作業と、いま投入した作業は区別します。</small>
        </div><div className="pg-world-note">環境操作はシナリオ開始時のみ。青：流入待ち / 黄：待機 / 緑：実処理。最大12単位を表示、個別IDなし。</div></section>
        <div hidden={!technical} className="pg-cognition"><CognitiveLoop cycle={cycle} active={evidence.active} selected={moduleId} onSelect={id => { setModuleId(id); setCandidateId(''); }}/>
          <section className="pg-module-detail" aria-live="polite"><b>{selectedModule.title}</b><p><span>入力</span>{summary.input}</p><p><span>出力</span>{summary.output}</p><small>{summary.detail}</small></section>
        </div>
      </div>
      {!technical && session?.scenario === 'low_to_high' && session.provenance.audit_status === 'passed' && <p className="pg-adverse">保存済みの比較実験では、モデル回復を行った方が結果が悪くなりました。経験から学ぶことが、必ず良い判断につながるわけではありません。数値は技術者向け詳細で確認できます。</p>}
      <div hidden={!technical} className="pg-future-stage"><section className="pg-panel"><div className="pg-panel-title"><h2>Counterfactual Futures</h2><span>{evidence.trace ? `同じ観測 Tick ${evidence.trace.state.payload.tick} から候補を比較` : '正式な候補記録待ち'}</span></div><Futures evidence={evidence} onSelect={value => { setCandidateId(value); setModuleId('decision'); }}/><div className="pg-world-note">候補は共通の観測・モデルから計算。数値予測 ≠ 仮想State。未選択候補のActualなし。</div></section>
        <section className="pg-panel pg-comparison" aria-label="予測と実際の対応"><div className="pg-panel-title"><h2>Future → Decision → Actual</h2><span>候補をクリックして比較</span></div><div className="pg-comparison-body"><h3>{actionName(candidate?.action)} <small>{candidateActual ? '選択・実行済み' : '候補 / 実測なし'}</small></h3><ForecastPlot prediction={prediction} actual={candidateActual}/>
          <table><thead><tr><th>次Tickの指標</th><th>予測 H1</th><th>Actual</th></tr></thead><tbody>{(['queue', 'delivered'] as const).map(key => <tr key={key}><th>{key === 'queue' ? '待機数' : '累積処理数'}</th><td>{prediction?.vectors?.[key]?.[0]?.toFixed(2) ?? '予測なし'}</td><td>{candidateActual?.state.payload[key] ?? '未確定 / 未選択'}</td></tr>)}</tbody></table><p>{candidate?.action.kind === 'probe_service' ? 'Probeの価値は既知の測定費用です。実行前に測定結果や情報利得を補いません。' : '判断の価値 = 予測したGoal進捗 − 待機・投入の費用。価値順位と最終Gateの結果は別々に確認できます。'}</p></div></section></div>
      <section hidden={!technical} className="pg-timeline" aria-label="イベントReplay"><div><b>{mode === 'replay' ? 'RECORDED REPLAY' : 'LIVE EVENT HISTORY'}</b><span>{mode === 'live' ? events.length : cursor} / {events.length} イベント</span></div><input type="range" aria-label="記録の再生位置" min="0" max={events.length} value={mode === 'live' ? events.length : cursor} disabled={mode === 'live'} onChange={e => { setPlaying(false); setCursor(Number(e.target.value)); }}/><button disabled={mode === 'live'} onClick={() => { setPlaying(false); setCursor(0); }}>先頭</button><button disabled={mode === 'live'} onClick={() => { setPlaying(false); setCursor(decisionStart(events)); }}>候補比較へ</button><select aria-label="記録roundへ移動" disabled={mode === 'live'} value="" onChange={e => { setPlaying(false); setCursor(Number(e.target.value)); }}><option value="">roundへ移動</option>{events.filter(e => e.event.kind === 'observed' && e.scope === 'world').map(e => <option key={e.reference} value={e.index}>Tick {e.event.data.state.payload.tick}</option>)}</select></section>
      <div hidden={!technical} className="pg-inspection"><section className="pg-panel pg-learning"><p className="pg-eyebrow">EXPERIENCE & LEARNING</p><h2>{phaseName}</h2><div className="pg-learning-status"><span>Health <b>{checkpoint?.status === 'await_checkpoint' ? '新世代の評価待ち' : checkpoint?.status || '未評価'}</b></span><span>世代 <b>{checkpoint?.generation ?? '—'}</b></span><span>回復標本 <b>{checkpoint?.effective_training ?? '—'}</b></span></div><p>Model <code>{checkpoint?.model_version?.slice(0, 16) || 'まだ記録なし'}</code></p><p>選択ActionのH1誤差 {evidence.h1Error == null ? '—' : evidence.h1Error.toFixed(3)} <small>· queue / delivered平均絶対誤差</small></p><p>予測対象 {evidence.matched} / 全実行 {evidence.receipts}（Probeも分母に含む）</p><p className="pg-honest">評価合格だけではモデルは変わりません。公開Liveに昇格操作はありません。Goal999は継続実行fixtureで、完了の実証ではありません。</p>{session?.scenario === 'low_to_high' && <p className="pg-adverse">凍結比較では回復ありReward 32.9、回復なし115.4。能力向上時に悪化した結果も保持しています。</p>}{technical && <details><summary>評価・候補・Promotionの保存証拠</summary><pre>{JSON.stringify(Object.fromEntries(['candidate', 'evaluation', 'promotion'].filter(k => checkpoint?.[k]).map(k => [k, { digest: checkpoint[k], record: artifacts[checkpoint[k]] || 'この記録では詳細なし' }])), null, 2)}</pre></details>}</section>
        <section className="pg-panel pg-inspector"><p className="pg-eyebrow">MODULE / CANDIDATE INSPECTOR</p><h2>{selectedModule.english}</h2><p>{selectedModule.description}</p><p><b>入力</b> · {summary.input}<br/><b>出力</b> · {summary.output}</p><p className="pg-implementation">{selectedModule.implementation}</p>{(moduleId === 'simulation' || moduleId === 'decision') && <ModuleImplementation trace={cycle.trace}/>}{candidate && (moduleId === 'decision' || moduleId === 'simulation' || moduleId === 'prediction') && <><h3>{candidate.action.name} <small>{candidate.fingerprint === evidence.selected ? '選択済み' : '候補'}</small></h3><p>{candidate.basis === 'fixed_prior_fallback' ? '固定事前によるfallback予測です。失効した経験モデルの予測ではありません。' : candidate.action.kind === 'probe_service' ? '既存の観測準備方策です。値は既知のProbe費用で、測定結果の予測ではありません。' : '保存された予測と価値評価。最終Gate・Actualは別の証拠です。'}</p><ForecastPlot prediction={prediction} actual={candidateActual}/></>}{moduleSource ? <><p className="pg-evidence-ref">出典 <code>{moduleSource.reference}</code> · {moduleSource.event.kind}</p>{technical && <details open><summary>実入力・出力と関連ID</summary><pre>{JSON.stringify({ implementation: selectedModule.implementation, reference: moduleSource.reference, kind: moduleSource.event.kind, recorded_timestamp: moduleSource.event.timestamp, data: moduleSource.event.data }, null, 2)}</pre></details>}</> : <p>この時点には対応する処理記録がありません。</p>}{technical && candidate && <details><summary>Action / Prediction / Gate / Actual</summary><pre>{JSON.stringify({ action: candidate.action, fingerprint: candidate.fingerprint, basis: candidate.basis, value_evaluation: evidence.trace?.scores[evidence.trace.simulation.candidates.indexOf(candidate)], ranked_fingerprints: evidence.trace?.ranked_fingerprints, prediction, qualification: evidence.qualifications.find(q => q.action.id === candidate.action.id), authorization: candidate.fingerprint === evidence.selected ? evidence.authorization : null, actual: candidateActual }, null, 2)}</pre></details>}</section></div>
      <section className="pg-library"><div className="pg-panel-title"><h2>保存済み実行記録を探索</h2><button onClick={() => void refresh()}><RotateCcw size={13}/>更新</button></div><p>{technical ? '「前の一手」「次の認知サイクル」で移動し、各段階をたどれます。「先頭」で準備後の全イベントを探索できます。' : '保存した実際の実験を選択。「この循環を再生」で観測から実行・経験まで、「次の処理を見る」で段階ごとにたどれます。'}</p>{catalog.length ? <div className="pg-recordings">{catalog.map(row => <button key={row.id} className={row.id === id ? 'selected' : ''} disabled={working || (!!live && row.id !== id)} onClick={() => choose(row)}><span>{row.mode === 'live' ? 'LIVE SESSION' : row.provenance.audit_status === 'passed' ? 'AUDITED REPLAY' : 'LOCAL RECORDED HISTORY'}</span><strong>{row.label}</strong><small>{row.provenance.pipeline === 'local_llm' ? 'LLM + 数値モデル' : '数値モデル'} · {String(row.provenance.strategy || statusNames[row.status])} · {row.rounds} rounds</small><ArrowRight size={17}/></button>)}</div> : <p>監査済みReplayが未準備です。運用手順の準備コマンドを実行してください。</p>}</section>
      {technical && session && <details className="pg-provenance"><summary>Session provenance / 準備コスト / 監査情報</summary><pre>{JSON.stringify(session.provenance, null, 2)}</pre></details>}
    </main><footer className="pg-footer"><span>PreAct：見る → 予想する → 試す → 選ぶ → 動かす → 結果を次へ戻す</span><span>詳しい実装・記録は「技術者向け詳細」で確認できます</span></footer>
  </div>;
}
