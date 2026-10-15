import type { PlaygroundEvent as StoredEvent } from '../playground-contracts';
import type { State, Observation } from '../contracts';

// Domain presentation narrows the intentionally generic serialized payloads.
export type PlaygroundEvent = Omit<StoredEvent, 'event'> & { event: Omit<StoredEvent['event'], 'data'> & { data: Record<string, any> } };
type QueueState = State & { payload: Record<string, any> };
type QueueObservation = Observation & { state: QueueState };

export const modules = [
  { id: 'perception', title: '認知・観測', english: 'Cognition / Perception', implementation: 'CognitiveAgent · WorldPlanner · Belief', description: '正式な観測と確定経験から、いま分かることと不明なことを整理します。' },
  { id: 'prediction', title: '予測', english: 'Prediction', implementation: 'QueuePredictionAdapter · QueueTemporalEngine', description: '候補Actionごとに、観測経験に基づく予測を保存します。能力の将来値は非公開です。' },
  { id: 'simulation', title: '複数の未来', english: 'Simulation', implementation: 'preact.engines.queue_temporal.paths · QueueTemporalEngine', description: '保存された能力仮説の経路を比較します。数値経路は完全な仮想Stateではありません。' },
  { id: 'decision', title: '意思決定', english: 'Decision / Planning', implementation: 'DeliberationPlanner · CandidateSelector', description: '候補の価値を比較し、Runtimeが検証した候補集合から選択します。' },
  { id: 'execution', title: '検証・実行', english: 'Verification & Execution', implementation: 'Runtime · InformationBoundVerifier · SelectionWorld', description: '独立検証、最終Gate、Authorization、Intentを経て、一手だけ実行します。' },
  { id: 'memory', title: '経験・学習', english: 'Memory & Learning', implementation: 'EpisodicMemory · TransitionDataset · LearningCoordinator · QueueLearningBackend', description: '確定Receiptだけを経験にします。評価合格とモデルの明示昇格は別です。' },
] as const;
export type ModuleId = typeof modules[number]['id'];

export function modulesFor(item: PlaygroundEvent): ModuleId[] {
  const { kind, data } = item.event;
  if (kind === 'observed') return ['perception'];
  if (kind === 'deliberation') {
    const candidates = data.simulation?.candidates || [];
    return ['perception', ...(candidates.some((c: any) => c.records?.length) ? ['prediction' as const] : []), ...(candidates.some((c: any) => c.paths?.length) ? ['simulation' as const] : []), 'decision', ...(data.memory_references?.length ? ['memory' as const] : [])];
  }
  if (kind === 'candidate_selection') return ['decision'];
  if (kind === 'outcome') return ['execution', 'perception'];
  if (kind === 'cognitive_update') return ['perception', 'memory'];
  if (kind.startsWith('learning_') || kind.startsWith('continuous_')) return ['memory'];
  if (['candidate_qualification', 'gate_preview', 'authorization', 'decision', 'execution_intent', 'cognitive_multi_dispatch'].includes(kind)) return ['execution'];
  return [];
}

export function reconstructEvidence(events: PlaygroundEvent[]) {
  let state: QueueState | null = null, trace: any = null, actual: QueueObservation | null = null;
  let lastOutcome: QueueObservation | null = null, lastArrived = 0;
  let selected: string | null = null, checkpoint: any = null, probe: { value: number; tick: number } | null = null;
  let qualifications: any[] = [], authorization: any = null, learned = 0;
  const receipts = new Set<string>();
  const confirmedRuns = new Map<string, Set<string>>();
  const moduleEvidence: Partial<Record<ModuleId, PlaygroundEvent>> = {};
  let matched = 0, totalError = 0;
  for (const item of events) {
    const e = item.event, data = e.data;
    for (const module of modulesFor(item)) moduleEvidence[module] = item;
    if (item.scope === 'world' && e.kind === 'observed') {
      state = data.state; trace = null; actual = null; selected = null;
      qualifications = []; authorization = null;
    }
    if (e.kind === 'deliberation') trace = data;
    if (e.kind === 'candidate_qualification') qualifications = data.batch?.candidates || [];
    if (e.kind === 'candidate_selection') selected = data.action_fingerprint;
    if (e.kind === 'authorization') authorization = data;
    if (e.kind === 'outcome' && item.receipt_verified) {
      actual = data.observation; state = actual!.state;
      lastOutcome = actual;
      lastArrived = trace ? Math.max(0, actual!.metrics.available_work - trace.state.payload.queue) : 0;
      const first = !receipts.has(actual!.receipt);
      receipts.add(actual!.receipt);
      const runReceipts = confirmedRuns.get(e.run_id) || new Set<string>();
      runReceipts.add(actual!.receipt); confirmedRuns.set(e.run_id, runReceipts);
      if (actual!.metrics.measured_service != null) probe = { value: actual!.metrics.measured_service, tick: actual!.metrics.measurement_tick };
      const candidate = trace?.simulation.candidates.find((c: any) => c.fingerprint === selected);
      const p = candidate?.records?.[0]?.prediction;
      if (first && p?.vectors?.queue?.length && p?.vectors?.delivered?.length) {
        matched++;
        totalError += (Math.abs(p.vectors.queue[0] - actual!.state.payload.queue) + Math.abs(p.vectors.delivered[0] - actual!.state.payload.delivered)) / 2;
      }
    }
    if (e.kind === 'learning_checkpoint') checkpoint = data.backend;
    if (e.kind === 'learning_explicit_promotion' && data.backend?.epoch) checkpoint = { ...checkpoint, ...data.backend.epoch, phase: data.backend.phase, status: 'await_checkpoint', promotion: data.backend.epoch.promotion_artifact };
    if (e.kind === 'learning_experience') learned = (data.runs as string[]).reduce((n, run) => n + (confirmedRuns.get(run)?.size || 0), 0);
  }
  const reward = events.filter(e => e.event.kind === 'outcome' && e.receipt_verified)
    .reduce((sum, e) => sum + (e.event.data.observation.metrics.reward ?? 0), 0);
  return { state, trace, actual, lastOutcome, lastArrived, selected, checkpoint, probe, qualifications, authorization,
    receipts: receipts.size, learned, reward, matched, h1Error: matched ? totalError / matched : null,
    moduleEvidence, active: events.length ? modulesFor(events[events.length - 1]) : [] };
}

export function decisionStart(events: PlaygroundEvent[]) {
  const task = events.findIndex(e => e.event.kind === 'deliberation' && e.event.data.simulation.candidates.length > 1);
  if (task < 0) return 0;
  const run = events[task].event.run_id;
  const observed = events.findIndex(e => e.event.run_id === run && e.event.kind === 'observed');
  return observed < 0 ? task : observed + 1;
}
