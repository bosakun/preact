import { ArrowDown, ArrowLeft, ArrowRight, Box, Check, Cpu, Database, Eye, GitBranch, Workflow } from 'lucide-react';
import type { CognitiveCycle } from './cycle';
import { modules, type ModuleId, type PlaygroundEvent } from './evidence';
import { visitorAction, visitorOptions } from './queueEvidence';

const fmt = (n: number) => Number(n.toFixed(2)).toString();
const short = (s?: string) => s ? s.slice(0, 10) : '未記録';
type Facts = { payload: Record<string, any> };

function Units({ count, kind }: { count: number; kind: string }) {
  return <div className={`aw-units ${kind}`} aria-label={`${count}単位`}>{Array.from({ length: Math.min(12, count) }, (_, i) => <i key={i}/>)}{count === 0 && <span>0</span>}{count > 12 && <span>＋{count - 12}</span>}</div>;
}

function Transfer({ count, receipt }: { count: number; receipt?: string }) {
  return <div className="aw-transfer"><ArrowDown className="aw-scene-arrow"/>{receipt && <span key={receipt} aria-hidden="true">{Array.from({ length: Math.min(3, count) }, (_, i) => <i key={i} style={{ animationDelay: `${i * .08}s` }}/>)}</span>}</div>;
}

function ObservedWorld({ state, actual, arrived }: { state?: Facts; actual?: any; arrived: number }) {
  if (!state) return <div className="aw-world-empty">ここに外の世界が現れます。<small>Liveで開始、または保存した実験を選択</small></div>;
  const p = state.payload;
  const pending = p.pending.reduce((n: number, b: any) => n + b.amount, 0);
  return <div className="aw-scene" role="img" aria-label={`実測World Tick ${p.tick} 待機${p.queue} 完了${p.delivered}`}>
    <div className="aw-scene-time">実際の世界 <b data-testid="world-time">t = {p.tick}</b></div>
    <div className="aw-transit"><span>到着前のタスク</span><Units count={pending} kind="pending"/><b>{pending} 単位</b></div>
    <Transfer count={arrived} receipt={actual?.receipt}/>
    <div className="aw-queue"><span>待機キュー</span><Units count={p.queue} kind="queued"/><b>{p.queue} / {p.capacity}</b></div>
    <ArrowDown className="aw-scene-arrow"/>
    <div className="aw-machine"><Cpu size={38}/><div><span>処理する機械</span><strong>能力は非公開</strong><small>AIは現在の能力を直接見られない</small></div></div>
    <Transfer count={actual?.metrics.processed || 0} receipt={actual?.receipt}/>
    <div className="aw-completed"><span>処理済みタスク</span><Units count={p.delivered} kind="completed"/><b>累計 {p.delivered} 単位</b></div>
    <p className="aw-goal">目標：{p.target} 単位を完了</p>
    {actual && <p className="aw-world-change" key={actual.receipt}><Check size={15}/>この一手で {actual.metrics.processed} 単位を実処理</p>}
  </div>;
}

function MetricFuture({ prediction }: { prediction: any }) {
  const vectors = prediction?.vectors;
  if (prediction?.raw?.status !== 'estimated' || !vectors?.queue?.length) return <div className="aw-no-forecast">未来の数値はまだありません</div>;
  const branches = prediction.raw.branches || [];
  const max = Math.max(1, ...branches.flatMap((b: any) => b.queue), ...vectors.queue);
  const point = (n: number, i: number) => `${18 + 154 * i / Math.max(1, vectors.queue.length - 1)},${58 - 42 * n / max}`;
  return <svg viewBox="0 0 190 80" aria-label="保存された待機量の予測経路">
    <path d="M18 12V58H177" fill="none" stroke="#324d68"/>
    {branches.map((b: any, i: number) => <polyline key={i} points={b.queue.map(point).join(' ')} fill="none" stroke="#7999bd" opacity={b.weight ? .45 : .16} strokeDasharray={b.weight ? undefined : '3 3'}/>)}
    <polyline points={vectors.queue.map(point).join(' ')} fill="none" stroke="#84baff" strokeWidth="2.5"/>
    {vectors.queue.map((_: any, i: number) => <text key={i} x={18 + 154 * i / Math.max(1, vectors.queue.length - 1)} y="76" textAnchor="middle">+{i + 1}</text>)}
    <text x="4" y="16">{max}</text><text x="5" y="61">0</text>
  </svg>;
}

export function architectureStops(events: PlaygroundEvent[]) {
  return events.filter(e => e.scope === 'world' && ['observed', 'deliberation', 'candidate_selection', 'authorization', 'execution_intent', 'outcome', 'cognitive_update'].includes(e.event.kind) && (e.event.kind !== 'outcome' || e.receipt_verified));
}

export default function ArchitectureWorld({ cycle, state, active, moduleId, onModule, candidateId, onCandidate, last, checkpoint, reward }: {
  cycle: CognitiveCycle; state: Facts | null; active: ModuleId[]; moduleId: ModuleId;
  onModule: (id: ModuleId) => void; candidateId: string; onCandidate: (id: string) => void;
  last?: PlaygroundEvent; checkpoint: any; reward: number;
}) {
  const before = cycle.observed?.event.data.state.payload;
  const actual = cycle.outcome?.event.data.observation;
  const options = visitorOptions(cycle);
  const proposal = cycle.trace?.simulation.proposal_module;
  const chosen = options.find((o: any) => o.selected);
  const inspected = options.find((o: any) => o.candidate.fingerprint === candidateId) || chosen || options[0];
  const hasPredictions = options.some((o: any) => o.known);
  const llmCalled = proposal?.called === true;
  const recordFor: Partial<Record<ModuleId, PlaygroundEvent>> = {
    perception: cycle.deliberation || cycle.observed,
    prediction: hasPredictions ? cycle.deliberation : undefined,
    simulation: cycle.trace?.simulation.candidates.some((c: any) => c.paths?.length) ? cycle.deliberation : undefined,
    decision: cycle.selection || cycle.deliberation,
    execution: cycle.outcome || cycle.intent || cycle.authorization,
    memory: cycle.update || (cycle.feedback.length ? cycle.deliberation : undefined),
  };
  const wire = (id: string, source?: PlaygroundEvent) => ({ 'data-testid': `aw-flow-${id}`, 'data-reference': source?.reference || '', className: `aw-wire ${source ? 'supported' : ''} ${source?.reference === last?.reference ? 'carrying' : ''}` });
  const title = (id: ModuleId, index: string, text: string, verb: string) => <button className={`aw-module-title ${active.includes(id) ? 'active' : ''}`} data-testid={`aw-module-${id}`} aria-pressed={moduleId === id} onClick={() => onModule(id)}><span>{index}</span><div><h3>{text}</h3><small>{verb}</small></div></button>;
  const descriptions: Record<string, string> = {
    observed: '外の世界を観測し、現在の世界を内部で表現する',
    deliberation: '内部モデルと行動案から複数の未来を計算し、価値を比べる',
    candidate_selection: '検証済みの候補から、一つの行動を選ぶ',
    authorization: '選んだ一手について、最終的な実行許可を得る',
    execution_intent: '実行する一手を記録する。世界はまだ変わっていない',
    outcome: '一手を実行し、変化した世界を正式に観測する',
    cognitive_update: '確定した実結果を経験として保持し、次の認知へ戻す',
  };
  return <section className="aw" aria-label="世界とPreActの認知循環">
    <div className="aw-current"><span>いま記録が示す処理</span><strong>{last ? descriptions[last.event.kind] || '経験とモデルの状態を確認する' : '実行記録を読み込んでいます'}</strong><small>明るい接続は元の処理記録がある経路</small></div>
    <div className="aw-system">
      <section className="aw-world" aria-label="外の世界">
        <header><span>OUTSIDE</span><h2>外の世界</h2><p>行動すると、実際に変わる環境</p></header>
        <ObservedWorld state={state || undefined} actual={actual} arrived={actual && before ? Math.max(0, actual.metrics.available_work - before.queue) : 0}/>
        {actual && <dl className="aw-measured"><div><dt>累積 Reward</dt><dd>{fmt(reward)}</dd></div><div><dt>直前 Overflow</dt><dd>{actual.unsafe ? '検出' : 'なし'}</dd></div></dl>}
        <div className="aw-world-example"><Box size={16}/><p>ここで使う世界の例：<b>Queue World</b><br/>仕事の流入・待機・処理を再現した2D環境</p></div>
        <div className="aw-world-times">{before ? `認知への入力 t=${before.tick}` : '観測待ち'}<ArrowRight size={15}/>{actual ? `実行後 t=${actual.state.payload.tick}` : '実行結果待ち'}</div>
      </section>
      <div className="aw-boundary" aria-label="外の世界と内部の間を渡るデータ">
        <div {...wire('observation', cycle.observed)}><span>観測</span><ArrowRight/><small>Observation</small></div>
        <div {...wire('action', cycle.intent)}><span>行動</span><ArrowLeft/><small>Action</small></div>
        <div {...wire('experience', cycle.outcome)}><span>実結果</span><ArrowRight/><small>Receipt</small></div>
      </div>
      <section className="aw-agent" aria-label="PreActの内部">
        <header className="aw-agent-header"><div><span>INSIDE / MODULAR WORLD-MODEL ARCHITECTURE</span><h2>PreAct の内部</h2></div><p>世界モデルの機能を、<br/><b>独立したモジュールをつないで再現する。</b></p></header>
        <div className="aw-top"><div className="aw-representation" aria-label="認知した世界と変化のモデル">
          <article className="aw-node aw-perception">
            {title('perception', '01', '認知', '観測を、扱える内部表現へ')}
            <div className="aw-node-body"><div className="aw-representation-label"><Eye size={16}/>{cycle.belief ? '認知した世界（Belief）' : '受け取った観測'}</div>
              {before ? <div className="aw-state-copy" data-testid="aw-internal-state" data-state-id={cycle.observed!.event.data.state.id}><span>t={before.tick}</span><div><b>到着前 {before.pending.reduce((n: number, b: any) => n + b.amount, 0)}</b><ArrowRight size={12}/><b>待機 {before.queue}</b><ArrowRight size={12}/><b>完了 {before.delivered}</b></div></div> : <p>外の世界からの観測を待つ</p>}
              <small>分かることと、分からないことを分ける。<br/>機械の現在・将来の能力は未観測。</small><div className="aw-implementation">観測Adapter + WorldPlanner</div>
            </div>
          </article>
          <div {...wire('state-model', cycle.deliberation)}><ArrowDown/><small>世界の表現</small></div>
          <article className="aw-node aw-prediction">
            {title('prediction', '02', '予測', 'この世界は、どう変わるか')}
            <div className="aw-node-body"><div className="aw-representation-label"><Cpu size={16}/>変化を予測する内部モデル</div><p className="aw-known-rule">既知の規則：タスクは{before?.arrival_delay ?? '—'} tick後に到着</p><p className="aw-learned-parameter">{cycle.model.pHigh == null ? 'このroundの数値予測は未生成' : `能力の仮説：1単位 / 3単位（3単位の確率 ${fmt(cycle.model.pHigh * 100)}%）`}</p><small>{cycle.model.pHigh == null ? 'この時点にモデル利用を示す予測記録はありません。' : cycle.model.basis === 'fixed_prior_fallback' ? '経験モデルが使えないため固定事前値を使用' : '過去の実行経験から学んだ能力分布を使う'}<br/>{cycle.model.pHigh != null && '同じ傾向が続くと仮定した推定。'}</small><div className="aw-implementation">{cycle.model.pHigh == null ? '数値予測は未生成' : cycle.model.basis === 'fixed_prior_fallback' ? '固定事前モデル' : '学習モデルbinding'} · {short(cycle.model.version)}</div></div>
          </article>
        </div>
        <div {...wire('model-futures', recordFor.simulation)}><ArrowRight/><span>内部のモデルを使って「この行動なら？」を計算する</span></div>
        <article className="aw-node aw-simulation">
          {title('simulation', '03', 'シミュレーション', '現実を動かす前に、複数の未来を試す')}
          <div className="aw-proposal-source" data-testid="aw-proposal-module"><Workflow size={15}/><span>行動案をつくる実装：</span><b>{proposal ? llmCalled ? 'LLM · Nemotron' : '観測準備方策（LLMはこのround未呼出）' : 'ネイティブの候補生成'}</b>{llmCalled && <small>LLMが提案した {proposal.output_fingerprints.length} 案を比較</small>}</div>
          <svg className="aw-branch-wires" viewBox="0 0 300 26" preserveAspectRatio="none" aria-label="行動候補ごとに分岐する予測">{options.map((o: any, i: number) => <path key={o.candidate.fingerprint} d={`M150 0V8H${300 * (i + .5) / options.length}V26`} className={o.known ? 'recorded' : ''}/>)}</svg>
          <div className="aw-future-branches">{options.length ? options.map((option: any, i: number) => <button key={option.candidate.fingerprint} data-testid="aw-future" className={`${option.selected ? 'chosen' : ''} ${inspected === option ? 'inspected' : ''}`} onClick={() => { onCandidate(option.candidate.fingerprint); onModule('simulation'); }}><span>未来の候補 {String.fromCharCode(65 + i)}</span><strong>{visitorAction(option.candidate.action)}</strong><MetricFuture prediction={option.prediction}/>{option.known ? <p>完了量の予想 <b>＋{fmt(option.completed)}</b><small>待機量の予想 {fmt(option.waiting)}</small></p> : <p>未測定の未来は補わない</p>}<em>{option.selected ? <><Check size={12}/>選ばれた経路</> : '未実行の比較候補'}</em></button>) : <div className="aw-empty-futures"><GitBranch size={30}/><p>この観測から作った行動案の未来が、ここで分岐します。</p></div>}</div>
          <svg className="aw-branch-wires aw-merge-wires" viewBox="0 0 300 26" preserveAspectRatio="none" aria-label="候補の比較から一つの行動へ">{options.map((o: any, i: number) => <path key={o.candidate.fingerprint} d={`M${300 * (i + .5) / options.length} 0V12H150V26`} className={o.selected ? 'selected' : ''}/>)}</svg>
          <footer>{hasPredictions ? `${cycle.trace?.simulation.horizon} tick先まで追加介入なし。線は保存された待機量の数値経路。仮想Stateではありません。` : '能力測定など、数値未来のないActionに架空の未来を付けません。'}<b>{hasPredictions ? '実装：数値シミュレータ QueueTemporalEngine' : '数値シミュレータは、このroundでは未使用'}</b></footer>
        </article>
        </div>
        <div {...wire('futures-decision', cycle.selection)}><span>候補を比較し、一つに絞る</span><ArrowDown/></div>
        <div className="aw-return-row">
          <article className="aw-node aw-memory">{title('memory', '06', '経験・学習', '実結果を、次の認知へ戻す')}<div className="aw-node-body"><Database size={24}/><strong>{cycle.update && actual ? 'この一手を経験として記録' : '確定した実結果を待つ'}</strong><p>{cycle.feedback.length ? `今回の認知も、過去の確定経験 ${cycle.feedback.length} 件を参照` : '実行していない未来は経験にしない'}</p><small>{checkpoint?.phase === 'await_promotion' ? '新モデルは独立評価合格・明示昇格待ち' : '経験の蓄積とモデル更新は別。更新は明示昇格。'}</small><div className="aw-implementation">EpisodicMemory + LearningCoordinator</div></div></article>
          <div {...wire('execution-memory', cycle.outcome && cycle.update)}><ArrowLeft/></div>
          <article className="aw-node aw-execution">{title('execution', '05', '実行', '検証して、一手だけ世界へ')}<div className="aw-node-body"><strong>{actual ? `t=${actual.state.payload.tick} に世界が変化` : cycle.intent ? '実行Intent保存済み・実結果待ち' : cycle.authorization ? '最終Gate許可・実行待ち' : '選択・実行許可を待つ'}</strong><p>{actual ? `実処理 ${actual.metrics.processed} / 実待機 ${actual.state.payload.queue}` : '世界が変わるのは実行後だけ'}</p><small>{chosen?.known && actual ? `次tickの処理量：予想 ${fmt(chosen.nextCompleted)} → 実際 ${actual.metrics.processed}` : 'LLMや予測値だけでは実行を許可しない'}</small><div className="aw-implementation">Core Runtime + World Executor</div></div></article>
          <div {...wire('decision-execution', cycle.authorization)}><ArrowLeft/></div>
          <article className="aw-node aw-decision">{title('decision', '04', '意思決定', '目的に照らして、一つを選ぶ')}<div className="aw-node-body"><strong>{visitorAction(cycle.selected?.action)}</strong><p>{cycle.selected?.action.kind === 'probe_service' ? '能力を確かめるための観測準備Action' : '予測される進捗と、待機・投入の費用を比較'}</p><small>{cycle.selection ? '検証済みの候補集合から選択したAction' : '価値順位だけでは選択済みにならない'}</small><div className="aw-implementation">DeliberationPlanner · 純粋な選択器</div></div></article>
        </div>
        <div {...wire('memory-cognition', cycle.feedback.length ? cycle.deliberation : undefined)}><ArrowLeft/><span>確定経験を参照 → 次の認知・予測・意思決定へ</span><ArrowUpIcon/></div>
      </section>
    </div>
    <div className="aw-explanation"><span>クリックしたモジュール</span><b>{modules.find(m => m.id === moduleId)!.english}</b><p>{modules.find(m => m.id === moduleId)!.description}</p><small>技術者向け詳細では、入力・出力・実装クラス・元イベントを確認できます。</small></div>
  </section>;
}

function ArrowUpIcon() { return <ArrowDown style={{ transform: 'rotate(180deg)' }}/>; }
