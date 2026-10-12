# Task-Aware Continuous Adaptation — 測定結果

既存PR #14の100tick・モデルによるタスク判断3回、Reward -1.6／delivered4という
結果は変更していない。今回の比較は独立したpilotと`queue-task-v1.json`を使う。

## Pilot（本評価前の探索）

| 条件 | 通常Action / Probe | モデル使用 | delivered | Reward | 昇格 | 制約 |
|---|---:|---:|---:|---:|---:|---|
| v1: 持続性0.98、Goal350、200/220tick | 155 / 45 | B40 | 290 | 210.95 | 1 | Aはタスク選択に未使用、Goal未達 |
| v2: 持続性0.95、Goal300、200/220tick | 199 / 1 | B47 | 305 | 225.45 | 1 | Goal閾値は達成、episode完了条件は未達 |
| v3: readiness不足数を使用、220/220tick | 204 / 16 | A31 / B40 | 305 | 223.95 | 1 | 第2回回復は有効5件で未達 |

v3では通常Actionから有効86件、Probeから16件を得た。最初の失効tick61、Bへの
昇格tick85、Bの失効tick150。Bは失効後の通常Action有効16件から学習し、独立評価と
実Worldの新監視を通過した。第2回の訓練候補は通常4件＋Probe1件で不足した。
Goal閾値達成はtick183、episode終了tick220。unsafe、Gate拒否、ABSTAINは0だった。

v3の実行済みActionの一手先状態MAEは0.140845、coverageは0.322727。
未実行Actionに実Outcomeを与えていない。H3比較の品質と閉ループ一手実測は別の指標である。
v3のCPU1284.53秒／wall1318.65秒は、回帰テスト等と同時に実行した環境の参考値である。
独立評価・再検証もepisode計算時間へ含まれる。実Worldのtickはその計算中に進まない。

pilotで修正した不具合は、初期fresh観測と学習Outcome確定の順序、JSONのtuple/list
一致、準備Healthの不足観測数、CognitiveAgentの実行後再観測時の自分のrun manifest
取り扱いである。条件探索はpilot段階に限定した。本評価ではVOI持続性0.95、Goal300、
220tick、seed409、既存評価関数・昇格条件を固定する。成功seedの選別はしない。

通常Action数には`submit(0)`も含むため、これだけでタスク進捗を主張しない。
本評価ではGoal達成前のモデルAction数、仕事を進めたAction数、deliveredも保存する。
一般化性能・未見seedへの優位性・LLMやPhysical Worldへの適用効果は未実証である。

## 本評価の比較上の注意

再学習なし方式でもverified Memoryを使う既存Bayesian Fallbackは適応する。
したがって、これは「記憶も学習も一切使わない方策」ではない。
モデルが利用可能にならずFallbackだけで完了したケースは、そのまま区別する。
固定submit(1)は一候補、通常タスク選択は三候補を検証するため、追加Verifier費用は
実測して比較する。各roundの上限は同じ128 callsであり、呼出し数の差を隠さない。

Probe中心方式はPR #14と同じく、利用可能な各epochでタスク判断を一度行い、
残りを正式Probeに使う比較controlである。PR #14の過去結果そのものの再実行ではなく、
今回の220tick／Goal300／同一scheduleに合わせた新しいpaired controlである。
元の100tick評価や公開結果は変更していない。

## 固定本評価：対応比較

全方式は同じseed409・220tick・Goal300・cap12・外生scheduleを使う。
各セルは1 episodeの結果であり、成功率の母集団推定ではない。

| 環境 / 方式 | 通常 / Probe | Goal達成tick | delivered | Reward | Goal-capped Utility | holding loss | モデルAction / 昇格 |
|---|---:|---:|---:|---:|---:|---:|---:|
| low-high-low / Probe中心 | 3 / 217 | 未達 | 5 | -6.60 | -6.60 | 0.00 | 3 / 2 |
| low-high-low / 固定submit(1) | 220 / 0 | 未達 | 219 | 186.00 | 186.00 | 0.00 | 0 / 0 |
| low-high-low / 再学習なし | 220 / 0 | 161 | 305 | 232.25 | 227.25 | 27.00 | 0 / 0 |
| low-high-low / Task-Aware | 204 / 16 | 183 | 305 | 223.95 | 218.95 | 34.50 | 71 / 1 |
| high-low-high / Probe中心 | 3 / 217 | 未達 | 7 | -4.90 | -4.90 | 0.00 | 3 / 2 |
| high-low-high / 固定submit(1) | 220 / 0 | 未達 | 219 | 186.00 | 186.00 | 0.00 | 0 / 0 |
| high-low-high / 再学習なし | 220 / 0 | 150 | 300 | 228.50 | 228.50 | 26.50 | 77 / 0 |
| high-low-high / Task-Aware | 160 / 60 | 211 | 305 | 229.75 | 224.75 | 26.50 | 44 / 0 |

Low→High→LowではProbe中心・固定submit(1)よりGoalとRewardを改善したが、再学習なし
FallbackのUtility227.25に対し218.95で8.30悪化した。再学習なしはこの条件で学習モデルの
タスク選択0件、全て既存Bayesian Fallbackだった。改善を世界モデル学習だけの寄与としない。

High→Low→HighではRaw Rewardが再学習なしより1.25高いが、余分なdelivered5件をGoalで
上限化するとUtilityは3.75低い。候補は独立H3評価MAE0でも、parentもMAE0のため、既存の
改善条件を満たせず昇格しなかった。品質条件を緩めず、失効した親モデルも復活させていない。

主ケースのBは通常Action有効16件から学習し、独立8比較で状態／Action差分MAE0、coverage1。
parentの状態MAE2.44444／差分MAE1.11111、固定事前は1.0／0.56944だった。
その後8件の新監視データと明示Promotionを経て、同じWorldで40件の実タスクActionを選んだ。
同じ判断Stateの固定low参照と選択が異なる40件が、Runtimeの実行とcomplete Receiptまで反映された。
これは未実行low候補にActualラベルを付けた比較ではない。

主ケースの失効cutoffは61/150tick、B昇格は85tick。シフト48/120に対して検出遅延13/30tick。
第2回は有効5件で不足し、Goal達成後は追加の回復を推奨しなかった。Cの回復成功は主張しない。
新方式の一手実測MAE0.140845・coverage0.322727（71/220）と、独立H3評価のMAE0は別物である。

stable lowでは通常159 / Probe61、delivered161、Reward124.25、Goal未達。通常の仕事量が
少なく教師が検閲され、短いMemoryと時間減衰によるBeliefの不確実性が再発する。未校正の
情報価値近似が多くの測定を正当化し、実際の利益を減らす失敗例となった。
stable highでは通常220 / Probe0、通常有効100件、delivered300、Goal tick101、Reward255。
通常Actionだけで情報を取得でき、Goal達成前にモデルで92件のタスクActionを選択した。
この2条件は診断であり、他方式とのpaired comparisonは実施していない。

## 計算費用の解釈

主ケースはCPU976.65秒／wall1008.75秒、ローカル予測呼出し4518、Verifier呼出し1256。
再学習なしはCPU451.74秒、固定submit(1)は6.06秒、Probe中心は84.01秒だった。
新方式のモデル利用と回復再検証には大きな追加費用がある。測定中は他の回帰・監査も
同時に動いており、CPU/wall値は専用の速度比較実験ではない。
`max_rss_platform_units`は同一benchmark processの累積high-water値（macOSではbyte）で、
独立したcase別peak memoryではない。外部APIは0呼出し、既知API費用USD=0、
計算のドル換算は不明。Probe費用はRewardの単位で別途減算している。

stable lowでは一般の到達性能を結論しない。固定Goal300は低能力220tickの物理上限より
大きいが、delivered161という進捗低下は情報費用の失敗として残る。各診断条件を見た後に
Goal・horizon・VOI重みや昇格条件を変更していない。

## 診断条件の完全結果

この7条件はTask-Awareだけを実行した診断であり、Baselineに対する優位性は主張しない。

| 条件 | 通常 / Probe | delivered | Reward / Utility | モデルAction / 昇格 | 一手MAE / coverage |
|---|---:|---:|---:|---:|---:|
| stable-low | 159 / 61 | 161 | 124.25 / 124.25 | 148 / 0 | 0.0000 / 0.673 |
| stable-high | 220 / 0 | 300 | 255.00 / 255.00 | 109 / 0 | 0.0000 / 0.495 |
| noise | 203 / 17 | 305 | 211.90 / 206.90 | 70 / 1 | 0.2411 / 0.318 |
| sparse-costly-work | 220 / 0 | 219 | 0.00 / 0.00 | 0 / 0 | 未予測 / 0.000 |
| expensive-measurement | 219 / 1 | 305 | 223.50 / 218.50 | 47 / 1 | 0.2128 / 0.214 |
| quality-rejected | 205 / 15 | 305 | 230.75 / 225.75 | 31 / 0 | 0.0000 / 0.141 |
| short-observation-budget | 56 / 14 | 88 | 64.90 / 64.90 | 31 / 0 | 0.0000 / 0.443 |

- noiseの第2候補は有効通常16件と独立評価に合格したが、昇格前にGoalを達成したため
  昇格せず、旧Bの失効を維持した。評価合格だけでモデルを利用可能にしない。
- sparse-costly-workは通常Actionを220回実行しても有効教師0件、Probe0件、モデル利用0件。
  delivered219／Reward0はFallbackによるタスク進捗であり、世界モデル学習成功ではない。
- expensive-measurementではProbe費用2.0でも1回の正式測定を選び、通常有効89件を得た。
  Bは通常の16件で学習・昇格した。高価なProbeを禁止する固定ルールではない。
- quality-rejectedは明示的に異なる独立評価regimeを用い、候補を拒否した。失効Aを復活
  させずFallbackでGoalを達成した。評価条件を後から変更してはいない。
- short-observation-budgetは70 tickで打ち切る事前診断。Goal未達、昇格0であり、220tickの
  通常条件と成功率を直接混ぜない。未完了Outcomeや予測値で標本を埋めていない。

全15ケースでunsafe0、Gate拒否0、ABSTAIN0だった。これは既知boundのQueue fixtureでの
結果であり、一般の安全性の証明ではない。偽造・未確定Receipt、Source更新、取消、予算
不足等の拒否は新規／既存否定テストで検証する。本評価のGate拒否0を、拒否時にも安全に
動くことの唯一の根拠にはしない。

## 検証と未実証事項

Python全回帰715 pass、追加22テストpass、Ruff check/format pass、frontend契約差分なし、
build pass、隔離APIで既存Chromium E2E6 pass。wheel/sdist整合性を検証した。
新規テストはタスクとProbe双方のGate/Intent/Receipt、実行時だけのprivate service読出し、
Scheduler/Source/Recovery/Memory不整合、取消・予算不足・未確定／偽造Receipt、独立監査の
判断・State・Authorization期限・Outcome改ざん拒否を含む。
既存Core/Guard/Recovery/Trainer/Adapter/PromotionPolicyの実装は変更していない。

凍結した独立auditorで全15ケース・3150件のsource Receiptを再検証し、全ケースpass。
実State連続性、学習教師、独立評価、明示昇格、SchedulerのVOI再計算、候補のqualified集合、
最終Action、Authorization、Intent、Receipt、Rewardを照合した。
`reports/queue-task-v1-audit.json`の事前固定受入条件も全項目passである。
これは主ケースでの継続タスク実行とBの利用を確認する条件であり、全環境での回復成功や
Baselineに対するUtility優位を証明する条件ではない。
本評価protocol hashは`4d90e5fc5f16b5529eb257ad15d277209eae81fc933e676b15488118b9148e2a`、
auditor hashは`590918a99752ddf4e33df4265df7061936848abb506393054e7336af36830806`。
再現用raw Store/Artifactは公開せず、集約結果と監査結果を保存している。
GitHub CIはPR作成後に確認する。ローカル検証の成功とCI成功は区別する。

未実証なのは、学習モデルによる一貫したUtility優位、多seedへの一般化、LLMや他Domain
への適用、適切な独立評価Worldの自動発見、実時間で進行する環境への適用である。
このfixtureはActionがtickを進め、学習・評価のCPU処理中にsource Worldのtickは進まない。

次は、健康な学習分布と短期Beliefの不確実性を整合させる情報価値の評価、検閲に強い
観測計画、再び元の分布に戻った際の固定parent改善条件の扱いを、独立した設計・protocolで
調べる。今回の結果を見て閾値・目的関数・昇格条件を変更しない。
