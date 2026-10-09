# Receipt-backed Dynamics Learning：実測結果

## 再現手順と評価範囲

```bash
uv sync --frozen --extra dev --extra physical --extra sandbox
uv run python scripts/benchmark_learned_dynamics.py --output .cache/new-dynamics-run
uv run python scripts/audit_learned_dynamics.py .cache/new-dynamics-run
```

出力先は新しいpathを指定する。raw Ledger・manifest・モデルはprivate cacheへ保存し、
公開するのは[protocol](../benchmarks/learned-dynamics-v1.json)と
[集計結果JSON](../benchmarks/learned-dynamics-v1-results.json)だけ。
過去のqueue/Software/情報獲得protocol・結果は変更していない。

固定protocolは本評価前に作成。学習8 episodes×24tick=192遷移、評価3条件×3seed×24tick=
216遷移。学習seed 11–18、評価seed 101–103を分離し、episode/run/receiptの重複も検査した。
候補順序はseedと現在tickだけで決まり、全実遷移は既存Bound Verifier・Gate・receipt経由。
非公開serviceや将来scheduleはモデル入力・教師・候補順序に使っていない。

学習は低能力優勢（noise=0.05、途中変化なし）。評価は同分布、noise=0.25、12tick目の
low→high変化。最初に成立確認を行った後、同じ固定protocolを新DBで2回実行した。
予測スコア・coverage・Runtime行動/観測/報酬は完全一致した。receipt IDやtimestampが変わるので
非空dataset/model artifactのhashは新しいrunごとに変わる。同じデータ・設定の再訓練では一致する。

## 学習曲線と既存方式

MAEは完了件数の誤差。公開arrivalと保存則により、次queue・次deliveredのMAEとも一致する。
未対応入力に対するengine出力はunknownのまま。全件比較のためだけに、unknownには
**明示的なservice=2の公開midpoint baseline**を当てたスコアを併記する。これは実行時fallback
でも学習予測でもない。純粋な支持入力だけのスコア・支持率はJSONにも残した。

| 方式 / 学習件数 | 安定low MAE | noisy low MAE | low→high MAE |
| --- | ---: | ---: | ---: |
| 未学習unknown＋評価用midpoint / 0 | 0.722222 | 0.597222 | 0.472222 |
| Learned / 24 | 0.694444 | 0.562500 | 0.479167 |
| Learned / 96 | 0.162037 | 0.402778 | 0.493519 |
| Learned / 192 | **0.129101** | **0.355589** | 0.469863 |
| 既存InformationForecast（online） | 0.263060 | 0.402671 | **0.246562** |

| Learned / 192 | 安定low | noisy low | low→high |
| --- | ---: | ---: | ---: |
| 支持入力率 | 94.44% | 95.83% | 94.44% |
| 支持入力だけのMAE | 0.121989 | 0.356556 | 0.497502 |
| 学習時標本範囲への実結果包含率 | 97.06% | 86.96% | 76.47% |

安定lowでは学習によって誤差とunknown率が下がった。この実験で経験から予測が変化したことは
確認できる。一方、**学習件数が増えれば常に改善するわけではない**。shift条件では24→96件で
悪化し、192件でも既存online方式より誤差が大きい。学習した低能力優勢の分布を固定しているため、
変化後の高能力を過小予測する。範囲包含率も低下し、校正済み確率区間とは主張できない。

既存方式は評価episodeの過去のreceipt-backed Memory（最新12件）をonlineで使う。
Learnedは評価ラベルで再訓練しない。この情報条件の違いを含む比較であり、最適化した既存方式を
上回るとの主張ではない。3seed・1設定の小規模結果で、一般化・因果理解・LLM優位は未検証。

## 計算コストとRuntimeの互換性

Python 3.12.13 / macOS arm64。時間はprocess CPUとwallで測定した。
192件のfitはCPU **0.5225秒** / wall **0.5474秒**。毎回全Datasetをreceipt再検証する時間を含む。
0/24/96件のfitもCPU約0.50–0.52秒で、今回の規模では検証IOが支配的。
安定lowの72予測ではLearned/192がCPU **16.73ms** / wall **16.76ms**、既存onlineが
CPU **149.38ms** / wall **164.04ms**。既存側はMemory更新検出・取得を含み、学習側は
既に訓練済みのimmutable artifactを使う。学習費用を含めた同一計算ではない。

Runtime比較は別seed 501の同じlow→high世界、24tick、width=4 / max_nodes=4 / max_calls=32。
3構成とも候補4件・同じ探索/検証予算。既存RuntimeはBound Verifierだけ、opt-in構成はその前に
未学習または学習済みengineを追加した。

| 構成 | engine calls | CPU秒 | wall秒 | 報酬 | unsafe / ABSTAIN |
| --- | ---: | ---: | ---: | ---: | ---: |
| 既存Runtime | 192 | 1.1513 | 1.3732 | 10.6 | 0 / 0 |
| ＋未学習engine | 288 | 1.5047 | 1.7936 | 10.6 | 0 / 0 |
| ＋学習済みengine | 288 | 1.5199 | 1.8024 | 10.6 | 0 / 0 |

Action列・実State payload・checks・metrics・報酬・危険判定は全構成で一致。
再実行でも一致し、既存/学習済みのwallは1.3884/1.8259秒だった。
学習済みengine追加で初回CPU **32.0%増**、wall **31.2%増**。予測は高速でも、必要なVerifierを
省略せず予測呼出しを追加するので全episodeは遅くなる。**意思決定性能の改善は実証していない。**
target=999は全tick収集用の未達目標であり、この比較を目標達成率改善実験とは扱わない。
小標本・共有ホストの測定なので、時間差を一般的な性能保証には使わない。

## 安全性の証拠とチェック

- Python全テスト **517 passed in 61.96s**（既存497＋新規20）。
- Ruff lint / format check成功（165 files）。frontend contracts再生成はbyte一致、build成功。
- 既存Chromium E2E **6 passed in 8.2s**。
- wheel/sdistのdistribution integrity成功、各76 Python sourcesを照合。
- 新しい独立auditorが2runそれぞれの192学習/216評価遷移、4モデルの統計、予測スコア、
  Runtimeの正式authorization・確定receipt・実観測を再読出しして成功。
- learned predictionはINFERENCEでmandatory_checksなし、success/riskはunknown。
  誤予測、Verifier不足、予算不足、危険候補でGateを迂回しない否定テストを追加。
- pending/aborted/forged outcome、未実行branch、時刻逆転、外部receipt-only変更、
  取得途中更新、キャンセル、実行例外から教師・古いモデルfallbackを作らないことを検証。
- 同じ汎用Trainer/Engineを実際のSoftware Worldの2遷移でも検証。Software予測精度の評価は未実施。

Core・Gate・Evidence・Runtime・Memory・既存queue行動仕様の差分はゼロ。
依存追加なし。初期テストの失敗は新規fixture/async記法の誤りで、修正後に再実行した。
既存テストは削除・弱体化していない。

## 次の優先課題

1. 時間分割での再訓練・drift検出・model失効/rollback。shift失敗を先に扱う。
2. 未見入力のcoverage拡張と、部分観測を扱う表現。現在はexact cell、一stepの観測deltaだけ。
3. 独立held-outでの不確実性校正と確率rollout。標本範囲を安全証明に昇格しない。
4. Software用adapterと意味のある予測指標を拡張し、初めて意思決定価値を検証する。
5. execution reconciliation/安全な再開とSandboxの強化。

latent/因果表現・intervention・multimodal fusion・分散学習はその後。
将来のモデルも観測authorityにはならず、VerificationとGateを保持する。
