import { ArrowRight, Check } from 'lucide-react';
import type { CognitiveCycle } from './cycle';
import type { ModuleId } from './evidence';

const format = (value: number) => Number(value.toFixed(2)).toString();
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

export const visitorModules: { id: ModuleId; name: string; role: string }[] = [
  { id: 'perception', name: '見る', role: '認知' },
  { id: 'prediction', name: '先を予想する', role: '予測' },
  { id: 'simulation', name: 'もしもを試す', role: 'シミュレーション' },
  { id: 'decision', name: '一つを選ぶ', role: '意思決定' },
  { id: 'execution', name: '実際に動かす', role: '実行' },
  { id: 'memory', name: '結果を覚える', role: '経験・学習' },
];

export function VisitorModules({ selected, active, onSelect }: { selected: ModuleId; active: ModuleId[]; onSelect: (id: ModuleId) => void }) {
  return <section className="pg-visitor-modules" aria-label="PreActが分担する6つの働き">{visitorModules.map((m, i) => <button key={m.id} aria-pressed={selected === m.id} className={active.includes(m.id) ? 'active' : ''} onClick={() => onSelect(m.id)}><span>{i + 1}</span><strong>{m.name}</strong><small>{m.role}</small>{i < 5 && <ArrowRight size={16}/>}</button>)}</section>;
}

export default function VisitorStory({ cycle, candidateId, onCandidate, moduleId }: {
  cycle: CognitiveCycle; candidateId: string; onCandidate: (id: string) => void; moduleId: ModuleId;
}) {
  const before = cycle.observed?.event.data.state.payload;
  const actual = cycle.outcome?.event.data.observation;
  const options = visitorOptions(cycle);
  const shown = options.find((o: any) => o.candidate.fingerprint === candidateId) || options.find((o: any) => o.selected) || options[0];
  const max = Math.max(1, ...options.map((o: any) => o.completed ?? 0));
  const hasForecast = options.some((o: any) => o.known);
  const actualForShown = shown?.selected ? actual : null;
  const explanations: Record<ModuleId, string> = {
    perception: '機械につながる環境から、待っている仕事や終えた量を読み取ります。機械の今の能力は直接見えません。',
    prediction: '過去の実行結果から機械の能力を見積もり、それぞれの受付量でどうなりそうかを計算します。',
    simulation: '同じ観測から「多く受け付けたら？」「少なくしたら？」「待ったら？」を試算します。ここでは現実の仕事は動きません。',
    decision: '予想される仕事の完了量と、待たせる費用・受付の費用を比べ、実行できる候補から一つを選びます。',
    execution: '選んだ行動を検証し、許可された一手だけを環境で実行します。この後で初めて実際の結果が分かります。',
    memory: '実際に起きた結果だけを経験として記録します。次の判断でその記録を参照し、モデルの更新は別に評価・承認します。',
  };
  return <section className="pg-visitor-story" aria-label="AIは何を考えたか">
    <div className="pg-visitor-explanation"><span>{visitorModules.find(m => m.id === moduleId)!.name}とは？</span><p>{explanations[moduleId]}</p></div>
    <div className="pg-story-observation"><b>判断前に分かっていたこと</b><p>{before ? `処理を待つ仕事は${before.queue}単位。到着前の仕事は${before.pending.reduce((n: number, b: any) => n + b.amount, 0)}単位。` : '実験を開始すると、環境を観測します。'}</p><small>機械がいま何単位処理できるかは、AIには見えていません。</small></div>
    <div className="pg-story-futures"><h3>どれを受け付けると、どうなりそう？</h3>
      {hasForecast ? <p>このあと追加の受付をせず、<b>{cycle.trace?.simulation.horizon}回</b>時間を進めた場合の予想です。</p> : <p>{options.some((o: any) => o.candidate.action.kind === 'probe_service') ? 'いまは能力を確かめる段階です。測定結果は、実行するまで分かりません。' : 'この時点には、比較できる予想がまだありません。'}</p>}
      <div className="pg-visitor-options">{options.map((option: any) => <button key={option.candidate.fingerprint} aria-pressed={shown === option} onClick={() => onCandidate(option.candidate.fingerprint)} className={option.selected ? 'chosen' : ''}>
        <strong>{visitorAction(option.candidate.action)}</strong>
        {option.known ? <><span>終えられる仕事 <b>{format(option.completed)} 単位</b><small>予想の平均</small></span><i className="pg-forecast-bar"><i style={{ width: `${option.completed / max * 100}%` }}/></i><small>その時に残る待ち仕事：{format(option.waiting)} 単位</small></> : <span>数値の予想はありません</span>}
        <em>{option.selected ? <><Check size={13}/>AIが選んだ案</> : option.qualified === 'rejected' ? '検証で実行不可' : '比較した案・未実行'}</em>
      </button>)}</div>
      {hasForecast && <p className="pg-visitor-model">{cycle.model.basis === 'fixed_prior_fallback' ? '経験モデルが使えず、設定済みの仮定で比べています。' : '過去の実行結果で学んだ、機械の能力の見積もりを使っています。'} 能力が同じ傾向で続くと仮定した予想なので、環境が変わると外れることがあります。</p>}
    </div>
    <div className="pg-story-result"><span>AIが選んだこと → 実際に起きたこと</span><h3>{visitorAction(cycle.selected?.action)}</h3>
      {actual ? <p><b>{actual.metrics.processed}単位</b>の仕事が終わり、<b>{actual.state.payload.queue}単位</b>が処理待ちになりました。</p> : <p>この一手の実行結果は、まだ確定していません。</p>}
      {actual?.unsafe && <p className="pg-error" role="alert">安全でない結果が観測されました。技術者向け詳細で実行記録を確認してください。</p>}
      {shown?.known && <p className="pg-visitor-match">この案の予想と実際：<strong>次の1回に終わる量　予想 {format(shown.nextCompleted)} 単位 ／ 実際 {actualForShown ? `${actualForShown.metrics.processed} 単位` : shown.selected ? '結果待ち' : '未実行'}</strong></p>}
      {actual && cycle.selected?.action.kind === 'submit' && cycle.selected.action.payload.amount > 0 && <small>新しく受け付けた仕事の到着は{before.arrival_delay}回後。今回終わったのは、前に受け付けて到着した仕事です。</small>}
      {actual?.metrics.measured_service != null && <small>今回の測定値：時間{actual.metrics.measurement_tick}では{actual.metrics.measured_service}単位を処理できる能力がありました。次の時間の能力は未観測です。</small>}
    </div>
    <div className="pg-story-memory"><b>結果を覚えて、次の判断へ ↻</b><p>{cycle.update && actual ? '今回の実行結果を、次に参照できる経験として記録しました。' : '結果が確定してから、経験として記録します。'}{cycle.feedback.length > 0 ? `今回の判断でも、過去の確定結果${cycle.feedback.length}件を参照しています。` : ''}</p><small>経験を記録するだけで、モデルが自動で更新されるわけではありません。</small></div>
  </section>;
}
