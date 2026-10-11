# World Model-Grounded Action Selection — 固定v2評価

今回検証する能力は、監視付き学習モデルの3 tick予測をGoal価値へ接続し、
実際に選んだ一候補が通常Runtime・独立Verifier・Gateを経由して実行され、
complete receiptと実観測に結び付くことである。設計・使用例は
[learned-dynamics.md](learned-dynamics.md)を参照する。

## Protocolと採点対象

[queue-model-selection-v2.json](../benchmarks/queue-model-selection-v2.json)を
最終pilot・本評価より前に固定した。通常7環境×8 seed×5方式=280 episode、
実際の回復2方向×4 seed×5方式=40 episode、合計320 episodeで比較する。
安定low/high、low→high、high→low、noise、部分観測、失効を含む。
通常noise=0条件のseed間一致は再現性であり、一般化性能の証明ではない。

全方式で48実行tick、Goal delivered=30、capacity=12、holding cost=0.25、
投入費用=0.1/job、probe費用=0.05を共有する。通常の準備13 tick
（正式probe 8回と通常行動5回）、部分観測の準備8 drainもこの48 tickに含む。
全方式の候補に同じ公開service下限のfilterを適用する。これは安全Evidenceではなく、
Runtimeの独立検証を引き続き必要とする。探索はwidth=1/max_nodes=1/search=False。

固定期待効用は各service branchで
`min(残Goal, D3−D0) − 0.25×sum(queue1..3) − 0.1×root投入量`を計算し、
branch確率で平均する。同値なら少ない投入を選ぶ。比較する5方式は、学習分布、
固定事前p(high)=0.5、既存InformationQueuePlanner(no_probe)、固定submit(1)、
モデル利用不可の既存Planner fallback。学習分布と固定事前は同じモデルHealthで
利用可否を判定する。全baselineも同じauthority照合wrapperを利用するため、
レイテンシはwrapper経路の費用であり、従来Planner単体の性能ではない。

実Rewardはdeliveredから実際のholding・投入・probe費用を引く。
Goal utilityはdeliveredを30でcapして同じ費用を引く。Goal達成後の余分な処理を
Rewardとして隠さず、両指標と余分な投入を記録する。shared parent学習32 tickと
回復の収集・独立評価は閉ループRewardに含めず、別の実tick・費用として明記する。

H3の継続はenvironment-onlyである。閉ループがその後選んだAction列とは異なる。
閉ループのMAEは実行したrootの一手のみで、未実行Actionに真値を付けない。
H3状態MAE・Action差分MAEは、同じ非空初期状態から別途正式Runtimeで
3候補と継続drainを実行した独立診断episodeで採点する。
この診断は両条件とも最初の5 tickをlowで実行してqueue=2、delivered=4を作る。
high診断は固定したtick5以降でhighへ変化する。学習分布をlow/high条件別に比較する
条件付き予測評価であり、その変化をAgentが検出してモデルを選んだ実証ではない。
直接のQueueTemporalEngineによる診断予測は監視付きAction選択経路と区別する。

v1はRecoveryの評価再検証72予測を漏らした診断protocolとして保持し、
最終受入結果には使わない。v2は効用・閾値・seedを変えず、全方式のcall上限を128へ
揃えた。通常選択3予測、昇格済みBは評価再検証72+選択3=75を既存予約・実績channelで
計上する。上限は各Runtime roundであり、episode全体128回という意味ではない。
上限32の場合、B比較は予算不足で開始されない否定テストを含む。
外部API呼出しとAPIドル費用は0、計算ドル費用は未換算である。

## 実測結果

本評価320 episode、11,400判断・11,400実行の独立監査がpass。
unsafe=0。Goal・Reward・holding等は各通常条件8 episode、回復条件4 episodeの平均。
公開集計は[queue-model-selection-v2-results.json](../benchmarks/queue-model-selection-v2-results.json)。

| 環境 | 方式 | Goal達成率 | Reward | Goal utility | delivered | holding | モデルcoverage | fallback率 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| stable_low | learned | 1.000 | 20.100 | 15.100 | 35.000 | 11.000 | 0.686 | 0.314 |
| stable_low | prior | 1.000 | -3.400 | -3.400 | 30.000 | 30.000 | 0.943 | 0.057 |
| stable_low | existing | 1.000 | 20.100 | 15.100 | 35.000 | 11.000 | 0.000 | 0.000 |
| stable_low | fixed | 1.000 | 23.000 | 14.000 | 39.000 | 11.500 | 0.000 | 0.000 |
| stable_low | unavailable | 1.000 | 20.100 | 15.100 | 35.000 | 11.000 | 0.000 | 1.000 |
| stable_high | learned | 1.000 | 26.600 | 26.600 | 30.000 | 0.000 | 0.743 | 0.257 |
| stable_high | prior | 1.000 | 26.600 | 26.600 | 30.000 | 0.000 | 0.743 | 0.257 |
| stable_high | existing | 1.000 | 31.100 | 26.100 | 35.000 | 0.000 | 0.000 | 0.000 |
| stable_high | fixed | 1.000 | 35.500 | 25.500 | 40.000 | 0.000 | 0.000 | 0.000 |
| stable_high | unavailable | 1.000 | 31.100 | 26.100 | 35.000 | 0.000 | 0.000 | 1.000 |
| low_to_high | learned | 1.000 | 21.100 | 21.100 | 30.000 | 5.500 | 0.943 | 0.057 |
| low_to_high | prior | 1.000 | 10.100 | 10.100 | 30.000 | 16.500 | 0.914 | 0.086 |
| low_to_high | existing | 1.000 | 25.600 | 20.600 | 35.000 | 5.500 | 0.000 | 0.000 |
| low_to_high | fixed | 1.000 | 30.000 | 20.000 | 40.000 | 5.500 | 0.000 | 0.000 |
| low_to_high | unavailable | 1.000 | 25.600 | 20.600 | 35.000 | 5.500 | 0.000 | 1.000 |
| high_to_low | learned | 1.000 | 26.600 | 26.600 | 30.000 | 0.000 | 0.743 | 0.257 |
| high_to_low | prior | 1.000 | 26.600 | 26.600 | 30.000 | 0.000 | 0.743 | 0.257 |
| high_to_low | existing | 1.000 | 30.350 | 25.350 | 35.000 | 0.750 | 0.000 | 0.000 |
| high_to_low | fixed | 1.000 | 35.500 | 25.500 | 40.000 | 0.000 | 0.000 | 0.000 |
| high_to_low | unavailable | 1.000 | 30.350 | 25.350 | 35.000 | 0.750 | 0.000 | 1.000 |
| noise | learned | 1.000 | 24.588 | 21.338 | 33.250 | 4.938 | 0.493 | 0.507 |
| noise | prior | 1.000 | 17.994 | 14.744 | 33.250 | 11.531 | 0.439 | 0.561 |
| noise | existing | 1.000 | 24.669 | 20.044 | 34.625 | 6.094 | 0.000 | 0.000 |
| noise | fixed | 1.000 | 33.250 | 23.250 | 40.000 | 2.250 | 0.000 | 0.000 |
| noise | unavailable | 1.000 | 24.669 | 20.044 | 34.625 | 6.094 | 0.000 | 1.000 |
| partial | learned | 1.000 | 20.500 | 15.500 | 35.000 | 11.000 | 0.100 | 0.900 |
| partial | prior | 1.000 | 20.500 | 15.500 | 35.000 | 11.000 | 0.100 | 0.900 |
| partial | existing | 1.000 | 20.500 | 15.500 | 35.000 | 11.000 | 0.000 | 0.000 |
| partial | fixed | 1.000 | 35.000 | 26.000 | 39.000 | 0.000 | 0.000 | 0.000 |
| partial | unavailable | 1.000 | 20.500 | 15.500 | 35.000 | 11.000 | 0.000 | 1.000 |
| invalidated | learned | 1.000 | 20.100 | 15.100 | 35.000 | 11.000 | 0.000 | 1.000 |
| invalidated | prior | 1.000 | 20.100 | 15.100 | 35.000 | 11.000 | 0.000 | 1.000 |
| invalidated | existing | 1.000 | 20.100 | 15.100 | 35.000 | 11.000 | 0.000 | 0.000 |
| invalidated | fixed | 1.000 | 23.000 | 14.000 | 39.000 | 11.500 | 0.000 | 0.000 |
| invalidated | unavailable | 1.000 | 20.100 | 15.100 | 35.000 | 11.000 | 0.000 | 1.000 |
| recovered_low_to_high | learned | 1.000 | 26.600 | 26.600 | 30.000 | 0.000 | 0.743 | 0.257 |
| recovered_low_to_high | prior | 1.000 | 26.600 | 26.600 | 30.000 | 0.000 | 0.743 | 0.257 |
| recovered_low_to_high | existing | 1.000 | 31.100 | 26.100 | 35.000 | 0.000 | 0.000 | 0.000 |
| recovered_low_to_high | fixed | 1.000 | 35.500 | 25.500 | 40.000 | 0.000 | 0.000 | 0.000 |
| recovered_low_to_high | unavailable | 1.000 | 31.100 | 26.100 | 35.000 | 0.000 | 0.000 | 1.000 |
| recovered_high_to_low | learned | 1.000 | 20.100 | 15.100 | 35.000 | 11.000 | 0.486 | 0.514 |
| recovered_high_to_low | prior | 1.000 | -3.400 | -3.400 | 30.000 | 30.000 | 0.943 | 0.057 |
| recovered_high_to_low | existing | 1.000 | 20.100 | 15.100 | 35.000 | 11.000 | 0.000 | 0.000 |
| recovered_high_to_low | fixed | 1.000 | 23.000 | 14.000 | 39.000 | 11.500 | 0.000 | 0.000 |
| recovered_high_to_low | unavailable | 1.000 | 20.100 | 15.100 | 35.000 | 11.000 | 0.000 | 1.000 |

モデルcoverageは学習または固定事前スコアを使った判断の割合。existing/fixedは
その予測を要求しないため0であり、失敗率を意味しない。unknown率は1−coverage。
固定ルールはGoal達成後もsubmit(1)を続けるため、余分なdeliveredと投入を含む。
それを有利/不利に隠さず、実Reward・Goal utility・excess_submitted_jobsを併記する。

| 環境 | priorとの実Action差率 | existingとの実Action差率 | fixedとの実Action差率 | unavailableとの実Action差率 |
|---|---:|---:|---:|---:|
| stable_low | 0.200 | 0.000 | 0.343 | 0.000 |
| stable_high | 0.000 | 0.114 | 1.000 | 0.114 |
| low_to_high | 0.200 | 0.114 | 0.657 | 0.114 |
| high_to_low | 0.000 | 0.114 | 1.000 | 0.114 |
| noise | 0.164 | 0.182 | 0.757 | 0.182 |
| partial | 0.000 | 0.000 | 0.425 | 0.000 |
| invalidated | 0.000 | 0.000 | 0.343 | 0.000 |
| recovered_low_to_high | 0.000 | 0.114 | 1.000 | 0.114 |
| recovered_high_to_low | 0.200 | 0.000 | 0.343 | 0.000 |

この差率は同じseed・tickの実行列の比較であり、選択が分かれた後のStateは同じではない。

| 独立H3診断 | 方式 | tick2/3状態MAE | Action差分MAE | coverage |
|---|---|---:|---:|---:|
| low | learned | 0.000 | 0.000 | 1.000 |
| low | prior | 0.646 | 1.042 | 1.000 |
| high | learned | 0.000 | 0.000 | 1.000 |
| high | prior | 0.354 | 0.625 | 1.000 |

独立診断はlow/high各4組。分布変化・noiseのH3 Action差分精度まで一般化しない。
閉ループ一手MAEはJSONのone_tick_state_maeとcoverageを併読する。

本評価全体CPU 4690.226s / wall 5038.857s、
process peak RSS 172.812MiB。重い検証を並行させず測定した。
方式別の準備・setup・比較・episode CPU/wall、観測・Memory・predict・Verifier回数はJSONに記録。
回復準備費用はrecovery_preparation、昇格setupの72予測はsetup_local_revalidation_calls、
各判断の72再検証と3選択予測はlocal_revalidation_calls/local_prediction_callsで分離する。

本評価semantic hash: `198cb50fbd4f8e7f7b8911afc7c7de4213bdaad6e55c9eb6dd03a9531eb47684`。
実装source hash: `3b23324a7eebcea1c9c635584edbe3b8b7fe31721cf88005eb74e5df285b8cb2`。
独立auditor source hash: `9cfed5e457d38d264403374dccc32938b9c8b72b00362a897be99b15cb82abfd`。
pilot45 episode/1,600判断・実行も独立監査pass。小規模同条件の2再実行と各監査がpassし、
semantic hash `bac93c31a6eb7fba7a348f31ece5b13cfa4a080bb809e5f4dab9753b75cb6c05`が一致した。これは全320 episodeの二重実行ではない。

Python全645件、Ruff check/format188 files、frontend契約byte一致/build、Chromium E2E6件、
CPU Python3.11 source bridge16件が成功。Python wall1019.62sとpilot時間は並行検証の影響を
含む参考値。外部GPU/クラウドの検証成功や通常の高速化は主張しない。

最終wheel/sdistのdistribution integrityもpass（85 Python sourcesとassets/license、
private-path除外）。独立監査の別計測はwall5168.36s/user3450.97s/sys1455.46s。
macOS timeのkern.clockrate照会は権限制限で警告したが、監査本体は成功した。
GitHub CIはPRの最終headで別途確認し、結果をPR本文へ記録する。

## 同じ実StateでActionが変わる例

統合テストでは、別の実training Worldで正式probeを各16回実行し、
complete receiptをTrainerへ渡す。一方はhigh 2件/16件、他方はhigh 8件/16件で、
モデルのパラメータをテスト内で差し替えない。監視Worldは同じ設定・外生scheduleで
正式な準備行動を行い、同じ公開State（tick=13、queue=2、delivered=4、pending=[]）、
同じGoal=20に到達する。両方のHealth=availableを確認し、3候補の予測から前者は
submit(1)、後者はsubmit(3)を選ぶ。両ActionはRuntimeが独立に評価し、
Gate許可・durable intent・complete receiptまで実行される。

これは学習された分布が実Actionを変える検証である。この一例だけから、
選択の変化が長期Rewardを改善することは主張しない。監視Worldとtraining Worldの
private scheduleは所有executor fixtureの設定であり、Planner/Engineへ渡さない。

## 改善・悪化・差なしの解釈

本評価の全320 episodeでGoal達成率100%。Goal達成率の改善は実証していない。
学習方式はstable lowで固定事前より実Rewardが−3.4→20.1、holding costが30→11となり、
実Action差率20%だった。しかし同条件の既存Plannerとは実Action列・結果が同じで、
固定submit(1)の実Reward23.0を下回る。学習の価値を全baselineへの優位性としない。

noiseでは学習Reward24.5875、固定事前17.99375、既存24.66875、固定ルール33.25。
固定事前より改善する一方、既存より0.08125悪化し、固定ルールより8.6625悪化した。
Goal utilityでも学習21.3375は固定ルール23.25を下回る。8 seedの短いnoise fixtureの
平均であり、統計的な優位性や未知分布への一般化は主張しない。

stable highとhigh→lowでは学習と固定事前のAction列・結果が同じ。
学習の一手予測MAEが低くても、意思決定価値が増えたとは言えない。
high→lowはGoalをshift前に達成でき、空queueの後半では能力を識別しにくい。
この条件から変化後の適応や長期安全性の改善を主張しない。

部分観測は準備8 drainだけでは利用不可だが、後続実績から遅れて有効観測が集まり、
tick42から各episode4回だけ学習スコアを使った（coverage10%、fallback90%）。既存Plannerと
実Action・結果が同じで、固定ルールよりReward・Goal utilityが悪化する。
Goal達成はtick39で、モデル利用開始より前だった。Goal達成への寄与は実証していない。
別の24 round統合テストでは有効観測不足のままfallbackのみで終了する。
失効モデル条件は全episodeで学習利用0、fallback100%。どちらも改善の証拠にしない。
noiseは8 episode中7で学習選択が成立し、seed108は準備tick8以降のHealthがsuspectedで
35判断全てfallbackとなった。標本不足と統計的疑いを同じ理由にしない。
通常モデルと回復後Bを合わせた64学習方式episode中55で利用が成立し、9で成立しなかった。
全学習方式のモデル選択は1,214回で、fallback-onlyの9 episodeを能力成立に数えない。

明示的に昇格したBは新episodeで各low→high 26回、high→low 17回の選択を行った。
後者は統計的Health不足でactive経路が閉じるため、fallback51.429%を維持する。
後でHealthがavailableへ戻っても自動restoreせず、deactivation履歴を残す。
回復後low→highの固定事前との差はなく、high→lowは固定事前より改善するが
既存Plannerとの差はない。同一episode内の連続的な自己改善は実証していない。

本評価でunsafe、Gate拒否、ABSTAINは全て0。一候補方式の安全な代替を失う問題が
本評価で発生しなかっただけで、一般的な拒否率0を保証しない。独立Verifierが拒否する
否定テストでは高スコアのsubmitを実行せずABSTAINし、安全なdrainを別経路で実行しない。
verifier不足・予算不足・改ざん・失効・取消・deadline/authorization期限切れも検証した。

観測回数はQueueSelectionWorld経由のカウンタで、準備/回復/診断のnative観測を含まない。
それらは別scopeの実行tick・費用に含む。goal_tickとfirst_model_tickの平均は値がある
episodeだけを対象とするため、Goal達成率とmodel_readinessの利用episode数も併読する。
first_available_tickは準備観測履歴内での最初のavailableを表す。準備後の利用開始は
first_model_tickと各判断のHealthで確認する。partialの前者None/後者42を矛盾と扱わない。

計算費用は大きい。学習方式の全episode CPU/wall平均はstable low 10.040/11.098s、
stable high 11.803/12.878s、noise 13.818/14.838s。
回復後Bはlow→high 134.548/144.199s、high→low 98.427/105.183sであり、
既存Recoveryの評価再実行を毎判断で行う費用を含む。これは高速化の成果ではない。
本評価で必要な検証を実行できたため、今回は性能最適化を行わなかった。

## 再現手順と監査境界

```sh
uv run python -m scripts.benchmark_queue_model_selection --pilot --output NEW_PILOT
uv run python -m scripts.audit_queue_model_selection --output NEW_PILOT
uv run python -m scripts.benchmark_queue_model_selection --output NEW_MAIN
uv run python -m scripts.audit_queue_model_selection --output NEW_MAIN
```

出力は新規directoryを使う。raw DB・receipt・Artifact・判断記録はprivateで保持し、
公開する結果はaggregateのみ。独立auditorはdataset再構築、実World再生、モデル再学習、
Health履歴、予測・効用再計算、順位、State ID/全State、fingerprint、run、
authorization、durable intent、fresh observation、complete receiptと時系列を照合する。
判断記録は実行証明ではなく、実行したActionだけがreceipt-backed経験となる。

receipt破損、manifest不一致、既知source更新、改ざん、例外・取消は正常な不足と
区別して停止する。正当なHealth不足・失効はfallbackとなる。失効済みAを復活させず、
明示的に昇格したBも統計的deactivation後に自動restoreしない。
timestampを加工したStateをfresh observationに使わない。

単一World ownerと完全なmanifestを前提とする。最後の照合後の外部writer変更を防ぐ
分散transactionや、任意Worldの観測を暗号学的に認証する仕組みは追加しない。
この保証をraw Engine/Worldの全APIへ一般化しない。

## 制約と次段階

- 3 tickのenvironment-only継続とiid/定常service分布はモデル仮定である。
  未校正の区間を確率保証、安全証明、因果理解と表現しない。
- Healthがavailableでも昇格済み経路がdeactivatedならfallbackを維持する。
  同一episode中の再学習・昇格・自動復元は今回実装しない。
- 一候補方式はGate拒否時に安全な代替候補を失う。複数の独立検証済み候補から
  Goal価値で選ぶ方式は別設計とする。
- 部分観測ではserviceを識別できず、モデル選択が一度も成立しないことがある。
  fallbackだけのepisodeを能力成立の証拠にしない。
- Goal達成がshiftより早い条件や、完了後の空queue観測では変化の寄与を識別しにくい。
  長いnoise・相関・緩やかな変化と、複数安全候補の設計を今後の課題に残す。
- LLM agent優位性、汎用認知能力、連続的自己改善、Software/Physicalへの直接展開は
  このQueue v2評価からは実証しない。
