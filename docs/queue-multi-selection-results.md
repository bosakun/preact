# Verified Multi-Candidate Decision — 計測結果

## 実現した能力

Runtimeが全提案候補の独立検証とqualified集合を所有し、その集合の中だけで認知Plannerの
世界モデル価値を使って一候補を選ぶ。最終選択を既存のfresh Gate・Authorization・
durable intent・一手実行へ接続し、complete receiptとActual Observationまで追跡した。
既存Coreのデフォルト選択、Gate、安全証拠、実行権限は変更していない。

投入許可を持つ専用fixtureのtick 8では、正式学習モデルのHealthはavailableで、
3候補の価値はsubmit(3)=2.7、submit(1)=0.9、drain=0だった。公開queue boundは
全候補を許すが、独立mandatory permit検証がsubmit(3)を拒否した。残るqualifiedの
submit(1)を選び、通常Runtimeのcomplete receiptを取得した。予測上の第一候補と
最終選択と実行済みActionは、別々の記録である。

正式probe 16件から学習した2モデル（high観測2/16と8/16）による統合テストでは、
同じ観測内容・provenance・uncertainty・Goal・Gate条件で実Actionが1と3に変わった。
各Worldの観測時刻は実際に取得したもので、比較のために捏造していない。Core utilityは
3候補で同じ、提案順も3/1/0のままである。能力分布を直接書き換えたテストではない。

## 固定protocolと評価範囲

[protocol v2](../benchmarks/queue-multi-selection-v2.json)、
[公開集計JSON](../benchmarks/queue-multi-selection-v2-results.json)、
[実装・再現方法](queue-multi-selection.md)を参照。既存PR #8〜#12の結果は変更しない。

- 通常Queue 7条件と別仕様の投入許可fixture 1条件 × seed 101/102 × 6方式 = 96 episode。
- 正式回復後Model Bのlow→high / high→low × 2 seed × 6方式 = 24 episode。合計120。
- 最大48 tick、Goal 30、capacity 12、H3、holding 0.25、投入0.1、probe 0.05。
- 一候補対照だけwidth/node=1、他方式は3。全方式のcall上限128、時間上限120秒は共通。
- 正式な準備行動も費用・tickへ含む。通常13 tick、部分観測とpermit fixtureは8 tick。
- 回復用学習・独立評価・昇格・新監視準備は各方式で実行し、別の準備費用にも記録する。
- 価値関数、検出条件、意思決定seedはpilot後に変更していない。

既存Plannerと固定方策の対照も、新方式と同じ複数候補検証経路で候補順位を選択に使う。
`core_utility_control`は同じ予測・検証を行いutilityで選ぶ交絡対照であり、デフォルトCoreを
変更した方策ではない。回復条件でPromotionからactive経路を構成するのは学習新方式と
一候補方式である。他の対照は回復Bを監視根拠として用いる実験経路であり、昇格済み
学習スコアの利用成功として数えない。Promotionの利用停止条件によるcoverage差も
含むため、回復条件の閉ループ比較を能力分布だけの純粋な因果効果と解釈しない。

新規v1 pilotは回復評価が4組で、既存の昇格条件8組を満たさず停止した。v1を保存し、
v2で回復比較seedを既存PR #12の301/302/303/304へ分離した。条件を緩和していない。
v2 pilotは60 episode / 2121判断 / 2120実行を初期版auditorで監査した。
本評価は追加の鮮度・時系列・Promotion commit検査を含む最終auditorで再検証する。

無noiseの条件では環境が決定論的で、seedを増やしても結果は同じになる。これは一般化
性能の証明ではない。noiseも2 seedのみの小規模対応比較である。Policy間ではActionに
よって状態が分岐するため、同tickの行動列比較を同一状態での因果比較と解釈しない。

## 実報酬とGoal-capped Utility

各セルは **実Reward / Goal-capped Utility** のepisode平均。Utilityは
`min(delivered,30) - holding - submission - probe`。準備の費用を含む。
120 episode中、新方式は20/20 Goal達成、一候補方式は18/20、他方式は20/20だった。
一候補方式の2失敗はpermit fixtureのtick 8でABSTAINして終了したもので、
48 tick実行したようには扱っていない。

| 条件 | 新方式 | 一候補 | 固定事前 | 既存Planner | 固定方策 | Core utility対照 |
|---|---:|---:|---:|---:|---:|---:|
| stable_low | 20.1 / 15.1 | 20.1 / 15.1 | -3.4 / -3.4 | 20.1 / 15.1 | 23 / 14 | -58 / -67 |
| stable_high | 26.6 / 26.6 | 26.6 / 26.6 | 26.6 / 26.6 | 31.1 / 26.1 | 35.5 / 25.5 | 96.5 / 18.5 |
| low_to_high | 21.1 / 21.1 | 21.1 / 21.1 | 10.1 / 10.1 | 25.6 / 20.6 | 30 / 20 | 8.9 / -48.1 |
| high_to_low | 26.6 / 26.6 | 26.6 / 26.6 | 26.6 / 26.6 | 30.35 / 25.35 | 35.5 / 25.5 | -12.2 / -42.2 |
| noise | 23.475 / 20.975 | 23.475 / 20.975 | 14.025 / 12.025 | 22.5 / 19 | 32.625 / 22.625 | -6.075 / -53.575 |
| partial | 20.5 / 15.5 | 20.5 / 15.5 | 20.5 / 15.5 | 20.5 / 15.5 | 35 / 26 | -75.7 / -84.7 |
| invalidated | 20.1 / 15.1 | 20.1 / 15.1 | 20.1 / 15.1 | 20.1 / 15.1 | 23 / 14 | -58 / -67 |
| admission_veto | 27.5 / 26.5 | -0.4 / -0.4 | 27.5 / 26.5 | 27.5 / 26.5 | 34.6 / 25.6 | 34.6 / 25.6 |
| recovered_low_to_high | 26.6 / 26.6 | 26.6 / 26.6 | 26.6 / 26.6 | 31.1 / 26.1 | 35.5 / 25.5 | 96.5 / 18.5 |
| recovered_high_to_low | 20.1 / 15.1 | 20.1 / 15.1 | -3.4 / -3.4 | 20.1 / 15.1 | 23 / 14 | -58 / -67 |

通常条件では新方式とPR #12の実Action列・Rewardは一致し、新方式の追加検証費用が
増えた。固定事前との比較ではstable lowのUtilityが−3.4→15.1、low→highが10.1→21.1
となった。stable high / high→lowでは固定事前との差はなかった。既存Plannerとも
差がない条件がある。noiseでは固定方策22.625に対して新方式20.975、partialでは
固定方策26に対して新方式15.5と悪化した。固定方策の実Rewardは多くの条件で新方式を
上回る。追加投入でGoal超過deliveredも報酬になるため、Utilityと別々に示した。

## 最上位候補拒否後の継続

以下はpermit fixtureの2 seed合計。通常Queueとは別のowned仕様である。

| 方式 | 第一候補拒否 | 代替qualifiedあり | 代替実行 | ABSTAIN | Goal達成 | Verifier calls |
|---|---:|---:|---:|---:|---:|---:|
| 新方式 | 68 | 68 | 68 | 0 | 2 | 400 |
| PR #12一候補 | 2 | 0 | 0 | 2 | 0 | 2 |
| 固定事前 | 68 | 68 | 68 | 0 | 2 | 400 |
| 既存Planner | 68 | 68 | 68 | 0 | 2 | 400 |
| 固定方策 | 0 | 0 | 0 | 0 | 2 | 400 |
| Core utility対照 | 56 | 56 | 56 | 0 | 2 | 400 |

第一候補拒否はモデル/fallback上の第一順位を指し、全候補のhard veto件数とは別である。
新方式は68/68件の代替候補を実行できた。一候補方式は2件とも実行せず停止した。
固定事前/既存Plannerも同じ複数検証方式では継続できており、改善を学習モデルだけの
功績とはしない。新方式のfixture Goal到達tickは45、固定方策は39と速かった。

全方式合計は4242判断、全候補検証完了4242件、ABSTAIN 2件、未解決ABSTAIN 0件、
unsafe実行0件。代替qualifiedあり260件、その実行260件である。全体の割合には対照
方式も含むため、新方式単独の成果と混同しない。予算4/7またはVerifierなしの否定テスト
では未展開/未解決を検出してselectorを呼ばず副作用なしで停止した。本評価の予算が
十分だったことは、低予算下の実用性の証明ではない。

## 学習モデルによる実Action変化

新方式と固定事前の、同seed・同tickで両方が実行したActionの不一致件数。
状態が分岐した後の対応比較であり、未実行ActionのActual Outcomeは作らない。

| 条件 | 実Action不一致 / 共通実行判断数 |
|---|---:|
| stable_low | 14/70 |
| stable_high | 0/70 |
| low_to_high | 14/70 |
| high_to_low | 0/70 |
| noise | 14/70 |
| partial | 0/80 |
| invalidated | 0/70 |
| admission_veto | 0/80 |
| recovered_low_to_high | 0/70 |
| recovered_high_to_low | 14/70 |

## 予測誤差とcoverage

閉ループでは実行したActionの一tick後のqueue/deliveredだけを採点する。
3 tickのenvironment-only予測は、次round以降も別Actionを実行する閉ループOutcomeと
直接比較しない。下表は新方式のepisode平均で、MAEは予測が成立した実行のみの値。
coverageは全判断に対する予測成立率、unknownは1−coverage。

| 条件 | 一tick状態MAE | coverage | unknown | delivered | holding | Goal tick |
|---|---:|---:|---:|---:|---:|---:|
| stable_low | 0 | 0.686 | 0.314 | 35 | 11 | 39 |
| stable_high | 0 | 0.743 | 0.257 | 30 | 0 | 22 |
| low_to_high | 0.273 | 0.943 | 0.057 | 30 | 5.5 | 33 |
| high_to_low | 0 | 0.743 | 0.257 | 30 | 0 | 22 |
| noise | 0.273 | 0.686 | 0.314 | 32.5 | 5.375 | 33 |
| partial | 0 | 0.1 | 0.9 | 35 | 11 | 39 |
| invalidated | — | 0 | 1 | 35 | 11 | 39 |
| admission_veto | 0 | 0.425 | 0.575 | 31 | 0 | 45 |
| recovered_low_to_high | 0 | 0.743 | 0.257 | 30 | 0 | 22 |
| recovered_high_to_low | 0 | 0.486 | 0.514 | 35 | 11 | 39 |

Healthが利用可能になるまで、正常な情報不足はno_probe Plannerへfallbackする。
invalidated条件では学習モデルを一度も使用せず全35判断をfallbackした。partial条件は
空queueで8 drainから始まり、有効観測が遅れて得られたため学習モデル利用は4判断のみ。
回復済みModel Bは両方向で実際に学習スコアを利用できた。利用件数・初回利用tickは
最終独立監査の実行統計で再確認する。計算済みの価値記録を実行件数として数えない。

別の正式branch実験では、Runtime/Receiptで作ったnonempty queue/pendingの同一状態
から3 Actionを別々に実行し、その後2 tickはenvironment-onlyに進めた。学習/評価は
独立したrun/receiptである。次の誤差はこの実験のtick 2/3であり、閉ループ効用とは別。

| 環境 | 分布 | 状態MAE | Action差分MAE | coverage | 比較組数 |
|---|---|---:|---:|---:|---:|
| low | learned | 0 | 0 | 1 | 2 |
| low | prior | 0.646 | 1.042 | 1 | 2 |
| high | learned | 0 | 0 | 1 | 2 |
| high | prior | 0.354 | 0.625 | 1 | 2 |

支持範囲の包含率を校正済み確率保証や安全性の証明として扱わない。既知の遅延・保存則
と学習した能力分布の合成であり、汎用因果理解を実証したという主張もしない。

## 計算・費用

本評価全体: process CPU 2754.361秒、wall 2954.581秒、
process peak RSS 163,250,176 bytes（約155.7 MiB）。
同じローカルホスト上で検証も実行しており、隔離した性能比較ではない。
計算性能最適化は行っていない。学習・独立評価・昇格にかかった費用も結果JSONの
`recovery_preparation_metrics`と`total_*seconds`に保持する。
共通の初期教師データはlow/high各16件の正式probeで作成した（32 tick、測定費用1.6）。
このオフライン準備の計算は全体時間へ含み、各policyの閉ループRewardへ重複加算しない。
回復を対照方式でも各回実行しているため、回復込み時間は実験手順の費用であり、
モデルを一度だけ昇格して使い続ける運用の時間として解釈しない。

| 条件・方式 | Decision CPU秒 | wall秒 | Verifier calls | local prediction calls |
|---|---:|---:|---:|---:|
| stable_low / 新方式 | 11.392 | 12.406 | 210 | 72 |
| stable_low / PR #12一候補 | 8.014 | 8.906 | 70 | 72 |
| admission_veto / 新方式 | 13.251 | 14.386 | 200 | 51 |
| admission_veto / PR #12一候補 | 0.201 | 0.22 | 1 | 3 |
| recovered_low_to_high / 新方式 | 108.349 | 117.625 | 210 | 78 |
| recovered_low_to_high / PR #12一候補 | 102.55 | 111.659 | 70 | 78 |
| recovered_high_to_low / 新方式 | 104.422 | 110.073 | 210 | 51 |
| recovered_high_to_low / PR #12一候補 | 114.887 | 120.472 | 70 | 51 |

stable lowでは新方式がVerifier 210回、一候補方式が70回で、新方式は3倍呼び出す。
独立検証で拒否された候補も検証費用に含む。全体のVerifier callsは22482、
ローカル未来予測4944、回復根拠のローカル再検証12960。準備のVerifierは別計数。
外部API calls/実際のAPIドル費用は0、CPU計算のドル換算は不明（null）であり0ではない。
存在しないAPI使用量やExecution Receiptを計測用に作っていない。

## 安全性・失敗例・未実証事項

- 全提案集合は実際のfingerprintを固定して照合する。未展開/未解決が一つでもあれば停止。
- selectorのコピー変更、不正ID、例外、async返却、取消、Policy変更、Store書込みは停止。
- 選択後とintent後のState/provenance/uncertainty/source/Receipt/Health/Auth期限変更を拒否。
- PreviewをAuthorizationへ流用せず、最終Actionだけをfresh GateとIntentへ結び付ける。
- 偽outcome、未確定Receipt、未実行候補をMemoryやDynamicsの教師にしない。
- 同一episode中の回復、深い探索、部分的qualified集合からの選択、自動昇格は未実装。
- selectorは信頼されたプロセス内契約であり、任意の悪意あるPythonのOS隔離ではない。
- 保証は監視付き経路と照合したsourceの範囲。直接TemporalEngineを監視済みとは主張しない。
- 独立auditorは保存フラグを信用せず、実際の分布/価値、Evaluation/Gate、qualified、
  選択ID、full State、時系列、最終Authorization/Intent/fresh Observation/Receiptを再計算する。
- 16 tickの独立再実行2回は各8実行/7代替実行、Action列と時間以外のhashが一致し、
  両方を監査した。hashは`cfbc4f20192e16d70851b65a09ba0713851e17bfadc2a9b2dd618a7277a9a229`。
  本評価120 episode全体を同じseedで再実行したという意味ではない。

次の能力は、限られた予算での検証完了条件、時間相関を含む予測とdrift、同一episode
でのモデル回復をそれぞれ別設計で検証する。今回の結果だけからLLM agentや他Worldに
対する優位性、一般的長期認知能力を主張しない。

## 完了した独立監査と実モデル利用

[最終監査JSON](../benchmarks/queue-multi-selection-v2-audit.json): 120 episode、4242判断、
4240実行を最終auditorで再検証してpass。保存フラグを採点せず、予測からcomplete receipt
までのState/fingerprint/run/時系列・根拠の対応を再計算した。

次の件数は新方式で**実際に実行された**Actionのepisode平均。監査でActual Receiptを
確認したものだけを数え、一候補方式の拒否されたモデル順位記録を利用成功に含めない。

| 条件 | モデルで実行 | 初回モデル利用tick | fallbackで実行 |
|---|---:|---:|---:|
| stable_low | 24 | 13 | 11 |
| stable_high | 26 | 13 | 9 |
| low_to_high | 33 | 13 | 2 |
| high_to_low | 26 | 13 | 9 |
| noise | 24 | 13 | 11 |
| partial | 4 | 42 | 36 |
| invalidated | 0 | — | 35 |
| admission_veto | 17 | 8 | 23 |
| recovered_low_to_high | 26 | 13 | 9 |
| recovered_high_to_low | 17 | 13 | 18 |

新方式のGoal達成後の投入Actionは全条件で0。ただしGoal到達前の投入が余剰deliveryを
生むことはあり、すべての投入が必要だったという証明ではない。対照方式の投入件数も
監査JSONの`submit_actions_after_goal`/`submitted_jobs`に残した。

### 検証コマンドと実績

- `.venv/bin/pytest -q --durations=12`: **687 pass**, 1206.04秒。新規42件を含む。
- `.venv/bin/ruff check src tests workers scripts`: pass。format check: 196 files pass。
- `npm --prefix web run contracts`: 生成契約2ファイルのgit diffなし。
- `npm --prefix web run build`: pass（依存モジュールのuse client警告のみ）。
- `PREACT_E2E_ARTIFACT_DIR=/private/tmp/preact-multi-e2e-final npm --prefix web run test:e2e`:
  Chromium **6 pass**, 12.7秒。
- 固定protocolのpilot、本評価、最終独立auditorを実行。v1 pilotの設定失敗を保存した。
- 取消・改ざん・Source更新・Authorization失効・全拒否・未解決・予算不足の否定テストpass。
- `uv build` / `scripts/audit_distributions.py`: wheel/sdist integrity pass。
  両配布物でPython source 89件を照合し、assets/license/private-pathも検証した。
  最初はsandboxのPyPI DNS制限で依存取得に失敗し、許可されたネットワーク経路で再実行pass。
  これはインストール後の実行や完全なsecret scanningの証明ではない。
- 最終headのCI結果は同PRのGitHub checksと検証記録を参照。

実測の識別情報:

- Protocol hash: `2502a96b783a16d0f74d389e3ab08bb4e52170ce169ee0b793e8ef00d3503160`
- Actor source hash: `b0fdb9bd84ac2caa63ea615c2910949b897413e3d13c666532ae91d2841ed33a`
- Semantic result hash: `b90bd713324fb3962452c1d69ec74b04abe4f36bf43d20e63270871118e486b2`
- Auditor hash: `0d56193a32ffac742089702bad26f68a4743c17d20d1c86a4f92cd18b7b1a01d`

公開用JSONは配列の整形のみ変更し、private出力のparsed内容と完全一致を確認した。
raw DB・manifest・Evidence・学習Artifact・研究原文は公開していない。
