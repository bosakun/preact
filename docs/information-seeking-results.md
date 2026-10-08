# Information-seeking Action v1 — 固定protocolの実測と反例

2026-10-09。**本評価ではVOI方式のNo Probeに対する改善を実証できなかった。**
外部測定をGate経由で選択し、確定receiptから学ぶループは動作したが、
通常処理が提供する情報に対する追加利益は小さく、測定費用と候補評価費用が増えた。
事前に宣言した不利な条件も含め、条件・モデルを結果に合わせて選び直していない。

## データと再現性

- [設計・モデル仮定](information-seeking-action.md)
- [pilot protocol](../benchmarks/information-seeking-pilot-v1.json) と
  [探索的pilot結果](../reports/information-seeking-pilot-v1.json)
  （修正後の同条件[再実行](../reports/information-seeking-pilot-v1-reproduced.json)も保存し、報酬・probe数が一致）
- [固定main protocol](../benchmarks/information-seeking-v1.json) と
  [全seed・反復・予算対照JSON](../reports/information-seeking-v1-results.json)
- [原receipt/Gateと集計の再監査](../reports/information-seeking-v1-audit.json)

mainは9条件×3seed（10/11/12）×4方式×2反復=216 episodeと、
No Probeの旧探索予算対照27 episode、合計243 episode。
各episode24tick、target35（情報不要条件のみtarget0）、同じseed/環境/目標/予算。
能力は1/3、変化tick12、noiseは通常0、noisy条件のみ0.25。
測定費用0.05/2、保持費用0.25/1。モデルは全条件で同じ事前0.5・持続率0.9・horizon6。
環境のshift/noise/scheduleをPlanner/Engineへ渡していない。

protocol SHA256:
`7df14764721f7ce8e3cb21484690a3d25caa22c2a1e41f412e9300e415417d30`。
pilotの探索後、main開始前に固定した。途中、観測の入れ子共有を見つけてdomainのdeep copyを
修正し、測定をSIGINTで停止した。モデル・条件・protocolは変えず、最終sourceで新規outputへ
全243 episodeを再実行した。途中測定は公開集計に混ぜない。

全108ペアでPrediction/Evaluation/Gate、行動、実観測、報酬、危険判定、
確定receipt由来学習の正規化トレースが一致。ランダムID・timestamp等を正規化する前に、
original Gate evidence hash、receipt全文、入力State・Action指紋を照合した。
追加auditorで全source/protocol hash、coverage、費用、再現性、集計も再計算した。
27予算対照はpolicy hashのみ設定差を保持し、Ledger seqを出所eventへ写像したうえで
全意味トレースが一致した。source reference自体を捨てる正規化はしない。
raw DB/events/traceはprivate `.cache` に保存し、PRには集計JSONとhashだけを含める。

## 報酬比較

下表は初回反復の3seed平均。報酬はholding/submission/probe費用を含む。
ΔはVOI−No Probe。全条件でunsafe=0、ABSTAIN=0。

| 条件 | No Probe | Periodic | Uncertainty | VOI | VOI Δ | VOI probe数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| stable high | 36.000 | 35.700 | 35.850 | 36.000 | 0 | 0 |
| stable low | 13.700 | 14.500 | 14.500 | 13.700 | 0 | 0 |
| high→low | 33.000 | 29.700 | 32.100 | 33.000 | 0 | 0 |
| low→high | 31.500 | 29.350 | 33.200 | 31.500 | 0 | 0 |
| noisy | 30.983 | 26.550 | 29.417 | 30.967 | −0.017 | 0.333 |
| high probe cost | 33.000 | 18.000 | 28.200 | 33.000 | 0 | 0 |
| no decision change | 0 | −0.300 | −0.300 | 0 | 0 | 0 |
| holding=1, low probe cost | 36.000 | 35.700 | 35.900 | 35.950 | −0.050 | 1 |
| holding=1, high probe cost | 36.000 | 24.000 | 32.000 | 36.000 | 0 | 0 |

VOIの27 paired seed条件で有利なケースは0、23同等、4悪化（許容差1e−9）。
条件平均では7同等、2悪化。VOIとNo Probeの全条件平均報酬は27.791/27.798。
測定コストを含むため、小さい負の差も残す。
周期probeはstable lowで+0.8、uncertainty方式はlow→highで+1.7となったが、
high probe costではそれぞれ−15/−4.8。VOIはこの高費用条件で測定を避けた。

成功率はNo Probe/VOI/Uncertaintyが24/27、Periodicが23/27。
stable lowでは上限24処理に対してtarget35なので全方式が未達。
noisyのPeriodicはseed12でthroughput33、目標未達。これを除外していない。
全方式とも全243 episodeで24tickを実行し、危険な結果は0だった。
このfixtureでの結果であり、一般環境の安全性の証明ではない。

## 処理量・損失・推定・検出

| 条件 | No Probe throughput / holding loss | VOI throughput / holding loss | No Probe / VOI 能力MAE | 変化検出遅延 No Probe / VOI |
| --- | --- | --- | --- | --- |
| stable high | 40 / 0 | 40 / 0 | .429 / .429 | 対象外 |
| stable low | 23 / 7 | 23 / 7 | .273 / .273 | 対象外 |
| high→low | 40 / 3 | 40 / 3 | .470 / .470 | 2 / 2 tick |
| low→high | 40 / 4.5 | 40 / 4.5 | .359 / .359 | 1 / 1 tick |
| noisy | 39.333 / 4.417 | 39.333 / 4.417 | 1.008 / 1.003 | [4,6,null] / [4,6,null] |
| high probe cost | 40 / 3 | 40 / 3 | .470 / .470 | 2 / 2 tick |
| no decision change | 0 / 0 | 0 / 0 | 1 / 1 | 対象外 |
| holding=1, low cost | 40 / 0 | 40 / 0 | .388 / .355 | 対象外 |
| holding=1, high cost | 40 / 0 | 40 / 0 | .388 / .388 | 対象外 |

MAEは実行前の能力期待値と実行tickの能力との差で、評価器だけが実行後にscheduleを読む。
検出は変化後の生成平均から±0.5内に3連続予測が入る最初の時点。nullは未検出／対象外。
予測が一度当たっただけで検出成功とはしない。ノイズ条件は小標本で検出打切りも多い。

VOIは初回27 episodeで4回probeを実行したが、次のpublic Stateで直前probe Experienceを
除いた場合との最良通常行動の差は全件0だった。これはmodel上の条件付き行動変更確率と
実際のsampleの違いを示す。比較はprobe Experience全体を除くため、通常処理情報も除かれる。
sensorだけの因果的寄与を単独推定した指標ではない。
holding=1/低費用の初期modelはnet VOI≈0.10924、低能力sampleなら後続通常行動を変えると
判断した。しかし実環境はhighで、No Probeも通常処理から早期に十分な情報を得たため、
測定は精度を少し改善しても行動を改善せず、費用0.05だけ失った。

探索pilotの保持費用1・8tick high→lowではVOI reward2.15、No Probe−1.2（+3.35）、
周期4.1だった。これは探索的な一条件・一seedの結果で、mainの改善実証へ昇格しない。
測定と1tickの投入延期を分離した因果ablationも未実施である。

## 実行・計算費用

全方式でwidth4/max_nodes4/max_calls32。No Probeは3候補、他方式は4候補。
通常3候補の順序は同じbinary controllerが決める。No Probeも共通の決定計算を行い、
probeだけ候補から除く。凍結v1 EMA結果との直接比較ではない。
予算を増やしたことだけがNo Probeの行動を変えないよう、width3/nodes3/calls24の
追加対照27 episodeを独立に実行し、実観測・学習・Gate判断の一致を確認した。

| 指標、1 episode平均 | No Probe | Periodic | Uncertainty | VOI |
| --- | ---: | ---: | ---: | ---: |
| World fresh observation回数 | 169 | 169 | 169 | 169 |
| Memory retrieve / infer回数 | 169 | 193 | 193 | 193 |
| proposal回数 | 24 | 24 | 24 | 24 |
| engine calls | 216 | 288 | 288 | 288 |
| VERIFY escalation | 144 | 192 | 192 | 192 |
| ABSTAIN | 0 | 0 | 0 | 0 |
| Belief reuse hit | 0 | 0 | 0 | 0 |
| episode CPU秒 | 1.418 | 1.755 | 1.749 | 1.745 |
| episode wall秒 | 1.641 | 2.039 | 2.043 | 2.032 |
| proposal計算CPU秒 | .0855 | .1024 | .0914 | .0885 |

VOIはCPU約23.1%、wall約23.8%増。候補4個を毎回検証することでcallsは33.3%増え、
probeを実行しないroundにも費用が発生する。mandatory検証の遅延・省略は実装していない。
観測は全方式で7×24+1。fresh observationやMemory確認を速度のために省略しない。
CPU/wallは同じhostの初回反復平均で、反復2の個別時間もJSONに残す。
OS・順序・通常の開発作業による変動があり、性能差の統計的有意性は主張しない。
原receipt/Gateの再監査時間とファイル出力時間はepisode計測外。

## テストと安全境界

全Python `470 passed in 51.02s`（既存443＋新規27）。新規テストはprobe contract、
tick/費用/既存処理、private scheduleのアクセスtrap、予測と測定の分離、
Gate/intent/complete receipt、検証・予算不足のABSTAIN、unsafe bound拒否、
既知probe sampleでもfuture verifierを代替できないこと、pending/aborted/forged outcome、
外部writer更新、receipt dedup、時点減衰、失敗sample、reset、新しい設定、
reuse opt-out、推定失敗・実行失敗・キャンセル、v1通常行動等価、観測deep copy、
実episode再現・予算対照・偽policy hash拒否を確認した。
既存Core/Gate/Runtime/Memoryとそのテストは変更・削除・弱体化していない。

Ruff lint/format（151 files）、frontend contracts再生成byte一致、frontend build成功。
既存Chromium E2Eは6 passed in8.6s。wheel/sdist integrityは70 Pythonソース、
ライセンス・必要ファイル・private cache排除を照合して成功。これはarchive integrityで、
外部クラウドやGPUでの実行検証ではない。
初回テストのcounter属性名/import誤り、監査のstakes/default数値型/seq正規化の不備は修正した。
benchmark protocolを実測結果へ合わせる修正ではない。

## 次の研究

持続率・有限horizon・打切り観測のモデル誤指定がVOIをどう狂わせるかを、別の固定protocolで調べる。
費用と投入延期を一致させた非測定drain対照で、sensor情報の寄与を分離する。
情報が行動を変える条件を事前に構成し、複数の独立環境へ移す。
新しい結果を今回のprotocolへ混ぜず、No Probeに勝てるかを改めて反証可能に評価する。
Lazy Verification、LLM Planner、Vector DB、Neural World Model、汎用Memory、
自発的目標生成には今回着手していない。
