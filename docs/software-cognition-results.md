# Software Cognitive Runtime 実測

2026-10-09。実行済みの[専用protocol](../benchmarks/software-cognition-v1.json)と
[結果JSON](../benchmarks/software-cognition-results-v1.json)を保存した。
過去のqueue / memory / belief / information-seekingの凍結資料は上書きしていない。

## 条件と検証方法

checkout-repair（準備→適用の2手）、ceil_batches、clamp_fractionの3タスク。
seed 11/12/13、各2反復、元Runtime・CognitiveAgent・記憶なしCognitiveAgentの3構成、
計54 episode。全て列挙済みfirst-party Pythonを既存保護probeで実際に実行した。
Programの保護評価seedも独立固定し、conditionの順をrotate/逆転して実行。
World/Store/Registry/Agentは毎回新規で、他のテスト/E2E終了後に最終評価を実行した。

共通Policy: search=true、width=3、depth=3、nodes=40/search、calls=24/episode、
30秒/episode、費用上限$1、calibration=false。元RuntimeのTask.max_stepsと同じ
episode行動上限を認知版にも適用し、round間でcall/time/costを再充填しない。
測定対象はepisode呼出しで、fixture/Store/Agent構築と測定後のauditは除く。
CPUは親Pythonのprocess_timeで子Python CPUを含まない。wallは子process実行を含む。
環境: Python 3.12.13、Darwin arm64。短時間のlocal測定で有意差の推定は行っていない。

## 平均episode結果

| 指標 | 元Runtime | 認知層あり | 記憶なし認知層 |
| --- | ---: | ---: | ---: |
| 成功率 | 100% | 100% | 100% |
| unsafe率 | 0% | 0% | 0% |
| 実行Action数 | 1.333 | 1.333 | 1.333 |
| ABSTAIN | 0 | 0 | 0 |
| engine calls | 8 | 8 | 8 |
| executable検証 | 4 | 4 | 4 |
| 測定済み外部費用 | $0 | $0 | $0 |
| 観測呼出し | 7 | 9.667 | 9.667 |
| Memory retrieval | 0 | 6.333 | 0 |
| Planner inference | 0 | 6.333 | 6.333 |
| native候補生成 | 2.333 | 2.333 | 2.333 |
| 親process CPU | 68.59 ms | 75.84 ms | 89.35 ms |
| episode wall | 165.69 ms | 172.93 ms | 187.29 ms |
| Memory retry signal | 0 | 0 | 0 |

認知層ありは元Runtime比でCPU約10.6%、wall約4.4%増加した。記憶なし版は
CPU約30.3%、wall約13.0%増加。この短い測定の順序/OSノイズの影響を含むため、
「Memoryを切ると遅くなる」という一般則や最適化効果は主張しない。
各54件の原測定値はJSONに保持した。reward定義がないSoftware fixtureなので、
人工的なrewardやthroughputを追加して改善を演出していない。

## 同一性とMemoryの価値

全9 task/seed組で、3構成×2反復の行動・実Observationのpayload/provenance/checks/metrics・
最終状態・成功/危険・確定実行由来のhorizon-1学習ラベルが一致した。
random Action/Prediction/receipt IDとtimestampを意味比較から除き、その前に
元Gate policy/evidence hash、authorization→intent→outcome、receiptのcomplete状態、
input State/Action指紋を全件検証した。summaryは安全性authorityにならない。
独立auditorも元DBと集計を再読出しして54件全て成功。

標準タスクではすでにnative候補・Verifier・Core探索で修正でき、Memoryによる
候補順位変更は0。**認知層による成功率・安全性・検証回数の改善は未実証**。
追加の再観測とMemory照合には実コストがある。これらの小タスクには既存Runtimeが
より単純な選択肢で、認知層を必須にはしない。

別の統合テストでは、許可済みPREPAREを同一stateで実際に安全実行して目標未達となった
receiptを取得し、同じ候補poolでPREPAREの再試行をSAFEより後順位へ移すことを検証した。
Memoryなしでは順位は変わらない。これは実経験で候補順位が変わる機能の証拠であり、
標準ベンチマークの能力向上や実用的な長期適応の証拠とは区別する。

## 実行した品質検証

- 基準: 既存470 Python tests成功。最終: `.venv/bin/pytest -q` 497 passed in54.59s。
- 新規Software統合/否定/benchmark audit tests 27件成功。multi-roundとnative仮想探索、
  verifier/予算不足、入力変異、stale state、Task/Policy緩和、pending/cancel/restart、
  forged/aborted/外部receipt更新、未実行枝排除、コピー、新episode、reuse opt-outを含む。
- `.venv/bin/ruff check src tests workers scripts` とformat check成功、155 files。
- contracts再生成byte一致、frontend build成功、既存Chromium E2E 6 passed in8.2s。
  buildには既存のReact Flow `use client` 無視警告があり、ビルド失敗ではない。
- `preact cognitive-demo --seed 11 --max-rounds 4` を実行し、各round一手、計2手、
  12 engine callsで成功/unsafe=falseを確認。

distribution検査とGitHub CIの完了記録は[progress](agent-progress.md)およびPR Checksを参照。
実装途中のqueue上限の回帰6件はepisode予算のopt-in化で修正し、旧テストを変更せず
全件再検証した。予備測定と並行E2E中の測定はprivate参考値に残し、本表に混ぜていない。

## 再現と残る課題

```sh
uv run python -m scripts.bench_software_cognition \
  --output .cache/new-software-study --report .cache/new-software-results.json
uv run python -m scripts.audit_software_cognition \
  --report .cache/new-software-results.json --raw .cache/new-software-study
```

新規output/reportを指定する。source SHA・protocol SHA、54件の網羅性、aggregate、
paired意味結果、元receipt/Gateを監査する。raw DB・semantic cacheは公開しない。
公開summary-only監査では元receiptを確認できず、その限界を出力する。

限定された3taskであり、任意repository編集、LLM比較、skill獲得、長期改善を示していない。
WorldPlannerはGoalを新たに解釈する汎用問題解法ではなく、native task proposalsの接続と
receipt-grounded retry heuristic。新episodeは新Agentで始め、未確定実行の実環境照合と
再開判断はホストが行う。次は、失敗/再試行が自然に発生する実用Software fixtureと、
reconciliation APIを明示した中断回復を優先する。
