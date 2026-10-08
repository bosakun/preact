# Receipt-backed Episodic Memory Index — Phase 2 最初のPR

## 監査と変更範囲

2026-10-08。GitHub main `471317db730b9a84d07d1883700294a2ec0cb84f`を基点とする。
既存PR #3はマージ済み、開発開始時のopen PRは0件。レポートが調査した
`feat/cognitive-loop`の`7f9396f3b2d62fbb7a5b04db59f8245a90ba4edd`と、現mainの
cognition、Store、既存memoryテストは同じ内容だった。ローカル開発資料
`deep-research-report-2.md`（1099行）を全文確認した。原文は公開PRに含めない。

Phase 1のMemoryは既にreceipt照合、run復元、receipt重複排除、直近domain検索を持つ。
これらを再実装せず、既存`read()`を未キャッシュのauthority検証器として残す。
繰り返し呼ぶ`retrieve()`と`remember()`にrun単位の派生キャッシュを追加する。
今回変更するCoreは汎用read-only APIの`Store.run_heads()`追加だけ。
Prediction、Evidence、Decision Gate、実行承認、Runtimeは変更しない。
Belief reuse、Information-seeking Action、Lazy Verification、LLM、Vector DB、
v2 agent比較はこのPRの範囲外。

## レポートのサンプルから変更した判断

`next_seq/status`だけのwatermarkでは不十分だった。現Storeの`intent()`、
`complete_execution()`、`abort_execution()`は独立したtransactionでexecution行を更新し、
`runs.next_seq`を進めない。receipt状態だけが変わるとそのサンプルでは古い経験を返せる。

schemaにrevision列を追加すると既存DBのmigrationと全writerの変更が必要になるため、
今回は`RunHead(next_seq, status, execution_digest)`をread-onlyで計算する。
`execution_digest`は該当runの全execution行をID順に並べたcanonical JSONのSHA-256。
ID、run ID、state ID、action hash、status、receiptの**内容全体**を含む。
単なる件数、complete件数、最終receipt IDでは改ざん検出を代替できない。

SQLAlchemyの通常のSELECT/UNION ALL/INを使う。256 run以下のbatchにつき1 SELECTで
runとexecutionを同一statementで読む。空集合はSQLなし、欠落runは結果に含めない。
SQLiteのPRAGMA、data_version、JSON集約関数、trigger、FTSは使わない。
DB schemaとwrite pathも変更しない。PostgreSQL向けSQL compilationはテストするが、
今回の実行測定はSQLiteのみで、PostgreSQL実運用性能の証明ではない。

[SQLAlchemy公式connection資料](https://docs.sqlalchemy.org/en/20/core/connections.html)を
参照した。PostgreSQLのREAD COMMITTEDはSELECT開始時点のcommitted snapshotを読むという
[公式仕様](https://www.postgresql.org/docs/current/transaction-iso.html)があり、複数SELECTを
一つのglobal snapshotと呼ばない。今回もその制約を明示する。

## データフローと寿命

```text
manifest run IDs / remember(run)
  → 最新runから必要なdomain/limit分の候補runを選ぶ
  → run_heads: イベント番号・run状態・execution内容を一括照合
  → unchanged: 私有cacheの検証済みExperienceを再利用
  → changed/new: 古いentryを捨て、run全イベントと全outcome receiptをreadで照合
  → changed batchのrun_headsを再確認
  → 一致した場合だけcacheを公開
  → Experience.model_copy(deep=True)で返す
```

派生cacheは`_RunIndex(head, experiences)`だけに絞る。不要な多重posting mapや
独立Ledgerは追加しない。Experienceのreferenceにrun/event seqが残り、input state、
action fingerprint、prediction IDs、Observation/receiptの関連も保持する。
イベントの追加は過去node_updatedやprediction参照を変える可能性があるため、
変更runのsuffixだけを読む最適化は採用せず**そのrun全体を再検証**する。
他runの全件再読出しは省く。未キャッシュrunでいったんbatchを止め、直近runから
必要数を得る既存探索順を維持する。

cacheはMemoryインスタンスだけが所有する。`asyncio.Lock`で同じインスタンスの
remember/retrieve/clearを直列化する。返却値やPlannerからcache内のPydantic objectを
直接参照できない。`clear()`はrun_idsを維持したままcacheを破棄し、次回再構築する。
新しいMemoryにmanifestのrun_idsを設定しても同じ結果を復元できる。
無制限の永続記憶基盤ではなく、現行のepisode専用controllerに対応したcacheである。

outcomeは重複排除する**前**にreceipt照合する。同じreceiptの正規outcomeをもう一度
appendしても経験は増えず、同じreceiptの偽outcomeをappendすると例外になる。
予測しかないrun、未実行candidate、pending/aborted receiptから新しい経験を作らない。
receiptと整合しないoutcomeは黙って無視せず失敗する。cacheのdigestは更新検出専用で、
安全性証明でも暗号学的な署名でもない。cacheはEvidenceにもGate入力にも昇格しない。

## 有効性を保証する範囲と安全側の動作

- **同一run追記**: Store.appendがイベント保存とnext_seq更新を同一transactionで行う。
  次回そのrunを検索すると再検証する。run statusの変更も検出する。
- **別Store/別プロセス**: process-localの世代番号は使わず、DBの現在値を毎回読む。
  cacheを温めた後の別プロセスからの追記を実際のsubprocessテストで検出する。
- **receiptのみの変更**: eventsに追記がなくても、executionの追加・状態遷移・内容変更・
  削除によってdigestが変わる。既存のcomplete observationを改ざんした場合も再検証する。
- **検証中の更新**: full read前後でheadが違えば、そのbatchのcacheを破棄して例外にする。
  自動で無限retryせず、次の明示的retrieveが安定したauthorityを再検証できる。
- **障害/欠落/キャンセル**: consulted batchのentryを破棄し、例外を伝播する。
  DB読出し失敗時に古いcacheをfallbackとして返さない。
- **時点の制約**: warm readはhead SELECT時点での一致、cold/changed readは前後の
  token一致を確認する。その後にcommitされた更新は次のretrieveで検出する。
  全runを跨ぐserializable snapshotや、返却後も変わらない事実を保証しない。
  limitで到達しない古いrunは未検査。更新するwriterをメモリ側のlockで止めない。
- **契約外**: eventsのin-place UPDATE/DELETE、next_seqを進めない直接event挿入、
  DBバックアップへのrollback/DB差替え、authority全体の敵対的な同時書換えは、既存の
  append-only Store契約外。これらを同一tokenのまま検出できるとは主張しない。
  その運用ではMemoryを破棄し、Ledger自体の監査・復元が別途必要。

強い小さなrevision counterだけの方式は将来検討できるが、全writerのtransactional更新と
schema migrationが必要。現方式はその変更を避ける代わりに、warm retrievalも該当runの
execution JSONを読む。全receipt走査を無くした、O(1)になった、という主張はしない。

## 再現と評価

```bash
UV_CACHE_DIR=.cache/uv uv sync --offline --frozen --extra dev --extra physical --extra sandbox
uv run python -m scripts.bench_cognitive_memory --output .cache/new-memory-index-bench
uv run pytest -q
uv run ruff check src tests workers scripts
uv run ruff format --check src tests workers scripts
```

依存cacheがないcloneでは最初のsyncから`--offline`を外す。outputは存在しない新しい
ディレクトリを指定する。protocolは`benchmarks/cognitive-memory-index-v1.json`。
60/600/6000実receiptを1件/runと全件/単一runの2配置で測る。直近12件を取得する。
fixtureはqueue actionを実際に実行し、Storeでreceiptを確定する制御実験。
Gate承認の証拠ではなく、reference-onlyの予測fixtureは予測精度評価に使わない。
既存RuntimeのGate維持は従来のend-to-end regressionで検証する。

before基準はPhase 1のretrieve/rememberアルゴリズムを保持した`FullRereadMemory`。
入力eventsと既存receipt検証器を共有するため、検証の削減による偽の高速化を防ぐ。
full reread / cold / warm / 一run無効化を比較し、各測定でExperience全fieldの一致をassert。
固定seed、mode順のshuffle、p50/p95 wall、process CPU、Store API calls、SQL statements、
cache件数、source/protocol hash、偽outcome拒否結果を保存する。fixture構築と無効化用の
diagnostic appendはretrieveの計時に含めない。CPUはDBサーバを含む分散計算コストではない。
30反復（warmは100）、6000件/単一runのcold・full・無効化は5反復に抑えるため、その
p95は最大値相当で、厳密なtail推定ではない。時間は機材/OS/他負荷の影響を受ける。

測定結果は`docs/episodic-memory-index-results.md`と
`reports/cognitive-memory-index-v1-results.json`に記録する。raw DB/反復sampleは開発cacheに
保持し、公開summaryのsource hashと再実行により検査できる。既存v1 protocol/結果は更新しない。

初期JOIN試作では6000件/1件runのwarmが36.53 msで、従来5.13 msより遅かった。
execution.run_idのSQL indexが無い現schemaではJOINが繰り返し走査するため、
同一statementのUNION ALLへ変更し、executionのfilterを一回にまとめた。
失敗した測定も開発cacheに保持し、schemaや安全境界を変えず再評価する。
