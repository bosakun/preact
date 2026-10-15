import type { ModuleId, PlaygroundEvent } from './evidence';

const latest = (events: PlaygroundEvent[], predicate: (e: PlaygroundEvent) => boolean) => [...events].reverse().find(predicate);

// A read-only view of ONE recorded cognitive round, never an inference engine.
export function cognitiveCycle(events: PlaygroundEvent[]) {
  const observed = latest(events, e => e.scope === 'world' && e.event.kind === 'observed');
  const run = observed?.event.run_id;
  const round = run ? events.filter(e => e.scope === 'world' && e.event.run_id === run) : [];
  const find = (kind: string) => latest(round, e => e.event.kind === kind);
  const deliberation = find('deliberation');
  const trace = deliberation?.event.data;
  const selection = find('candidate_selection');
  const selected = trace?.simulation.candidates.find((c: any) => c.fingerprint === selection?.event.data.action_fingerprint);
  const outcome = latest(round, e => e.event.kind === 'outcome' && e.receipt_verified);
  const update = find('cognitive_update');
  const experience = run ? latest(events, e => e.event.kind === 'learning_experience' && e.event.data.runs?.includes(run)) : undefined;
  const binding = find('learning_binding')?.event.data.provenance;
  const prediction = trace?.simulation.candidates.flatMap((c: any) => c.records || []).map((r: any) => r.prediction).find((p: any) => p.raw?.status === 'estimated' && p.raw.p_high != null && p.vectors?.queue?.length);
  const raw = prediction?.raw;
  const health = trace?.simulation.health || binding?.health;
  // The parameter is only shown when the saved forecast actually used it. Binding a
  // model or collecting another receipt does not imply a new parameter or promotion.
  const model = {
    version: raw?.base_model_version || raw?.model_version || health?.model_version || binding?.model_version,
    pHigh: raw?.p_high ?? null,
    interval: raw?.p_high_interval,
    statistics: raw?.statistics,
    basis: trace?.simulation.candidates.find((c: any) => c.records?.some((r: any) => r.prediction.id === prediction?.id))?.basis,
    health, prediction,
  };
  const referenced = new Set<string>(trace?.memory_references || []);
  const feedback = events.filter(e => e.scope === 'world' && e.receipt_verified && e.event.kind === 'outcome' && e.event.run_id !== run && e.index < (observed?.index ?? 0) && referenced.has(e.reference));
  const stages: { id: ModuleId; label: string; source?: PlaygroundEvent }[] = [
    { id: 'perception', label: '観測', source: observed },
    { id: 'prediction', label: '予測・比較', source: deliberation },
    { id: 'decision', label: '選択', source: selection },
    { id: 'execution', label: '実行結果', source: outcome },
    { id: 'memory', label: '経験へ', source: update },
  ];
  return { run, observed, trace, deliberation, selected, selection, outcome, update, experience,
    belief: trace?.belief, model, feedback, stages, authorization: find('authorization'),
    intent: find('execution_intent'), qualification: find('candidate_qualification') };
}
export type CognitiveCycle = ReturnType<typeof cognitiveCycle>;

export function cycleStops(events: PlaygroundEvent[]) {
  return events.filter(e => e.scope === 'world' && e.event.kind === 'cognitive_update');
}

export function firstWorkingCycle(events: PlaygroundEvent[]) {
  const outcome = events.find(e => e.scope === 'world' && e.receipt_verified && e.event.kind === 'outcome' && e.event.data.observation.metrics.processed > 0);
  const update = outcome && events.find(e => e.event.run_id === outcome.event.run_id && e.event.kind === 'cognitive_update');
  return update?.index || 0;
}
