import { ArrowRight, RotateCcw } from 'lucide-react';
import { modules, type ModuleId } from './evidence';
import type { CognitiveCycle } from './cycle';

const number = (n: unknown) => typeof n === 'number' ? Number(n.toFixed(3)).toString() : '—';
const shorten = (s?: string) => s ? s.slice(0, 12) : '記録待ち';
export function actionName(action: any) {
  if (!action) return '選択待ち';
  return action.kind === 'probe_service' ? '能力を測定する' : action.payload.amount ? `${action.payload.amount} 単位を投入` : '投入せず処理を待つ';
}

export function moduleSummary(cycle: CognitiveCycle, id: ModuleId) {
  const { trace, selected, model, outcome, update } = cycle;
  const state = cycle.observed?.event.data.state.payload;
  const candidates = trace?.simulation.candidates || [];
  const actual = outcome?.event.data.observation;
  const score = selected && trace?.scores[candidates.indexOf(selected)];
  const facts: Record<ModuleId, { input: string; output: string; detail: string }> = {
    perception: {
      input: state ? `正式な観測 Tick ${state.tick} · 確定経験 ${cycle.feedback.length} 件を参照` : 'Worldの正式な観測待ち',
      output: state ? `待機 ${state.queue} · 流入待ち ${state.pending.reduce((n: number, b: any) => n + b.amount, 0)} · 完了 ${state.delivered}` : 'Belief未記録',
      detail: '現在・将来の能力は観測されていない。未選択Actionの結果も不明。',
    },
    prediction: {
      input: model.pHigh == null ? '観測と現在のモデルbinding' : `観測 × ${model.basis === 'fixed_prior_fallback' ? '固定事前' : '経験由来'}の能力分布 P(3) = ${number(model.pHigh)}`,
      output: candidates.length ? `${candidates.filter((c: any) => c.records?.length).length} 候補に予測記録` : '予測記録待ち',
      detail: model.pHigh == null ? 'この記録に数値未来はありません。測定準備やunknownを予測値で補いません。' : `使用モデル ${shorten(model.version)} · ${model.basis === 'fixed_prior_fallback' ? '固定事前fallback' : '経験由来の推定'}`,
    },
    simulation: {
      input: trace ? `候補Action ${candidates.length} 件 × 保存済み能力仮説` : '候補と予測の記録待ち',
      output: model.prediction ? `H${trace?.simulation.horizon} 累計完了予測: ${candidates.map((c: any) => `${c.action.payload.amount}投入→${number(c.records?.[0]?.prediction.vectors?.delivered?.at(-1))}`).join(' / ')}` : '数値未来未記録',
      detail: `${candidates.reduce((n: number, c: any) => n + (c.paths?.length || 0), 0)} 保存済み数値経路。到着規則は既知。H3は追加介入なし。仮想Stateは作らない。`,
    },
    decision: {
      input: trace ? `Goal: ${trace.goals.map((g: any) => `${g.metric} ≥ ${g.target}`).join(', ')} · 価値比較と候補検証` : 'Goal・候補の記録待ち',
      output: actionName(selected?.action),
      detail: selected ? `選択候補の価値 ${number(score?.value)} · 最終実行許可は別の証拠` : '価値順位だけでは選択・実行済みにならない。',
    },
    execution: {
      input: cycle.authorization ? `最終Gate: ${cycle.authorization.event.data.decision} · ${cycle.intent ? 'Intent保存済み' : 'Intent待ち'}` : '最終Gate・Authorization待ち',
      output: actual ? `Tick ${actual.state.payload.tick} · 実処理 ${actual.metrics.processed} · Reward ${number(actual.metrics.reward)}` : 'Actual未確定',
      detail: outcome ? `確定Receipt ${shorten(actual.receipt)}` : '確定Receiptが得られるまでActualは表示しない。',
    },
    memory: {
      input: outcome ? 'この一手の確定Receiptと正式な再観測' : cycle.feedback.length ? `過去の確定Receipt ${cycle.feedback.length} 件` : '確定実行の記録待ち',
      output: update ? `次のBelief Tick ${update.event.data.belief.observed.payload.tick} · 経験索引 ${update.event.data.memory_run_ids.length} round` : cycle.feedback.length ? `確定経験 ${cycle.feedback.length} 件を今回の認知へ参照` : '次の認知への反映待ち',
      detail: cycle.experience && outcome ? '学習Coordinatorの経験manifestへ反映。モデル変更には評価と明示昇格が必要。' : '経験の追加とモデルversionの更新は別の処理。',
    },
  };
  return facts[id];
}

export default function CognitiveLoop({ cycle, active, selected, onSelect }: {
  cycle: CognitiveCycle; active: ModuleId[]; selected: ModuleId; onSelect: (id: ModuleId) => void;
}) {
  const sources = {
    perception: cycle.observed,
    prediction: cycle.trace?.simulation.candidates.some((c: any) => c.records?.length) ? cycle.deliberation : undefined,
    simulation: cycle.trace?.simulation.candidates.some((c: any) => c.paths?.length) ? cycle.deliberation : undefined,
    decision: cycle.selection || cycle.deliberation,
    execution: cycle.outcome || cycle.intent || cycle.authorization,
    memory: cycle.update || (cycle.feedback.length ? cycle.deliberation : undefined),
  };
  const links = [
    { id: 'observe-predict', path: 'M329 50H344', source: sources.prediction },
    { id: 'predict-simulate', path: 'M657 50H672', source: sources.simulation },
    { id: 'simulate-decide', path: 'M984 50H994V146H984', source: cycle.selection },
    { id: 'decide-execute', path: 'M672 146H657', source: sources.execution },
    { id: 'execute-remember', path: 'M344 146H329', source: cycle.outcome && cycle.update },
    { id: 'remember-observe', path: 'M16 146H6V50H16', source: cycle.feedback.length ? cycle.deliberation : undefined },
  ];
  return <section className="pg-panel pg-loop" aria-label="認知アーキテクチャ">
    <div className="pg-panel-title"><h2>PreAct · 世界モデルの機能をつなぐ</h2><span>{cycle.observed ? `観測 Tick ${cycle.observed.event.data.state.payload.tick} の一手` : '正式な観測待ち'}</span></div>
    <div className="pg-loop-canvas">
      <svg className="pg-loop-wires" viewBox="0 0 1000 196" preserveAspectRatio="none" aria-label="実記録に対応するデータフロー">
        <defs><marker id="pg-loop-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10" fill="currentColor"/></marker></defs>
        {links.map(link => <path key={link.id} data-testid={`flow-${link.id}`} data-reference={link.source?.reference || ''} className={link.source ? 'recorded' : ''} d={link.path} markerEnd="url(#pg-loop-arrow)"/>)}
      </svg>
      <div className="pg-loop-modules">{modules.map(m => {
        const id = m.id;
        const facts = moduleSummary(cycle, id), source = sources[id];
        return <button key={id} style={{ gridArea: id }} data-testid={`module-${id}`} aria-pressed={selected === id} className={`pg-module ${active.includes(id) ? 'active' : ''} ${selected === id ? 'inspected' : ''}`} onClick={() => onSelect(id)}>
          <small>{m.english}</small><strong>{m.title}</strong><b>{facts.output}</b><em>{source ? `${source.event.kind} · #${source.event.seq}` : 'このroundの処理記録待ち'}</em>
        </button>;
      })}</div>
    </div>
    <div className="pg-internal-model" data-testid="internal-model">
      <div><span>内部環境モデルの表現</span><strong>観測とBelief ＋ 既知の到着規則 ＋ 能力の仮説分布</strong><small>経験由来の推定、または明示された固定事前値で、この一手の未来を構成します。</small></div>
      <div className="pg-model-parameter"><span>{cycle.model.basis === 'fixed_prior_fallback' ? '固定事前による能力仮説' : 'この予測に使った能力分布'}</span>
        <b>{cycle.model.pHigh == null ? '数値予測なし' : `P(service = 3) = ${number(cycle.model.pHigh)}`}</b>
        {cycle.model.pHigh != null && <div className="pg-probability" role="img" aria-label={`保存された高能力確率 ${cycle.model.pHigh}`}><i style={{ width: `${cycle.model.pHigh * 100}%` }}/></div>}
        <small>{cycle.model.statistics?.informative_count != null ? `識別可能な学習標本 ${cycle.model.statistics.informative_count} · ` : ''}Health {cycle.model.health?.status || '記録待ち'}</small>
        {cycle.model.interval && cycle.model.statistics?.informative_count > 0 && <small>Wilson 95%区間 {cycle.model.interval.map(number).join('–')} · iid・定常性の仮定</small>}
      </div>
    </div>
    <p className="pg-feedback"><RotateCcw size={14}/><span data-testid="feedback-link">{cycle.feedback.length ? `過去の確定Receipt ${cycle.feedback.length} 件 → このroundのBelief・予測へ参照` : '次のroundは確定経験を再取得して認知する'}</span><ArrowRight size={14}/><span>モデル変更は明示Promotionのみ</span></p>
  </section>;
}
