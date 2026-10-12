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
