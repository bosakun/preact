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

## Dynamics drift / Safe model invalidation v1（独立評価）
旧PR #8/#9のprotocol・結果を維持し、`benchmarks/queue-drift-v1.json`を別途固定した。
小規模pilot（seed201、3条件）は24比較/144実遷移の独立監査に成功後、本評価へ進んだ。
学習seed11〜14、評価seed101〜104、8条件×4 episode、各64tick。学習はlow/high各96遷移、
有効能力標本各64（low high-count3、high high-count61）。予測はroot通常Actionと
2回の実drainに対応する3tickだけを採点する。各方式の実Action列・Runtime・Verifier予算は同じ。
学習/評価episode・run・receiptを分離し、モデルは更新せず固定する。thresholdは本評価後に変更しない。
window16（8有効観測から）、学習有効数16以上、割合差0.10、alpha_k=0.05/(k*(k+1))。
観測が識別不能な場合に低能力教師を補完しない。

| 条件 | 検知 | 遅延tick / 有効標本数 | 固定MAE | 監視付きsupported MAE | coverage / unknown | 失効抑止数 |
|---|---:|---|---:|---:|---|---:|
| stable_low | 0/4 | — | 0.129311 | 0.140479 | 75.0000% / 25.0000% | 0 |
| stable_high | 0/4 | — | 0.069743 | 0.082330 | 75.0000% / 25.0000% | 0 |
| low_to_high | 4/4 | 11〜21 / 6〜11 | 0.580307 | 0.564594 | 34.3750% / 65.6250% | 26 |
| high_to_low | 4/4 | 11〜21 / 6〜11 | 0.553317 | 0.527462 | 34.3750% / 65.6250% | 26 |
| noise | 4/4 | 15〜15 / 8〜8 | 0.527921 | — | 0.0000% / 100.0000% | 48 |
| partial_low_to_high | 0/4 | — | 0.000000 | — | 0.0000% / 100.0000% | 0 |
| stable_ordinary | 0/4 | — | 0.129311 | 0.179835 | 50.0000% / 50.0000% | 0 |
| ordinary_low_to_high | 4/4 | 19〜31 / 5〜8 | 0.580307 | 0.857677 | 23.4375% / 76.5625% | 17 |

MAEはqueue/deliveredの3tick平均絶対誤差で、supportがある予測だけを採点する。
固定モデルのcoverageは全条件100%。全unknown方式は全条件coverage0%、unknown100%、MAEは未定義。
監視付きの予測値は利用可能な間、固定モデルと同じ。**同じ採点集合の固定MAEは監視付きMAEと完全一致する。**
異なる集合のMAEを比較して予測能力の改善とは主張しない。通常実績急変の全体supported MAEは
0.580307→0.857677と悪化しており、その結果を保持する。noiseでは最初の利用可能判定時に失効し、
coverage0%で全unknown方式と同じである。partialでは固定方式の誤差が0でも監視は教師を識別できずunknownとなる。
検知・予測抑止・精度/coverageを分離した評価であり、ランキング/タスク成功率の改善は測定していない。

安定low/highと通常実績の計12 episodeで誤失効0（観測範囲の結果、普遍的false-alarm保証ではない）。
probe併用急変8/8、通常実績急変4/4、noise4/4で検知、部分観測急変4/4では未検出。
noiseはlow .05学習から .5環境へのdeployment初期不一致としてchange_tick0で採点した。
既定のprobe併用急変は11〜21tick（6〜11有効観測）、通常実績のみでは19〜31tick（5〜8有効観測）。
検知tickは初めて有効標本で失効条件を満たすafter tick。実際の予測抑止は次の4tick周期の予測入口からで、
そのcutoffは結果JSONのfirst_suppressed_cutoffsで別に記録する。未検出episodeの遅延を0で補完しない。

事前固定の感度評価: window8ではprobe併用急変4/4を各方向tick33で検知、
window32ではtick37〜49、alpha0.01ではtick37〜47となった。通常実績だけの急変は
alpha0.01で3/4検知（1/4未検出）。学習16有効標本の有限比較はprobe急変各3/4、noise1/4、
通常実績急変0/4と弱まった。学習4有効標本は全条件insufficient_data。
これらは同じ評価traceで有限標本/window設定を変えた統計的感度分析で、学習モデルの再昇格・予測更新ではない。
既定設定を有利な感度設定へ変更していない。

独立auditorは2240実遷移（学習192+評価2048）、512比較を再検証する。
Fisherの両側p値を別のhypergeometric列挙で検査し、既存receipt検証、モデル再fit、
fixture seedによる実結果、current cutoffのHealth再構築、Engine view version、
失効時の空vectors/INFERENCE、感度分析、MAE/coverage/検知遅延を再採点した。
監査のfixture schedule再現は評価oracleに限定し、Learner/Guard/Engineに渡さない。
raw DB・receipts・モデル・manifest・casesは.cache内のみ、公開は集計JSON。時間の独立認証は行わない。

再現コマンド（毎回新しい出力先）:

```bash
uv run python -m scripts.benchmark_queue_drift --protocol benchmarks/queue-drift-v1.json --output .cache/drift-run-A
uv run python -m scripts.audit_queue_drift --protocol benchmarks/queue-drift-v1.json --output .cache/drift-run-A
uv run python -m scripts.benchmark_queue_drift --protocol benchmarks/queue-drift-v1.json --output .cache/drift-run-B
uv run python -m scripts.audit_queue_drift --protocol benchmarks/queue-drift-v1.json --output .cache/drift-run-B
```

時間相関/緩やかな変化は今回のfixtureで実測していない。Fisherのiid標本仮定、
通常観測の検閲・失敗による偏り、少数標本と逐次alpha減少による未検出・遅延は残る。
取得/推論コスト、future timestamp、外部更新、pending/aborted/forged、reset、キャンセル、
監視対象外の直接利用とscope所有契約は設計文書に記載した。

最終sourceの2回の固定条件実行で、実Action/可視trace、予測vectors、Health判定、検知tick/有効数、
MAE/coverage、感度分析が一致した。意味的hashは
`43559916bd5ce884d2a26f3a10bbf6170bffedf7eaf3b00048160e08a79eb626`。
raw receipt ID/timestamp/model versionは実行ごとに異なり、意味的比較では正規化した。
結果は`benchmarks/queue-drift-v1-results.json`に集計のみを保存する。

Python3.12.13/macOS arm64、512予測/方式/実行。CPU平均/比較は固定0.586/0.579ms、
監視付き335.724/328.768ms、wall平均は固定0.585/0.578ms、監視付き371.792/364.794ms。
全実験CPU253.077/248.672s、wall284.528/279.985s。
最終sourceの両実行とも他の重い検証を同時に行わなかった。
OS負荷の影響と計測の独立認証がないことは残る。
**約570倍のCPU増加**であり、高速化の主張はできない。
全Ledger/receiptを繰り返し検証し、固定学習統計を再fit照合し、look履歴を再構築する
安全側の初期実装による負担である。既存の直接Engine/APIはこのopt-inコストを負わない。
次にreceipt-backed更新検出と固定prefixの派生キャッシュを活かした増分検証を検討する。

失効後の予測採用は117件すべて抑止、実遷移unsafe0、Runtime検証engine calls4096/実行。
新機能はActionを実行せず、Gate/Authorization/Intent/Receiptの意味を変更しない。
実観測の再取得とreceipt検証を省略する最適化は行っていない。

時点監査で観測生成とoutcome記録の間を区別する必要を見つけ、outcome eventの記録
timestampもcutoff以下であることと未来timestamp拒否を追加した。これを含む最終sourceで
2回再実行し、前の結果との意味的hashも一致した。閾値・protocol・条件は変えていない。
記録timestampとread時のcomplete receiptを併用する。DBの物理commit-clock履歴や
暗号学的World認証を追加したわけではなく、既存append-only StoreとWorld所有契約の範囲である。

最終Python **573 passed in89.45s**（新規30ケース）、Ruff lint/format176 files成功。
frontend契約再生成byte一致/build、専用worktree API（18333）のChromium6 passed in8.0s。
既存64 benchmark JSONとCore/Trainer/TransitionDataset/Adapter/QueueTemporalEngineの変更なし。

最終wheel/sdistのdistribution integrityは全80 Python sourcesのbyte一致と
private/generated path除外に成功。配布物のインストール後実行や秘密情報検出の保証は対象外。

## Model Recovery & Safe Model Promotion v1

今回実証するのは、**失効後の新しい実経験から学習し、別の実episodeで評価・監視準備を
行って、明示的な切替で3tick予測を再開する能力**である。同じWorldのqueue/pendingを
保持する連続的更新、Actionランキングやタスク成功率の改善は実証していない。
固定protocolは`benchmarks/queue-recovery-v1.json`、公開集計は
`benchmarks/queue-recovery-v1-results.json`。PR #8/#9/#10の結果は変更していない。

### 固定条件と独立性

学習16有効件、評価16有効件、Action比較8組、状態/差分MAE各0.5以下、
固定事前との悪化許容0.05、旧A比状態MAE10%改善、coverage100%、新Health availableを
本評価前に固定した。pilotはseed201、本評価は101〜104、比較用301〜304、
旧A学習11/12。8条件の全seedを実行し、閾値やseedを結果に合わせて変更していない。

Aの学習は実probe16件。元source episodeの全履歴を検証し、最初の失効観測を
`begin`で固定してからB用の16実probeを追加する。suffixのepisode IDは元のまま。
需要不足・測定失敗を能力低/ゼロの教師へ補完しない。教師とoutcomeの確定時刻も確認する。

昇格前評価は各比較seed×2 workload（pending、queue+pending）の8組。
各組のsubmit(3)、submit(1)、drainは、同じ外生fixture条件を持つ3つの実Worldで実行する。
準備submitもRuntime/Receiptを通し、rootの3tick予測を記録してからroot+2drainを実行する。
各branchは実際のtick0からの完全manifestを持つ。学習/評価/新監視はepisode/run/receiptで
分離し、prefixの準備実績を評価の有効標本数に含めない。
有効評価数はhigh36、low60。学習・評価の標本を新Healthへ流用しない。

評価完了後、さらに別の監視episodeで8probeと2submitを正式実行する。
昇格はその新scopeのavailable Healthを検証してから行う。昇格後の比較にも
3つの別実Worldを用い、submit(3)/submit(1)/drain+2drainの実結果を採点する。
このデータは候補選択・昇格前合否判定に使わない。Learner/Engine/Lifecycleへ
隠されたサービスscheduleを渡さず、fixture再生は独立auditorだけが行う。

### 昇格成功と拒否

| 条件 | 4 seedの結果 | 根拠・限界 |
|---|---|---|
| low→high | 4昇格 | 最初の失効tick18、B有効学習16 |
| high→low | 4昇格 | 最初の失効tick18、B有効学習16 |
| stable low/high | 各4切替なし | A未失効。不要な回復は開始しない |
| 学習不足 | 4拒否 | 有効4件。候補を作らない |
| 品質不足 | 4拒否 | Bをhighで学習後、lowで独立評価。品質/prior/A比較不合格 |
| 監視不足 | 4拒否 | 独立評価は合格でも、新scopeの有効数不足 |
| noise 0.05 | 4昇格 | 最初の失効tick18/19/19/17。短い有限fixtureの結果 |

全32ケースで12昇格・12拒否・8切替なし。不適切な昇格は、この定義済み不合格条件で0。
未失効8件を除いた24件で昇格率50%、拒否率50%。両方向の通常shiftは8/8昇格。
4477の実遷移、Runtime engine calls8954、unsafe0。ゼロ件はこのfixture内の観測結果で、
一般の安全性保証ではない。失効した24ケースでAの監視付き予測がunknownへ変わることも照合した。

### 精度とcoverage（MAEは仕事単位）

昇格前の8組を各seedで採点した結果は以下。各予測方式のcoverageは100%、unknown0%。

| 独立評価の環境 | B 状態/差分MAE | 旧A 状態/差分MAE | 固定事前 状態/差分MAE |
|---|---|---|---|
| high（low→high） | 0 / 0 | 2.444444 / 1.111111 | 1.000000 / 0.569444 |
| low（high→low） | 0 / 0 | 2.444444 / 1.111111 | 1.444444 / 0.541667 |
| low（品質不合格） | 2.444444 / 1.111111 | 0 / 0 | 1.444444 / 0.541667 |

昇格後の**別集合**（各seedで1組・3Action×3tick）では以下。
旧Aの数値は監視対象外の直接Engineによる比較用予測であり、失効後の実利用ではない。

| 回復先 | B 状態/差分MAE | 旧A直接 状態/差分MAE | 固定事前 状態/差分MAE |
|---|---|---|---|
| high | 0 / 0 | 1.888889 / 1.333333 | 0.736111 / 0.555556 |
| low | 0 / 0 | 3.000000 / 0.888889 | 1.736111 / 0.305556 |

B/旧A直接/priorのcoverage100%、unknown0%。回復なし（失効Guard）と全unknown方式は
coverage0%、unknown100%、MAEは未定義(null)。unknownを誤差0として採点しない。
昇格前後は状態集合が異なるため、両者のMAE減少を同じ対象の改善として比較しない。

noise条件でも今回の4 seedは上記highの数値と同じだった。失効遅延にはノイズの影響が
あったが、切り出した16学習tickには全seedでlow測定がなく、評価/回復後の短い採点集合も
結果としてhigh予測と一致した。したがって、この結果だけでノイズ分布の学習や頑健性を
主張できない。長い独立採点区間、時間相関、緩やかな変化は今後の課題である。

### 回復に必要な実行と計算費用

両方向成功ケースは、source失効後16tick、独立評価の準備込み108実行、
昇格対象の新監視episodeは10tick（8probe+2submit）で準備した。
3Actionの昇格後比較用に監視Worldを3つ用意するため、逐次実験の合計は
16+108+3×10=154実行。これは**episodeをまたぐ実験の実行数**であり、
同じWorldの失効から154tickで連続回復したという意味ではない。

Python3.12.13/macOS arm64で、本評価2実行の全ケースCPU合計425.014/418.620秒、
wall合計480.410/473.791秒。成功ケース平均はCPU21.852/21.496秒、
wall24.608/24.242秒。計測中に重い回帰テスト/ビルドを重ねなかった。

| 成功ケースのstage | 平均CPU秒（1回目 / 2回目） | 平均wall秒（1回目 / 2回目） |
|---|---|---|
| B学習・根拠検証 | 0.292 / 0.287 | 0.324 / 0.320 |
| 独立評価・根拠検証 | 2.109 / 2.064 | 2.330 / 2.284 |
| 明示昇格 | 2.891 / 2.835 | 3.198 / 3.142 |
| 回復した3Action比較 | 2.727 / 2.683 | 3.015 / 2.976 |
| 明示restore | 2.593 / 2.543 | 2.868 / 2.818 |

各入口が完全なauthority/evaluationを再検証・再fitするため高コストであり、
高速化は主張しない。旧の直接Engineや新機能opt-outへこのコストを課さない。
ru_maxrssの最大は138,854,400/144,375,808 bytes（約132.42/137.69 MiB）。
これは各実行process全体の累積high-waterであり、個別caseやモデルの増分メモリではない。
OS負荷と時計精度の影響は残る。合計時間はcaseの合計で、独立auditor時間を含まない。

2実行の判定・予測スコア・実Action/実観測の意味的hashは一致した:
`2395594a376cb299a733ea95b32a72b3c0912a1f6867c51adddb97bda3a9ea3f`。
ランダムrun/receipt/model ID、timestamp、latencyを意味的比較から分離した。

### 再現・監査

毎回新しい出力先を指定する。raw DB/モデル/manifest/receiptsはprivate cacheに留める。

```bash
uv run python -m scripts.benchmark_queue_recovery --pilot --output .cache/recovery-pilot-new
uv run python -m scripts.audit_queue_recovery --protocol benchmarks/queue-recovery-v1.json --output .cache/recovery-pilot-new
uv run python -m scripts.benchmark_queue_recovery --output .cache/recovery-run-new-A
uv run python -m scripts.audit_queue_recovery --protocol benchmarks/queue-recovery-v1.json --output .cache/recovery-run-new-A
uv run python -m scripts.benchmark_queue_recovery --output .cache/recovery-run-new-B
uv run python -m scripts.audit_queue_recovery --protocol benchmarks/queue-recovery-v1.json --output .cache/recovery-run-new-B
```

独立auditorはactual fixture再生・Receipt・時点・suffix・源データの非重複を検証し、
能力分布/3tick列挙と状態/Action差分MAEを別計算する。固定条件の昇格判定、
新Health/view/Registry、Aの抑止、再起動restore、公開集計を再照合する。
Fisherの計算も独立hypergeometric列挙で確認する。計測時間の独立認証は対象外。

取消/commit中のwriter更新ではactiveを利用不可にし、Aへ戻さない。
承認のみの中断はrestore拒否、commit済みの中断でもfresh観測と全根拠の再検証を要求する。
pending/aborted/forged receipt、Artifact破損、正しいJSONでも異なるモデル統計、
データ再ラベル・重複、未来/事後予測、prefix差替えを否定テストで確認する。
実行権限は既存Gate/Verifier/Authorization/Intent/Receiptに留まり、BもINFERENCEである。

単一所有者/append-only Storeの点時点保証、World manifestの所有者責任、
直接Engineは監視外という既存境界を維持する。自動昇格・rollback、分散所有、
同一episode途中anchor、意思決定価値は未実装・未実証。今回のCPU費用を理由に
検証を省略せず、通常の高速化を後続へ残した。

### 最終検証

本評価2実行の独立auditorは各32ケース/4477遷移でpass、pilotも432遷移でpass。
新規27ケースを含むPython全回帰は**600 passed in229.92s**。
Ruff check/format183 files、frontend contract再生成のbyte一致、frontend build成功。
専用API18334と所有済みChromiumの既存E2Eは**6 passed in8.3s**。
wheel/sdist監査は83 Python sourcesの一致・assets/license・private path除外を確認した。
旧67 benchmarkファイル、Core/Gate/Registry/Memory/Trainer/既存Guardへの変更はない。
既存テストは削除・弱体化していない。

初期失敗はTask/Artifactの0と0.0正規化によるhash差、fixtureコピー後のtimestamp差、
否定テストの拒否メッセージ期待の不一致であり、正規化/原時刻保持/期待範囲を修正して再実行した。
E2Eのsandbox内listen制限は許可経路で再実行した。昇格条件を緩める修正はしていない。
通常のmodelとReceiptの不整合、取消、取得中更新は例外を伝播してactiveを破棄する。
完全なOS隔離、暗号学的World認証、DB巻戻し耐性、live外部サービスは今回の監査対象外である。

## PR #11追評価：fresh ObservationのAuthority境界

上記Model Recovery v1の数値と`benchmarks/queue-recovery-v1-results.json`は
commit8051aad時点の修正前記録として保持する。その実装は評価最終Receipt Stateの
timestampを評価cutoffへ置換しており、同時刻の実観測取得を裏付けていなかった。
レビュー指摘に従い置換を除去し、実Runtime/Receipt確定後のWorld.observe()で取得した
final_observationを必須のEvaluationBranch v2へ保存・再検証する方式へ修正した。

protocol、seed、昇格閾値、Action、採点集合は変更しない。実行記録/監査に
`validation_revision: fresh-observation/v2`を付け、別の出力先へ全条件を再実行する。
修正後の公開集計は`benchmarks/queue-recovery-v1-fresh-observation-results.json`。
旧記録を新timestamp/架空のObservationで補完して使うことはしない。

通常条件（noise=0）は決定論的で、異なるseedでも同じサービス列と予測結果になる。
この同値性は再現性/処理経路の確認であり、独立な多様環境への一般化性能の実証ではない。
学習・評価・監視のreceipt/episode非重複はデータ漏洩の防止であり、環境分布の多様性とは別。
既知Dynamicsを持つ限定fixtureと、noise条件の短い標本の限界を維持する。

同一固定protocolの修正後再現コマンド（全て新しいprivate出力先）:

```bash
uv run python -m scripts.benchmark_queue_recovery --pilot --output .cache/recovery-fresh-pilot-new
uv run python -m scripts.audit_queue_recovery --protocol benchmarks/queue-recovery-v1.json --output .cache/recovery-fresh-pilot-new
uv run python -m scripts.benchmark_queue_recovery --output .cache/recovery-fresh-run-new-A
uv run python -m scripts.audit_queue_recovery --protocol benchmarks/queue-recovery-v1.json --output .cache/recovery-fresh-run-new-A
uv run python -m scripts.benchmark_queue_recovery --output .cache/recovery-fresh-run-new-B
uv run python -m scripts.audit_queue_recovery --protocol benchmarks/queue-recovery-v1.json --output .cache/recovery-fresh-run-new-B
```
