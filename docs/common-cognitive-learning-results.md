# 共通認知・経験学習パイプラインの測定結果

## 実証範囲

同じCognitiveAgent/DeliberationPlanner/LearningCoordinatorからQueueとSoftwareを動かし、
正式RuntimeによるAction、complete Receipt、実観測を学習と次の判断へ接続した。
Queueは同じ実Worldを56 tick継続、Softwareは実際に異なる短いProgramWorldタスクを使う。
既知候補の結果記憶と未知候補・別タスクへの汎化は別であり、後者は未実証。

固定本評価は`benchmarks/common-learning-v2.json`、32ケース、seed11/12。
タスク部分の960 Receiptすべてを正式Runtimeで確定し、unsafeは0。
Softwareの準備/独立評価、Queueの初期学習/独立H3評価は別の実行として保持する。
生のDB・cache・モデル・protected evaluator入力は公開成果に含めない。

## Software

タスクは既存`ceil_batches`。公開候補の観測不足から8件の準備実行（各候補4件）を選び、
別の実episodeで8件の独立評価を行う。Teacherは実行したpatchのGoal達成とテスト通過率。
H1指標を専用Goal Adapterで評価し、指標から後続Stateを捏造していない。

| 公開候補順 | 学習＋明示昇格 | 固定事前0.5 | 既存Core | 学習・評価のみ（未昇格） |
|---|---:|---:|---:|---:|
| 通常 | 100% | 100% | 100% | 100% |
| 逆順owned fixture | 100% | 0% | 100% | 0% |

各セルは2 seed×4実タスク。逆順fixtureは同じState/候補集合/Gate条件で提案順だけを
反転する。学習モデルは公開候補コードから得た実績（正しいpatch=1、もう一候補=0）を
使い、固定事前が選ぶ候補と異なるActionを選ぶ。選択差はcomplete Receiptと実Goalへ
反映された。Modelの能力分布や成功ラベルを手動で差し替えていない。

独立評価のGoal MAEは学習モデル0、固定事前0.5、coverage100%。ただし保護された
examplesで失敗する既知候補を記憶する小規模な決定論的条件であり、未知コードの理解や
汎化ではない。両seedで結果が同じことも一般化性能ではない。

既存Coreは両条件で成功した。独立Verifierが実テストに基づくutilityを提供するため、
今回の学習機構の既存Coreに対する性能優位は示していない。
Softwareには独立したReward指標がない。結果JSONの基本集計のreward=0は指標未提供時の
集計既定値であり、実報酬として解釈しない。補足metricsではnullと実Goal進捗を明示する。

## Queue

全ケース56実行tick。Goal999は途中終了を防ぐ継続実行fixtureであり、Goal達成率は0。
実タスクの一般的な達成性能をこの設定から主張しない。両seedの下表の結果は同じ。
No recoveryは失効後に明示的な固定事前fallbackを利用する。

| 条件 | 回復 | 昇格 | Task / Probe | Delivered | Reward | Holding loss |
|---|---|---:|---:|---:|---:|---:|
| Stable high | あり / なし | 0 | 48 / 8 | 141 | 126.2 | 0 |
| Stable low | あり / なし | 0 | 46 / 10 | 47 | 29.6 | 12 |
| Low→High | あり | 1 | 18 / 38 | 39 | 32.9 | 0 |
| Low→High | なし | 0 | 48 / 8 | 129 | 115.4 | 0 |
| High→Low | あり | 1 | 18 / 38 | 24 | 4.25 | 15.25 |
| High→Low | なし | 0 | 48 / 8 | 47 | −18.2 | 59.5 |

Low→HighはAで8回、昇格済みBで10回の実タスクAction。High→LowはAで5回、Bで13回。
同じWorldをresetせず、失効後の確定経験から候補を学習し、既存の独立H3比較、16有効
学習標本・8比較・新しい監視条件を維持した明示Promotionを行った。

Low→Highで回復方式は大きく悪化した。準備のProbeが仕事を投入しないため、機会費用が
大きい。High→Lowは回復なしfallbackのqueue滞留が大きく、回復方式のRewardが良かった。
Stableでは差がない。Probe準備方策は情報価値最適化やPR #16 TaskAware方策の新しい
優位を証明するものではない。既存TaskAware実装/凍結結果は変更していない。

### 予測とcoverage

| 条件 | 回復H1 MAE / coverage | 回復なしH1 MAE / coverage |
|---|---:|---:|
| Low→High | 0.444 / 0.321 | 0.979 / 0.857 |
| High→Low | 0.444 / 0.321 | 1.063 / 0.857 |

H1 MAEは実際に選択・実行したActionに対応するqueue/delivered予測のみを採点する。
coverageの分母はProbeも含む全実行。Probeには未実行の能力測定値の予測を作らない。
観測されたケースを絞ってMAEが下がっても、それだけでは全体改善ではない。
H3環境のみの反実仮想比較は独立評価Worldの既存評価契約で別に採点する。
閉ループで選ばなかった候補にActual OutcomeラベルやAction差分MAEを付けていない。

## コストと検証

32ケース合計のCPUは1861.825秒、wallは1972.213秒。Software学習＋明示昇格は
準備/独立評価込みで約8.2〜11.4秒、既存Coreの4タスクは約0.43〜0.68秒。
Queueの56tickは約59〜290秒。並行検証のCPU競合があるため速度比較ではない。
process peak RSSは約131〜199MB。プロセス寿命全体の高水位であり、ケースごとの
独立メモリ割当ではない。外部API利用は0、ローカル計算のドル費用は未算定。
実行・Verifier・推論・学習制御の詳細は補足metricsに分離する。

Python全回帰は834 passed/8 skipped。その後追加された新プロセスのimport順テスト2件
も成功。既存Blender実プロセスは別実行8 passed、Ruff、frontend契約一致、build、
Chromium E2E6件、wheel/sdist整合性が成功。既存凍結Deliberation44ケース・486 Receiptの
再実行と独立監査も成功した。全32ケース・960タスクReceiptの独立監査は成功。最新headのCI結果はPRと進捗へ記録する。

## Pilotと修正履歴

40tickのpilotではLow→Highの回復標本が不足し昇格しなかった。双方12 delivered、
Reward9.2、Task8/Probe32だった。これは探索段階の旧readiness baselineによる結果で、
本評価のNo recovery fallbackとは区別する。過去結果を置き換えていない。

v1本評価は完了前に止め、評価Worldの実体分離と完了時刻の検証を追加した。
v2は全seed/World/候補順/昇格閾値を維持し、侵襲的tracemallocからRSS測定へ変更した。
不備を修正した各再実行は新しい出力先に保存し、成功するseedの選別や閾値調整はない。

## 次の課題

1. readinessの情報取得費用を減らす判断を、既存TaskAware/VOIとの共通接続で検証する。
2. Softwareの未観測候補、別タスク、変化した結果分布への学習・Health契約を拡張する。
3. Queue以外の独立評価方式と連続episode適応を追加Worldで検証する。
4. 学習モデルの有効性と意思決定の価値を、より多様で非決定論的な条件で評価する。

Physical統合、自動Promotion、汎用ニューラル世界モデル、強い情報隔離、通常の
高速化は今回実装していない。特定のQueue/Program規則をPreAct全体の定義にしない。
