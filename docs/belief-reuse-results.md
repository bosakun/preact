# Safe Belief Reuse — CPU測定結果 v1

**推定回数は減ったが、今回の軽いEMAでは全体速度は悪化した。**
通常構成の推定421→181回（57.01%減）、推定CPU 5.32→2.09msに対し、episode CPUは
3.327→3.411秒（2.52%増）、wallは3.908→3.993秒（2.16%増）。既定値は無効のまま。
これは推定再利用の実装・挙動一致を示す実験で、汎用認知能力やLLMへの優位性の実証ではない。

## 条件と再現

2026-10-09、Python 3.12.13、macOS/arm64、CPUのみ。外部API・GPUなし。
公開main `1f2604c`の推定経路を保持したdisabled構成と、新しいopt-in構成を比較した。
ベースラインは同じ改修checkoutの`reuse_beliefs=False`で、従来の直接infer経路を通る。
別main checkoutとのwall比較ではない。Core/Store/既存Plannerの推定アルゴリズムは同じ。

専用[protocol](../benchmarks/belief-reuse-v1.json)に固定した開発seed 0/1/2と
条件変更seed 100/101/102、60tick、Memoryあり/なし・適応なしの3構成で36 episode。
18組のペアを別Ledgerで直列実行し、順番はseed偶奇で交互に変更。開始前に各modeを
4tickでwarm-up。測定中にpytest/build/E2Eは実行しなかった。
変化後条件は従来v1の宣言済みtransfer設定であり、blindな未知環境試験ではない。

```sh
uv sync --frozen --extra dev --extra physical --extra sandbox
uv run python -m scripts.bench_belief_reuse --output .cache/new-belief-reuse-run
uv run pytest -q
uv run ruff check src tests workers scripts
uv run ruff format --check src tests workers scripts
npm --prefix web ci
npm --prefix web run contracts
npm --prefix web run build
PREACT_E2E_ARTIFACT_DIR="$PWD/.cache/new-belief-e2e" npm --prefix web run test:e2e
uv build --out-dir .cache/new-belief-dist
uv run python scripts/audit_distributions.py .cache/new-belief-dist --output .cache/new-belief-dist-audit.json
```

outputは存在しない新しいdirectoryを指定する。
[公開結果JSON](../reports/belief-reuse-v1-results.json)はprotocol、source SHA-256、全測定、
集計と意味的一致のhashを含む。raw DB/artifact/traceは公開しない。
今回のraw出力は`.cache/belief-reuse-v1-final`。過去の凍結protocol/結果は変更していない。

## 呼出し監査と再利用箇所

通常60roundの各phaseを実際に計数した。括弧内は再利用有効時。

| phase | 実世界観測 | retrieve | infer | propose |
|---|---:|---:|---:|---:|
| Runtime開始 | 60 | 60 | 60 (60) | 60 |
| 3候補のForecast | 0 | 180 | 180 (0) | 0 |
| dispatch直前のfresh観測 | 60 | 60 | 60 (0) | 0 |
| executor内部 | 180 | 0 | 0 | 0 |
| Runtime終了観測 | 60 | 60 | 60 (60) | 0 |
| 実行記憶後のround間観測 | 60 | 60 | 60 (60) | 0 |
| Agent最終観測 | 1 | 1 | 1 (1) | 0 |
| 合計 | 421 | 421 | 421 (181) | 60 |

adapterの観測は241回。通常構成はhit240/miss181/bypass0。
最終Agent観測もinferするのは、round間観測の後に`cognitive_update`をappendし、
次のretrieveでrun変更を検出するため。実観測とMemory確認は一切減らさない。
`no_memory`はretrieve 0、infer421→121、hit300/miss121。
取得を無効化しているためcognitive_updateによるrefreshがなく、次round開始と最終観測もhitする。
Memoryの記録とreceipt確定は引き続き実行する。

全36 episodeでengine calls540、execution calls60、propose60、実世界観測421、
adapter観測241が同じ。SQLも通常/適応なし10677、Memoryなし10082で有効/無効間に差がない。

## 平均時間（各構成6ペア）

推定CPU/wallは**実行したPlanner.inferの合計**。hit照合やcopyの費用は含めず、
episode全体には全費用を含める。CPUはprocess_timeでStoreのworker threadも含む。
報告のためのLedger読出し・hash計算はepisode計測の後で実行した。

| 構成 | infer回数 off→on | infer CPU ms off→on | infer wall ms off→on | episode CPU 秒 off→on | episode wall 秒 off→on |
|---|---:|---:|---:|---:|---:|
| 通常認知 | 421→181 | 5.318→2.094 | 5.224→2.061 | 3.327→3.411 (+2.52%) | 3.908→3.993 (+2.16%) |
| Memoryなし | 421→121 | 4.039→1.287 | 4.037→1.314 | 2.896→2.939 (+1.49%) | 3.448→3.522 (+2.14%) |
| 適応なし | 421→181 | 4.599→1.737 | 4.509→1.703 | 3.337→3.406 (+2.05%) | 3.916→3.976 (+1.54%) |

通常のretrieve合計はCPU411.73→404.81ms、wall434.04→427.04ms。
適応なしはCPU415.34→411.73ms、wall437.68→434.08ms。頻度とSQLを変えていないため、
この数msの差はretrieval最適化の成果とは扱わない。

## 重複推定microbenchmark

同じ現実状態を200回新しく観測し、毎回receipt-backed Memoryを取得する。
0/12件の実行記録でcacheはhit199/miss1、infer200→1。単なる高コストダミー関数ではない。
microのfixture実行はcontrolled receipt作成であり、Gate付きAgentの実行とは区別する。
推定内容/unknownが全反復で一致することをassertする。

| 記憶件数 | retrieve回数 off/on | infer CPU ms off→on | 全体CPU ms off→on | 全体wall ms off→on |
|---|---:|---:|---:|---:|
| 0 | 200/200 | 0.843→0.005 | 2.677→7.541 | 2.676→7.548 |
| 12 | 200/200 | 1.765→0.009 | 129.495→160.799 | 137.882→169.286 |

軽いEMAでは安全な入力のdump/hash/deep copy・宣言照合の費用を償えない。
12件では全体wallが22.78%増。0件でも照合費用が支配的。
これは純粋な推定再利用をCPU queueの既定値にする根拠にはならない。
高コストPlannerでの速度改善は未測定であり、主張しない。

## 意味的一致と安全の範囲

18組全てで正規化したPrediction、Evaluation、Gate preview/authorization、行動列、
実観測、報酬、危険判定、cognitive_update、execution由来のerror/calibration行が一致した。
random run/node/action/prediction/receipt IDは同じ論理参照へ写し、関係を保持する。
timestamp、latency、authorization expiryの絶対値を比較から除外する。
Claim key/source digestは元の構造へ対応付ける。元のGate evidence hashを実Evaluationと
照合し、各outcomeのStore complete receipt・state・action bindingを確認してから正規化する。
安全判定・証拠のmembership/依存関係・予測値・実測値は除外しない。

今回の全episodeはunsafe=False。通常の開発報酬は80.25/47.65/49.35、
transferは52.15/47.70/53.85で有効/無効が一致する。
unsafeが起きなかったことは、cacheが安全を証明したという意味ではない。
receipt/外部更新/不正branch/cancel等は別の否定テストで検証する。

## 制約と次の判断

3seed/split、一回ずつの測定、小さいEMA、macOSのdisk/fsync/OS変動、計数の計測費用がある。
小さい全体差への統計的な速度保証はない。time-dependent Plannerでの高速化は測定していない。
推定の純粋性は明示契約であり、Python全般の自動証明ではない。
整合性は既存Memoryのappend-only・取得範囲・時点境界に限定する。
詳細は[設計文書](belief-reuse.md)に記載した。

次はreceipt確認を維持したままfingerprint/copyの費用と推定の費用を分離してprofilingし、
実際に高コストな純粋推定で損益分岐点を測る。現段階では**opt-inのまま**が妥当。

## 実行した最終ローカル検証

- Python全テスト: **443 passed in 47.26s**（既存404 + 新規39）。
- Ruff check成功、format --check成功（145 files）、git diff --check成功。
- frontend contract再生成は既存2ファイルとbyte一致、TypeScript/Vite build成功。
- 既存Chromium E2E: **6 passed in 8.5s**。
- wheel/sdist buildとdistribution integrity成功（67 Python sources一致）。
- 公開JSONのprotocol/source hash、36 episodeの一致、平均集計を再計算して照合した。
- 既存Core/Store、Gate/Runtime/Memoryテスト、凍結queue/memory protocol・結果はdiffなし。

途中の否定テストでfrozen Stateへの直接assignmentがPydanticに先に拒否されたため、
別のStateを返すfixtureへ修正し、BeliefEstimator自体の拒否を検証した。
初期比較scriptのrandom Claim key/source digestの正規化漏れも修正した。
最終測定・検証にはこれらの失敗試行を成功データとして混入させていない。
