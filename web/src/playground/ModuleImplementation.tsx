export default function ModuleImplementation({ trace }: { trace: any }) {
  const proposal = trace?.simulation.proposal_module;
  if (!proposal) return <p className="pg-implementation">{trace ? '行動案：既存のQueue候補生成。LLM呼出の記録はありません。' : 'このroundの実装記録は、まだありません。'}</p>;
  return <details className="pg-module-composition" open>
    <summary>実際に使った行動提案モジュール</summary>
    <p><b>{proposal.called ? 'ローカルLLMを実際に呼出' : '候補が一つの観測準備round：LLM未呼出'}</b><br/>{proposal.implementation}</p>
    <p>入力：State <code>{proposal.state_id}</code> と既存候補 {proposal.input_candidates.length} 件<br/>出力：{proposal.output_fingerprints.length} 件の候補Action</p>
    <p>Engine <code>{proposal.engine_id}</code><br/>Version <code>{proposal.engine_version}</code></p>
    <p>この出力を既存の数値予測・価値比較へ接続します。実行には独立検証とRuntimeの許可が必要です。</p>
    <pre>{JSON.stringify({ structured_output: proposal.structured_output, input_candidates: proposal.input_candidates, output_fingerprints: proposal.output_fingerprints, actual_call_usage: proposal.usage }, null, 2)}</pre>
  </details>;
}
