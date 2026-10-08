# Episodic Memory Index — 変更前後の実測

2026-10-08。固定seed 20261008、Python 3.12.13、SQLite/file DB、CPU実行。
最終sourceの6ケースを実行し、全測定でExperience全fieldの一致と偽outcome拒否を確認した。
rawから集計を再計算し、source SHA-256が現ファイルと一致することも確認。
数値summary/source/protocol/raw hashesは[JSON](../reports/cognitive-memory-index-v1-results.json)。
設計と再現手順は[設計文書](episodic-memory-index.md)。

## Wall time（p50 / p95、ms）

| receipt配置/総件数 | 変更前 full reread | indexed cold | indexed warm | 一run無効化 |
| --- | ---: | ---: | ---: | ---: |
| 1件/run / 60 | 4.54 / 5.26 | 10.39 / 11.14 | 0.68 / 0.80 | 1.52 / 1.61 |
| 単一run / 60 | 12.98 / 13.41 | 14.80 / 19.67 | 1.05 / 1.19 | 14.87 / 20.10 |
| 1件/run / 600 | 4.60 / 4.69 | 10.92 / 11.35 | 0.69 / 0.78 | 1.56 / 1.64 |
| 単一run / 600 | 146.32 / 150.81 | 157.88 / 161.23 | 6.12 / 7.08 | 158.89 / 163.74 |
| 1件/run / 6000 | 4.93 / 5.14 | 36.87 / 38.31 | 2.07 / 2.30 | 4.34 / 4.71 |
| 単一run / 6000 | 1823.94 / 1925.51 | 1990.20 / 2029.08 | 117.00 / 126.08 | 2088.41 / 2137.85 |

## CPU time（p50、ms）

| receipt配置/総件数 | full reread | cold | warm | 一run無効化 |
| --- | ---: | ---: | ---: | ---: |
| 1件/run / 60 | 3.51 | 8.34 | 0.63 | 1.35 |
| 単一run / 60 | 10.37 | 12.09 | 1.01 | 12.16 |
| 1件/run / 600 | 3.57 | 8.86 | 0.65 | 1.39 |
| 単一run / 600 | 120.54 | 132.02 | 6.08 | 133.04 |
| 1件/run / 6000 | 3.90 | 34.80 | 2.03 | 4.17 |
| 単一run / 6000 | 1562.63 | 1728.19 | 116.84 | 1826.10 |

## DB呼出しと解釈

1件/runの直近12件検索では、full rereadのSQL 24回（read_events 12 +
execution_record 12）をwarmではSQL 1回（run_heads 1）へ減らした。
一runが更新されるとSQL 4回（run_heads 2 + read_events 1 + execution_record 1）。
coldは未キャッシュrunで探索を止めて旧探索順を保持するため48回。
coldの追加照合コストは削減できておらず、6000件では36.87 msと変更前4.93 msより遅い。

通常のepisode相当60件でwarm wallは6.7倍、6000件では2.4倍高速。
6000件のwarm CPU中央値も3.90→2.03 msへ減った。execution.run_idのSQL indexが
現schemaにないため、UNION ALLでもexecution tableのfilter scanが残り、O(1)ではない。

単一runに6000 receiptがある場合は変更前のSQL 6001回をwarm 1回へ減らし、
1823.94→117.00 ms（15.6倍）。一方そのrunが更新されると全receiptを再検証するため
2088.41 msへ悪化する。warmでも6000件のexecution JSONを読む/serializeするコストが残る。
cacheは1件/runの測定で12件、単一runで60/600/6000件を保持した。
件数だけの測定であり、bytesでのpeak memoryやDBサーバCPUは測っていない。

初期JOIN試作は6000件/1件runでwarm 36.53 ms（旧5.13 ms）へ退行したため修正した。
最終UNION ALL方式で2.07 msへ改善。失敗を含む初期測定も開発環境に保持。
cache導入だけで全条件が速くなるとは主張しない。

## 測定・能力の限界

- 各条件30反復、warm 100反復。6000件/単一runのfull/cold/無効化は5反復。
  5件のp95は最大値であり、tail latencyの精密推定ではない。
- controlled fixtureはqueue actionを実行しreceiptを確定するが、Gateを経由しない。
  retrieval性能/整合性の実験であり安全性承認の実証ではない。
- 未実行候補のprediction参照をMemory経験へ混入させない。全結果は既存取得結果と一致。
  偽outcomeは変更前・index双方で拒否。
- 別プロセスの追記/receiptのみの変更、defensive copy、障害、競合、再構築はテストで検証。
- SQLiteで実測。PostgreSQL SQL compilationは検証するがlive DB性能は未検証。
- end-to-end回帰で旧Memoryと行動/観測/報酬/推定が一致することを確認する。
  新たなgoal達成率・安全性・学習能力の改善はこのmicrobenchmarkから主張しない。
- 新しいagent比較protocol、Belief reuse、Information Action、Lazy Verificationは未実装。
  既存cognitive-queue-v1 protocol/実測結果は変更していない。

## 検証

全Pythonテスト404件成功（44.18s、新規24）。Ruff check/format（142 files）、
frontend contractsのbyte一致、web build、E2E 6件（7.9s）、distribution audit成功。
既存forged outcomeテスト、Gate/Runtime/Evidence、v1 protocol/結果のdiffなし。
GitHub CIの結果はPRおよびdocs/agent-progress.mdへ記録する。
初期SQL形変更時はtestがJOIN文字列を期待して1件失敗し、UNION ALLの形と
read-only/batch/PostgreSQL compilationを検証する形へ修正して再実行した。
既存forged outcomeのテストは編集していない。
追加テストの未format箇所もRuffで検出し修正した。
raw bundleは開発cacheに保持し、公開JSONは集計値と検査可能なhashを収録する。
新しい出力先でbenchmarkを実行すると結果と各反復sampleを生成できる。
wall/CPUの一致は測定環境に依存し、byte一致する時間を保証しない。
