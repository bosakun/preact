# Safe Belief Reuse — Phase 2

対象はPR #4後の公開main `1f2604c`。`State.id`はdomainとpayloadだけを識別する。
今回はCoreを変更せず、cognitionの**推定計算**にepisode内の単一entryを追加した。
既定は`reuse_beliefs=False`。軽いqueue EMAに無条件の速度改善は期待しない。
[実測結果](belief-reuse-results.md)と独立[protocol](../benchmarks/belief-reuse-v1.json)を参照。

## 監査したデータフロー

成功する一roundでは、Runtime開始、dispatch直前、Runtime終了、round間の4回で
`_CognitiveWorld.observe`が実世界を観測し、Memoryを取得して推定する。
さらに3候補それぞれのQueueForecastがMemory取得と推定を行う。
`propose`は開始観測に対して1回だけ。episode終了時にもう1回観測・取得・推定する。
queue executor内部の3観測は推定を呼ばない。60roundではadapter観測241、
実世界観測421、取得421、推定421、提案60。engine callsは540。

再利用時もこの観測・取得・提案・engine callsは維持する。
`BeliefEstimator`は毎回現在のStateを検証し、既存`EpisodicMemory.retrieve`が
receipt検証と更新検出を完了してから推定を照合する。
Gateは各候補の**新しいPredictionとEvidence**を評価し、Runtimeがfresh Stateを
再観測して一手だけ承認・実行する。確定receiptとoutcomeを記憶し、次roundへ進む。
候補、未来、検証、Gate、authorizationをこのcacheへ入れない。

## 再利用の契約

具体的なPlannerクラス自身が`belief_reuse_policy()`を宣言し、
`BeliefReusePolicy(token, timestamp_independent=False)`を返す場合だけ対象となる。
継承された宣言だけでは新しい推定アルゴリズムの保証にならないため、subclassはopt-out。
宣言がない、不正、例外、episode外、Taskが異なる場合は通常の推定を実行する。

これは「推定は入力とtokenに対して純粋」という**実装者がレビューして保証する契約**で、
任意Pythonの純粋性を自動証明する機構ではない。tokenにはアルゴリズムversion、設定、
推定へ影響する内部状態を全て含める。入力外の時計、乱数、外部状態、必要な副作用が
あるPlannerは宣言してはいけない。QueuePlannerのEMAは時刻を読まず、設定は
adaptation/priorのみ。余分なinstance属性があれば宣言を撤回する。

同じentryを使うには以下を全て満たす必要がある。

- Stateの全field（payload、domain、kind、provenance、uncertainty、parent_id、
  schema_version、idを含む）が一致する。
- timestampだけは`timestamp_independent=True`を明示したPlannerで除外できる。
  時間依存のopt-in推定はtimestampも照合する。時間減衰や壁時計依存を一般化しない。
- 検証済みExperience全fieldと順序が一致する。input/action/observation、receipt、
  prediction ID、runとevent参照を省略しない。
- 同一Memory・Store instanceで、run manifestとMemoryのgenerationが一致する。
  generationは派生状態の無効化通知で、authority tokenではない。
- 同一Planner instance、infer関数とcode object、宣言token、Task全field、use_memoryが一致する。
- 推定後も宣言・アルゴリズム・Memory・Experienceが変わらず、返却されたobservedが入力と一致する。

SHA-256でserializable入力を照合する。表現不能なら通常推定へ戻り、entryを破棄する。
entryが保持するのはinferredとunknownのdeep copyだけで、Stateを保持しない。
hitでも`Belief.observed`は**今回実際に取得して検証したState**のdeep copyで構築する。
返却値への変更はcacheを変更しない。

## 寿命と整合性の範囲

CognitiveAgentが開始時にbegin、終了・例外・cancel時にfinallyでendする。
同じAgentの再実行は従来同様禁止し、新episodeはfresh Agentで開始する。
Memoryのclear、run追加・変更・検証失敗はgenerationを進める。
単一entryなので、入力変更時には古い推定を破棄する。観測・推定・Memory確認の失敗を
古いBeliefで埋め合わせない。async lockとepochがawait中の無効化を保護する。

外部writerは**毎回のretrieve**で既存Store.run_headsの更新検出を受ける。
receipt-onlyの変更や同一runへの追記も対象。例えばExperienceが変わらないdiagnosticの
追記もgenerationでmissにする。Memoryの検査対象は従来のdomain/limit=12の範囲であり、
manifestにないrunや取得対象外の履歴まで検証したという主張はしない。

整合性は[Memory Indexのappend-only・時点境界](episodic-memory-index.md)を継承する。
照合が完了した後に別プロセスがcommitした変更は次のretrieveで検出する。
Store全体のtransaction snapshot、外部writerとの原子的な推定・実行は保証しない。
Ledgerイベントの直接書換え、databaseの交換・rollbackは既存Store契約外。
キャッシュの一致や成功率は安全証明ではなく、Gate/receiptのauthorityは従来のまま。
SQLite固有の新規APIや独立Ledgerは追加していない。

## 利用方法

```python
agent = CognitiveAgent(store, artifacts, registry, planner, goals, policy,
                       reuse_beliefs=True)
forecast = QueueForecast(world.task, planner, agent.memory,
                         use_memory=agent.use_memory,
                         belief_estimator=agent.belief_estimator)
agent.registry = Registry([forecast, immediate_verifier, future_verifier])
result = await agent.run(world, max_rounds)
```

共有は明示的。QueueForecastへ渡さない場合、そのforecastは従来どおり推定する。
Coreや他domainへ自動適用しない。`use_memory=False`は取得を完全に無効化し、
receipt-backed履歴の記録は維持する。既存のadaptation/Memory ablationは変更しない。

## 検証と残る研究課題

回帰・否定テストは`tests/test_belief_reuse.py`。既存Gate/Runtime/Memoryテストは維持。
外部プロセスの偽outcome、pending/aborted receipt、未実行branch、入力や参照の変更、
timestamp・内部状態・Task・設定、cancel/失敗、defensive copy、episode境界を検証する。
実episodeの比較はrandom IDを関係を保って正規化し、original Gate evidence hashと
確定receiptを先に照合してからPrediction/Evaluation/Gate/実観測/学習結果を比較する。

次の課題は高コストだが純粋な推定での損益分岐点、より安価で完全な入力fingerprint、
Planner契約違反の検出、時間依存beliefでの無効化設計。他の認知機能、LLM、Vector DB、
Information Action、Lazy Verification、観測やreceipt確認の省略はこのPRに含めない。
