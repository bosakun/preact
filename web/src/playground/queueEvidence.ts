import type { CognitiveCycle } from './cycle';

export function visitorAction(action: any) {
  if (!action) return 'まだ選んでいません';
  if (action.kind === 'probe_service') return '仕事を受け付けず、機械の能力を測る';
  return action.payload.amount === 0 ? '新しい受付をせず、待つ' : `仕事を${action.payload.amount}単位、受け付ける`;
}

// Translate saved metrics into quantities a visitor can compare. These differences
// are presentation arithmetic, not a forecast, a successor State or a new score.
export function visitorOptions(cycle: CognitiveCycle) {
  const before = cycle.observed?.event.data.state.payload;
  return (cycle.trace?.simulation.candidates || []).map((candidate: any) => {
    const prediction = candidate.records?.[0]?.prediction;
    const known = prediction?.raw?.status === 'estimated' && prediction.vectors?.delivered?.length && prediction.vectors?.queue?.length;
    return {
      candidate, prediction, known: !!known,
      completed: known ? prediction.vectors.delivered.at(-1) - before.delivered : null,
      waiting: known ? prediction.vectors.queue.at(-1) : null,
      nextCompleted: known ? prediction.vectors.delivered[0] - before.delivered : null,
      selected: candidate.fingerprint === cycle.selected?.fingerprint,
      qualified: cycle.qualification?.event.data.batch.candidates.find((c: any) => c.action.id === candidate.action.id)?.status,
    };
  });
}
