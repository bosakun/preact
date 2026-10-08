# 認知queueの実測結果

2026-10-08。Phase 1 の実装、Phase 2 の観測による推定更新、Phase 3 の限定比較を実行。
6構成 × 5シード × 2条件 = 60エピソード。各episode最大60 tick。
これはfirst-party queueのオンライン適応実験であり、汎用LLM agentの優位性の実証ではない。

## 構成と評価方法

反応型は観測queueの閾値だけで投入/排出し、既存Runtimeの明示的direct baselineを使う。
記憶なしはledgerを残すがretrievalを無効化。適応なしはretrievalを行うがprior=3を固定。
固定予測はservice=3を仮定する単一のqualifiedモデルで即時/未来を検証する。
予測スコア用のunmeasured forecastは全構成で記録するが実行許可を与えない。
既存PreActはWorldの固定候補順を使い、認知層の目標達成後の排出方針を持たない。
認知版は実receiptからEMAを更新し、6 tickの仮説価値で候補順を変える。
安全版は公開service>=1の占有上界を既存Gateへ渡す。学習で安全閾値を緩めない。

開発条件はseed 0–4、high→low、変化tick=30、noise=0.1、容量8。
別条件はseed 100–104、low→high、変化tick=23、noise=0.2、容量6。
後者は事前に宣言した配置変更であり、学習済みepisode間転移や外部のblind benchmarkではない。
全条件の乱数系列は対応し、隠れたschedule/変化時刻はplanner/engineに渡さない。
報酬 = 処理数 − 0.25×queue占有 − 0.1×投入数。危険時にはepisodeを停止する。
成功は終了tickで85件以上処理し、overflowがないこと。安全率はepisode単位。
記憶なしと適応なしの行動/報酬が同じなのは、この最小モデルがretrieval経由でのみ学習するため。

## 開発条件

| 構成 | 成功率 | 危険率 | 平均報酬 | 処理数予測MAE | 全処理完了Brier | ECE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 反応型（Gateなし） | 60% | 40% | 44.98 | 0.887 | 0.0367 | 0.0367 |
| 記憶利用なし | 100% | 0% | 49.43 | 0.437 | 0.0467 | 0.0467 |
| 固定の楽観モデル | 40% | 60% | 46.41 | 0.306 | 0.0393 | 0.0393 |
| 適応なし | 100% | 0% | 49.43 | 0.437 | 0.0467 | 0.0467 |
| 既存PreAct・固定候補 | 100% | 0% | 24.72 | 1.053 | 0.0167 | 0.0167 |
| 認知PreAct | 100% | 0% | 57.77 | 0.263 | 0.0184 | 0.0204 |

適応なしに対する対応シード報酬差: +8.34、
seed bootstrap 95%区間 [1.98, 14.70]。
5シード・単一タスク族の区間であり、広い環境への統計的一般化を保証しない。

## 別条件

| 構成 | 成功率 | 危険率 | 平均報酬 | 処理数予測MAE | 全処理完了Brier | ECE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 反応型（Gateなし） | 80% | 20% | 61.81 | 0.973 | 0.1230 | 0.1230 |
| 記憶利用なし | 100% | 0% | 38.83 | 0.740 | 0.0167 | 0.0167 |
| 固定の楽観モデル | 0% | 100% | -1.52 | 1.493 | 0.1867 | 0.1867 |
| 適応なし | 100% | 0% | 38.83 | 0.740 | 0.0167 | 0.0167 |
| 既存PreAct・固定候補 | 100% | 0% | 61.58 | 0.820 | 0.0167 | 0.0167 |
| 認知PreAct | 100% | 0% | 52.44 | 0.508 | 0.0387 | 0.0394 |

適応なしに対する対応シード報酬差: +13.61、
seed bootstrap 95%区間 [10.76, 15.38]。
5シード・単一タスク族の区間であり、広い環境への統計的一般化を保証しない。

## 効果と反例

H1はこのqueueでは支持される。両条件で適応なしより報酬と処理数MAEが改善した。
開発条件の全処理完了Brierは改善するが、別条件では悪化する。
平均能力のEMAを確率混合モデルに使うと、少ない仕事では能力を観測できず、
low→highの検出が遅れる。確率の校正改善は一般的には成立していない。
別条件では既存PreAct/反応型が平均報酬で認知版を上回る。目標を満たすと投入を止める
認知方針と、継続投入で報酬を増やすbaselineの目的差も含むため、目標制御の純粋な効果ではない。
投入数/総処理数も減るので、throughput最大化に対する一方的な優位性はない。

H2は本分布で支持される。bound版の危険率0に対し、楽観固定モデルは能力変化で失敗。
これは公開された能力下限が正しい環境での結果であり、モデルの仮定が誤れば安全保証は失われる。
H3は検証不足/予算不足/推定のみのnegative testでVERIFYまたはABSTAINを確認した。
十分な予算の本比較では全episodeに実行可能な候補があり、round全体のABSTAINは0。
candidateに対するhard vetoとepisodeの停止は区別する。

## 適応とコスト

- development: 認知版の予測回復tickは [2, 2, 1, 2, 4]。
  平均wall 10.54s / CPU 9.51s、
  記憶なし平均wall 3.48s。
- transfer: 認知版の予測回復tickは [9, 3, 3, 1, 5]。
  平均wall 10.68s / CPU 9.65s、
  記憶なし平均wall 3.45s。

回復は真の分布平均±0.5に予測が3 tick連続で入る最初の位置。未来scheduleは分析のみで利用する。
low→highでprior=3の固定版が0 tickになるのは初めから新分布平均に近いためで、学習の証拠ではない。
horizon-1の成功/危険BrierはJSONに別記し、処理完了確率と混同しない。
ECEは5bin、episode内sampleに基づく。安全forecastの間違いを平均能力の校正で隠さない。

全60 tickを完了するbound条件は540 engine calls（9/tick）、360 escalation（6/tick）。
360 callsは未選択候補に使われる。安全判断に寄与する棄却も含み、全て不要と認定はしない。
同一requestのcache再利用回数も記録し、独立な証拠として数えない。
本実装では検証call削減は実証していない。receipt整合の反復読出しで認知版は遅い。
外部請求額は全て0、CPU/wall時間は実測。並行した回帰テスト等の影響もあるため速度比較は参考値。

## 再現と証拠

```bash
uv run python -m preact.cognition.benchmark --output .cache/new-cognitive-evidence
uv run python -m scripts.summarize_cognition .cache/new-cognitive-evidence \
  --output-doc .cache/new-cognitive-results.md --output-json .cache/new-cognitive-results.json
uv run pytest -q tests/test_cognition.py
```

今回のraw証拠: `.cache/cognitive-queue-v1-final`。manifest、各episodeのSQLite、events、実receipt、
prediction/error rows、content-addressed artifacts、各sourceのSHA-256を保存した。
[数値とsource/evidence hashes](../reports/cognitive-queue-v1-results.json)はrawから再計算・照合した。
最初の実行 `.cache/cognitive-queue-v1-run1` も保持し、最終境界修正後に全60episodeを再実行した。
既存凍結protocol/datasetは変更していない。benchmarkは新規outputを要求し、
実行途中の失敗は成功結果で置換しない。途中results.jsonとSQLiteは残るが自動resumeは未実装。

## 次のマイルストーン

1. receiptの整合を保つ索引/読出し効率と、支配された候補の検証コスト削減。
2. 需要で打ち切られた観測と変化検出を扱う確率belief、情報取得行動のablation。
3. 目標達成率/throughput/holding lossを揃えた目的設定と多シード・別タスク族評価。
4. 明示的なepisode間記憶転移、Crafter等の外部環境、必要性を測ってからlearned Engine。

自発目標、procedural skill獲得、online NN学習、一般的な異種model合成、
生物学的認知の忠実な再現、LLM-only比較は未実装/未実証。

公開リポジトリには数値要約とhashを収録する。上記raw証拠は開発環境に保持しており、
cloneには含まれない。再現コマンドは新しいローカルraw証拠を生成して監査する。
