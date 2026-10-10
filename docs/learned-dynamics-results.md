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

## 2026-10-10：3 tick Action比較の追加評価

PR #8の上記結果・protocolは保持した。今回の専用成果物は
[queue-temporal-v1.json](../benchmarks/queue-temporal-v1.json)と
[集計結果](../benchmarks/queue-temporal-v1-results.json)。設計判断とAPIは
[Dynamics設計](learned-dynamics.md#2026-10-10-承認判断学習した能力分布による時間的action比較)に追記した。

```bash
uv sync --python 3.12 --frozen --extra dev --extra physical --extra sandbox
uv run python scripts/benchmark_queue_temporal.py --output .cache/new-temporal-run
uv run python scripts/audit_queue_temporal.py .cache/new-temporal-run
```

新しい出力pathが必須。raw DB・モデル・ケース・manifestはprivate cacheのみ。
実行系pilot（12学習/126評価遷移、6 paired比較）成立後、固定protocolで本評価。
学習seed11–14と評価seed101–104を分離し、episode/run/receiptもrequire_disjointで確認。
安定low、noisy low、tick8のlow→highの3条件、開始tick0/8、4seed、3Actionで24 paired比較。
評価は504確定遷移（288 prefix probe＋216 root/follow-up）、学習は96確定遷移。
prefixは同一seed・同じprobe系列で初期状態を再現するための実行であり、学習モデルを更新しない。
すべてのroot/follow-upもVerifier/Gate/Authorization/intent/receipt経由。
今回の初期queue/pendingは空。非空pendingは単体テストで検証したが、本評価の一般化対象ではない。

### 有効観測数と学習の寄与

| 学習総数 cell.count | 有効能力観測 | probe / ordinary |
| ---: | ---: | ---: |
| 0 | 0 | 0 / 0 |
| 6 | 4 | 2 / 2 |
| 24 | 16 | 8 / 8 |
| 96 | 64 | 32 / 32 |

識別不能な観測は低能力に補完しない。未学習モデルはcoverage0%、MAEはnull。
有効数4未満のunknownも自動テストで確認した。今回の6/24/96件モデルは24比較でcoverage100%。
これは今回の公開schema・仮定内の支持率で、未知の世界へのcoverageではない。

既知の到着規則・保存則・列挙経路・初期状態・Actionを同一にし、固定p(high)=0.5と
学習分布のみを比較した。2・3 tick後のdelivered MAEを状態誤差とする。
この評価では全投入が到着済みでqueue+deliveredが保存されるため、queue MAEとも一致する。
差分誤差は3組のActionペアのdelivered差を同じ外生経路・実条件で照合した。

| 条件 | 固定事前：状態 / Action差分 MAE | 学習96件：状態 / Action差分 MAE |
| --- | ---: | ---: |
| 安定low | 0.291667 / 0.583333 | **0.030884 / 0.061768** |
| noisy low | 0.250000 / 0.500000 | **0.136719 / 0.273438** |
| low→high全体 | 0.250000 / 0.500000 | 0.250000 / 0.500000 |
| low→high変化前（開始tick0） | 0.291667 / 0.583333 | 0.030884 / 0.061768 |
| low→high変化後（開始tick8） | **0.208333 / 0.416667** | 0.469116 / 0.938232 |

学習の寄与は安定・noisy条件で確認できたが、shift全体で改善せず、変化後には悪化した。
全期間平均は改善と悪化を相殺するため、固定条件の開始tick別集計も併記する。
6件モデルは安定lowで誤差0、24件では0.080729、96件では0.030884だった。
有効標本4件で高能力を観測しなかったモデルが、この小規模held-outでは偶然よく当たった。
学習量の単調改善や統計的な一般優位は主張しない。

既存InformationForecastは評価episodeの過去の確定receiptを使う一手先予測として併記し、
216予測のprocessed MAEは0.136944。実観測を各tickで取り込む情報条件・予測期間が異なるので、
上の3 tick予測との勝敗には使わない。既存方式に未実装の3 tick能力を補っていない。

### 動作例と不確実性

正式probeだけのlow能力6件から学習したモデルでは、空queueからsubmit(3)のdelivered曲線が
`[0,1,2]`、high能力6件のモデルでは`[0,3,3]`になることを実行テストで確認。
投入の遅延構造は既知規則であり、経験で変わったのは処理能力分布による予測値。
これを到着規則の学習・因果理解・汎用世界理解とは呼ばない。

全1/3経路のsupport範囲は全条件で実測を100%含むが、既知の能力範囲を全列挙した結果であり、
学習や確率校正の実証ではない。Wilson区間と平均曲線のparameter sensitivityもiid仮定付き。
非公開shiftは公開Stateから事前識別できず、unknownへの自動切替はない。
schema/provenance/時刻余裕/有効数/予算/推論が対応範囲外ならunknownを維持する。

### 計算コストと監査

Python3.12.13 / macOS arm64。96件fitはCPU0.10565秒 / wall0.11807秒。
authority再検証を含むので、少数件fitにも約0.1秒かかった。
72予測（24比較×3Action）のCPU/wallは、固定事前31.38/31.37ms、学習96件34.60/34.60ms。
学習版はCPU約10.3%増。各方式72 engine calls / 576経路で列挙予算は同一。
比較は別のopt-in分析経路であり、標準Runtimeに余分な予測を追加していない。
全benchmarkのwallは19.75秒、再実行20.07秒。これは全収集/学習/分析の時間で、
agentの意思決定改善やepisode高速化の比較ではない。

新DBで2回実行し、全スコア・開始tick別スコア・予測曲線・実State traceの意味的hashが一致。
各回の96/504遷移、4モデル、24比較を元receiptから独立監査し、fit、mask件数、
予測、Action差分、実行順序、Gate、公開集計を再検証した。
ランダムID/timestampにより非空artifact hashは変わるが、同じDatasetでの再fitは一致する。
時間測定の厳密な再現・独立認証は主張しない。4評価seedの小規模CPU実験である。

Core/Gate/Memory/既存Trainer・Adapterの変更はゼロ。予測はINFERENCEでmandatory checkなし。
既存経路と新エンジンを追加した固定Action fixtureのAction/実State/checks/報酬が一致。
楽観モデルでもVerifier不足・危険候補では実行せず、予測だけでは経験を生成しない。
意思決定性能の改善は測定していない。

## 2026-10-10：PR #9レビュー後の非空queue/pending追加評価

上の空初期状態のprotocol・結果は変更していない。そこでは事実上submit(3)だけが能力依存し、
Action差分MAEは状態MAEの2倍になる。追加成果物は
[queue-temporal-nonempty-v1.json](../benchmarks/queue-temporal-nonempty-v1.json)と
[追加集計JSON](../benchmarks/queue-temporal-nonempty-v1-results.json)。
同じbenchmark/auditorを拡張し、Core・Gate・既存Trainer・Engineを変更していない。

```bash
uv run python scripts/benchmark_queue_temporal.py \
  --protocol benchmarks/queue-temporal-nonempty-v1.json --output .cache/new-nonempty-run
uv run python scripts/audit_queue_temporal.py .cache/new-nonempty-run \
  --protocol benchmarks/queue-temporal-nonempty-v1.json
```

### 条件・実観測の来歴

小規模pilotの8比較/204評価遷移で実行と安全性を確認した後、追加protocolを固定した。
3環境×4seed×準備probe 0/8 tick×仕事prefix 2種類＝48 paired比較。
仕事prefixは正式submit(3)を1回、または2回。全準備Actionも通常Runtime→Verifier→Gate→
Authorization→durable intent→実行→再観測→complete receiptを経る。
直接queue/pendingを書き換えない。root Action後も正式drainを2回実行する。
同一seed・prefix・外部条件で各Action branchを独立に作り、初期payload/provenanceを照合した。

全48初期状態にpendingがあり、19状態にはqueueもある。準備792＋root/継続432＝
1224確定評価遷移、実行Verifier calls2448、unsafe0。初期tickは1/2/9/10。
3 tickを確保するためepisode長16、安定/noisy条件はshift16、変化条件はshift8とした。
行動・capacity12・Policy（width1/max_nodes1/max_calls32）・予測の各Action8能力経路は共通。
この追加条件の都合で既存空状態評価のepisode設定や結果を変更していない。

学習は従来と同じ別seed11–14の96遷移、評価はseed101–104。
モデル0/6/24/96件の全条件を保存。有効能力観測は0/4/16/64件で、96件中probe32/ordinary32、
p(high)=3/64。準備で得た実績/probe測定を固定学習モデルに取り込まない。
評価episode/run/receiptと学習を分離し、独立監査でauthorityとdisjointnessを再検証した。

固定事前0.5および96件学習分布のどちらも非退化で、全48比較×3 Actionについて
binary service経路によりdelivered曲線が変わる。公開の感度件数は全経路supportの診断で、
p=0の6件モデルについて確率的不確実性を証明するものではない。

### 固定事前と学習分布の比較

状態MAEは従来と同じ2/3 tickのdelivered、差分MAEは3 Actionペアのdelivered差。
Action別にqueue誤差も測り、今回も保存則によりdelivered MAEと一致した。
1/2/3 tick別のdelivered誤差と各初期条件別スコアは追加JSONに保存した。

| 条件（各16比較） | 固定事前：状態 / Action差分 MAE | 学習96件：状態 / Action差分 MAE |
| --- | ---: | ---: |
| 安定low | 1.666667 / 0.812500 | **0.187944 / 0.064280** |
| noisy low | **0.679688** / 0.549479 | 1.077442 / **0.402991** |
| low→high全体 | 1.135417 / **0.822917** | **0.938059** / 0.970372 |
| low→high変化後（8 tick準備＋仕事prefix） | **0.604167 / 0.833333** | 1.688175 / 1.876465 |

Action別の2/3 tick状態MAE（deliveredとqueue）は次の通り。

| 条件 | Action | 固定事前 | 学習96件 |
| --- | --- | ---: | ---: |
| 安定low | submit(3) | 2.281250 | 0.232701 |
| 安定low | submit(1) | 1.656250 | 0.194850 |
| 安定low | drain | 1.062500 | 0.136282 |
| noisy low | submit(3) | 0.964844 | 1.356103 |
| noisy low | submit(1) | 0.675781 | 1.120153 |
| noisy low | drain | 0.398438 | 0.756071 |
| low→high全体 | submit(3) | 1.828125 | 1.750811 |
| low→high全体 | submit(1) | 0.984375 | 0.768115 |
| low→high全体 | drain | 0.593750 | 0.295253 |

今回はAction差分MAEが状態MAEの2倍にはならない。全Actionが能力差の影響を受ける。
学習は安定lowで両誤差を減らしたが、noisy lowでは全Actionの状態誤差を増やした。
低ノイズ学習分布と高ノイズ評価分布の不一致により、既存空状態評価の改善が一般化しない。
一方、共通backlogの処理誤差は複数Actionに共通に現れ、差分で相殺され得る。
状態誤差と差分誤差は別の指標で、片方の改善から他方の改善を推定しない。
shift後は古いlow分布への依存により両誤差が悪化する。全期間平均だけではこの失敗を隠す。

6件モデルは安定lowで0/0、noisy lowで1.145833/0.416667。
24件は安定lowで0.486003/0.178711、noisy lowで0.969727/0.388631。
96件で常に改善するわけではない。4評価seedの小規模実験で、統計的優位は主張しない。
未学習0件はcoverage0%/MAE null、6/24/96件は今回の48比較でcoverage100%。
全binary pathのsupport包含100%は既知範囲によるもので、確率校正や安全証明ではない。

### 実測した非空状態の例

stable_low/seed101、正式submit(3)を2回実行した実Stateは
`tick=2, queue=2, pending=[{due:3,amount:3}], delivered=1`。
ここから正式実行した3 Actionのdeliveredはいずれも`[2,3,4]`だった。

| Action | 固定事前の3 tick予測 | 学習96件の3 tick予測 |
| --- | --- | --- |
| submit(3) | [3.000, 5.000, 6.875] | [2.094, 3.188, 4.281] |
| submit(1) | [3.000, 5.000, 6.250] | [2.094, 3.188, 4.275] |
| drain | [3.000, 4.750, 5.750] | [2.094, 3.185, 4.268] |

固定事前のsubmit(3)−drain差分曲線は`[0,0.25,1.125]`、学習版は
`[0,0.002198,0.012875]`、実差分は`[0,0,0]`。
共通backlogがあると短いhorizonではAction差が実際にゼロになる例も含まれる。
予測が異なること自体ではなく、実観測との誤差で学習の寄与を検証した。

### 再現・監査・コストと限界

新DBで2回実行し、全環境/初期条件/Action別スコアと意味的hashが一致した。
各回96学習＋1224評価遷移、4モデル、48比較を独立auditorで再検証。
prefixのAction系列・最終観測・root入力・receipt連続性・Gate・教師mask・refit・予測・
全集計を照合する。初期条件、Action別スコア、従来スコア、実traceの偽装は自動テストで拒否。
旧v1のmini benchmark/audit回帰も維持した。raw DB/モデル/manifest/casesは公開しない。

Python3.12.13/macOS arm64。推定を返す各方式の144推論は同じ1152 service pathsで、
固定事前のCPU64.29/64.55ms、学習96件70.41/69.98ms（約9.5%/8.4%増）。
96件fit CPU0.11513/0.13214秒、wall0.12743/0.14891秒。
全実験CPU40.83/38.89秒、wall48.62/46.00秒。1回目は全Python回帰、2回目は
Chromium E2Eと一部同時実行しており、時間にはOS負荷の影響がある。
時間の独立認証・高速化・意思決定改善は主張しない。
既存online一手先baselineは432 processed予測MAE0.462361。
情報条件/期間が異なるので3 tick方式の勝敗には使わない。

追加6ケースを含む全Python **543 passed in65.37s**、関連26 passed、
Ruff lint/format170 files成功。frontend contracts byte一致/build成功、
独立worktree API（18333）で既存Chromium6 passed in10.1s。
Core/Gate/学習契約の追加変更なし。学習予測は引き続きINFERENCEで、
mandatory safety check/実観測/実行承認へ昇格しない。
残る課題は分布変化・時間相関とモデル失効、区間校正、意思決定性能の実測である。
