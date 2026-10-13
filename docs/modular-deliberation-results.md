# Modular Counterfactual Deliberation v1 — 実測と限界

## 固定評価と対象

[protocol](../benchmarks/deliberation-v1.json)はpilot-v2の後に固定した。seeds 11/23、
44ケース。Softwareは通常checkoutと別宣言したworkflow fixture、各Core Search/短期/H2。
Queueはstable low/high、noise、warmup後のhigh→low、失効、部分観測、未対応sequence。
Queue各ケースに正式な別訓練episodeの16 probeと、実評価Worldの8準備Actionがある。
準備費用0.4をQueue rewardへ含め、訓練probe費用0.8は別項目で表示する。
部分観測ケースの準備は8 drainで、情報が得られずモデルは利用不可だった。
実際のQueue評価はtick8から最大8判断、World taskの終了tick32とは異なる。
Goal6の達成とWorldの時間終了を別指標で保存し、Goal達成後の余剰deliveredは
Goal-capped Utilityで除く。SoftwareのRewardはprogress−0.05×実Action数という
宣言した比較値であり、金銭やサービスの実報酬ではない。

Softwareのaction cost0.05、uncertainty cost0.1、Queue期待効用、固定事前p(high)=0.5は
固定前後で変更していない。noiseの保守的adapterだけsupport spread cost1.0を宣言した。
Core Searchはdepth2/nodes12、flat比較はnodes2（Queue3）、両者max_calls64。
探索条件の違いを隠さず、同じnative候補/Verifier/Goalで比較する。
通常の決定論的ケースはseedを変えても同じ結果になるため、一般化性能ではない。
noiseも2seedの有限標本だけで、統計的優位を実証する規模ではない。

## 結果

以下は各2seedの平均。全44ケースunsafe=0、未解決=0。root qualificationの拒否は8件、
独立監査で再計算したGate Preview拒否（Core Searchのhypothetical候補も含む）は14件。

| Software条件 | 方式 | 実Action数 | Goal達成 | Reward |
|---|---|---:|---:|---:|
| workflow | Core Search | 3 | 100% | 0.85 |
| workflow | 短期 | 3 | 100% | 0.85 |
| workflow | 明示H2比較 | 2 | 100% | 0.90 |
| native checkout | 3方式すべて | 2 | 100% | 0.90 |

workflowの短期はImmediate repair→Prepare validator→Integrate validator、H2は
Prepare validator→Integrate validatorを正式Runtimeで実行した。同じ初期payloadと
候補fingerprintで選択が変化し、complete Receiptにまで反映された。既存Core Searchにも
lookaheadがある。今回の差はworkflow Goal adapterの価値比較とoptional sequenceの接続であり、
新しい汎用探索能力、因果理解、Coreに未来比較がなかったことの証明ではない。
通常checkoutでは選択と結果が同じで、sequence計算だけが増えた。

| Queue条件 | 方式 | Goal達成 | delivered | 生Reward | Goal-capped Utility | holding loss |
|---|---|---:|---:|---:|---:|---:|
| high | 既存Planner | 100% | 11 | 9.50 | 4.50 | 0 |
| high | 短期projection | 0% | 0 | −0.40 | −0.40 | 0 |
| high | 学習H3 | 100% | 6 | 5.00 | 5.00 | 0 |
| high | 固定事前H3 | 100% | 6 | 5.00 | 5.00 | 0 |
| low | 既存Planner | 0% | 5 | 3.35 | 3.35 | 0.75 |
| low | 短期projection | 0% | 0 | −0.40 | −0.40 | 0 |
| low | 学習H3 | 100% | 6 | 3.50 | 3.50 | 1.50 |
| low | 固定事前H3 | 100% | 6 | 2.00 | 2.00 | 3.00 |
| noise | 既存Planner | 100% | 11 | 9.25 | 4.25 | 0.25 |
| noise | 学習/固定事前/保守H3 | 100% | 6 | 5.00 | 5.00 | 0 |

lowの学習H3の投入量列は[3,0,1,1,1,0,0,0]、固定事前は[3,3,0,0,0,0,0,0]。
正式Receiptから学習した分布が、その後の実Actionと滞留費用へ反映された。
noiseでは学習分布と固定事前がともにp=0.5で、通常H3のActionと実利益は同じ。
保守adapterは[1,1,1,1,1,1,0,0]、通常H3は[3,3,0,0,0,0,0,0]となり、
異なるGoal adapterを同じ比較器へ接続して実Actionを変えたが、この2seedで利益差はない。
high/noiseでは既存Plannerの生Rewardとthroughputが新方式を上回った。
Goal-capped値を使った結果と生Rewardを使った結果は別の主張である。
短期projectionは遅延投入の費用だけを見てdrainを続けた。正確な一手予測だけでは
Goalを達成しない反例であり、既存Plannerがそのような失敗をするという意味ではない。

## 誤予測・unknownとcoverage

第一実行stepのみ、queue/delivered平均絶対誤差を採点する。H3 environment-onlyは
以後の閉ループAction列と違うため、それを実H3 Outcomeとして採点しない。
未実行候補にActualラベルを与えない。Action差分MAEもこの閉ループからは算出しない。

| 条件 | 学習H3 MAE | 固定事前MAE | 学習方式の判断round coverage |
|---|---:|---:|---:|
| high | 0 | 0.25 | 100% |
| low | 0 | 0.5625 | 100% |
| noise | 0.25 | 0.25 | 100% |
| high→low（warmup後） | 1.6 | 今回は比較していない | 5/6=83.3% |
| 初期失効/部分観測/未対応sequence | 採点対象なし | 比較していない | 0% |

high→lowでは5実Action、delivered4、reward0.25で、モデル利用不能となった6判断目に
ABSTAINした。Goal未達・unknown1件であり、適応による回復や性能改善の実証ではない。
unknown3条件では意思決定Actionは0、準備以降tickが進まない。古い順位を再利用しない。
unknownだけでMAEを減らす方式を改善として扱わない。

## コスト・監査

固定本評価r2合計CPU62.45s、wall71.81s（macOS/Apple Silicon、他の検証と並行実行）。
プロセス累積max RSSは137,183,232 bytesで、ケース個別増分メモリではない。
速度比較ではなく参考測定。Queue学習H3は1case平均CPU2.69–2.74s/wall3.03–3.05s、
既存PlannerはCPU1.05–1.06s/wall1.22s。新比較は追加コストを持つ。
各8roundのQueue H3は提案側ローカル24計算、Runtime Verifier48回。
Software workflowではH2側14計算+Verifier4回、短期5計算+Verifier5回、Core Searchは
Runtime推論11回+Verifier11回。native checkoutではH2側16計算+Verifier4回、
短期4計算+Verifier4回で、結果が同じなのに計算が増えた。
API calls/USDはローカル構成で0、CPU等の金額換算はunknown/nullとして保存する。
訓練・準備の正式Actionも各summaryに別記し、CPU/wallには含める。

[結果JSON](../reports/modular-deliberation-v1-results.json)と
[独立監査JSON](../reports/modular-deliberation-v1-audit.json)を保存した。
監査44ケース、意思決定/準備のcomplete Receipt486件。別訓練Receipt512件もモデルを
再学習して検証した（総実行998件）。公開JSONは数値とAction名/fingerprintの要約のみ。
source hashは全Python sourceとbenchmark script、auditor hashとprotocol hashは別途追跡する。
監査はsaved success/qualifiedフラグに依存せず、モデル・予測・native全leaf・価値・
独立Verifierを再計算し、最終Authorization/Intent/Receipt/Actualを照合した。

pilot-v1のreward owner誤参照とprefix list共有は新outputで修復した。pilot-v2の
通常checkout controlとnoiseを確認してからprotocolを固定した。本評価初回の監査後、
報告項目のRuntime inference/Verifier混同を修正し、元結果を上書きせずr2として再実行。
重み/条件/seedは維持し、44ケースの実Action列・Rewardは初回と完全一致した。
既存protocol・過去results/auditを変更していない。

## 安全性と残る課題

28新規テストは同一候補の短期/sequence実選択、H3有限lineage、Memoryの実経験のみ、
高価値候補への独立vetoと次点実行、予算不足、State/Action/version/lineageの不整合、
Store更新、Health/anchor/provenance変更、例外・取消、saved score/continuation/Gate/
Authorization/Outcomeの改ざん拒否を含む。
通常checkoutでは危険な既存patch候補が拒否され、別のqualified候補が実行された。
結果JSONのgate_rejectionsはopt-inのroot qualificationだけを数え、Core Searchには
この記録がない。監査JSONのGate Preview件数を別途示し、0を安全性の証明としない。
予測上の最高価値候補へのvetoと次点実行は専用fixtureテストでも確認した。
Core/Runtime/Gate/Registry/既存Guard/Recovery/Promotionのコードや契約は変更していない。

未校正のsoft uncertainty、iid/定常性仮定、partial observation、Policy選択バイアスを
解決していない。Queueのexplicit sequence、stochastic Software State合成、Physical/Blender
統合、外部API/generative model、モデル自動昇格は未対応。新しい大規模WorldModelや
第二Runtimeはなく、trusted in-process adapter/selectorをOS sandboxと呼ばない。
次は実測coverageを持つ異種モデルadapter、不確実性の校正、独立したH3反実仮想評価を
検討する。今回の結果だけから長期計画、因果理解、一般的な優位は主張しない。
