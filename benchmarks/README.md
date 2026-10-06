# Benchmark Protocol

`preact benchmark --seeds 5 --ablations` executes 80 local episodes through the actual shared
runtime/executors, writes raw events, policies, task hashes and reports, and counts failures and
abstention. This is a regression suite: one software family and a Cartesian manipulation lab.
It is not the full accepted held-out evaluation. The primary condition freezes calibration;
online adaptation is a separately reported chronological condition with a separate per-domain store.
Frozen is also retained as the explicitly equivalent control. Flat/fixed/frozen currently often tie PreAct;
report this rather than claiming an ablation gain absent evidence.

Add `--workflows` for 120 development episodes including a genuinely separate Software
release workflow: code, actual SQLite migration, data/schema preservation, configuration
and bytecode compilation. This adds one authored development task, not 24 held-out tasks.
The eighth condition disables the disagreement threshold while retaining mandatory checks,
risk bounds and violation vetoes. Ties are expected where only one measured engine is available.

Full acceptance remains 24 held-out tasks × 5 paired seeds per domain, distinct 6 development
and 12 calibration tasks. Required families are logic, behavioral/security, dependency/config
and physical clearance, grasp/place, perception/dynamics. Freeze task/policy/analysis hashes
before held-out execution; use the same proposer/model/action contracts/evaluator for Direct
and PreAct. The local packs below now implement these instance counts; live sponsor-model and
broader repository/perception evaluation remain open.

Primary comparison freezes calibration; chronological online adaptation is separate. Measure
success under constraints, unsafe execution/attempts, Brier/log loss/ECE/coverage/sample counts,
disagreement/error, calls by engine/tier/reason, p50/p95/warm/cold latency, marginal/allocated cost,
and abstention. Use task-clustered paired intervals on genuinely distinct tasks. Local Wilson
intervals describe narrow episodes and must not be presented as those held-out intervals.
The analysis resamples whole task clusters; one-cluster local conditions intentionally have no
generalization interval. Unknown prices yield null cost and explicit cost coverage, never a free claim.
Success and risk each retain Brier, log loss, ECE and sample counts. Continuous/vector errors
are aggregated from executed receipts. Probability interval coverage is unknown from a single
binary outcome; continuous interval coverage is scored only when explicitly supplied.
Pending execution retains unknown unsafe-action rates and bounds, including partial failures.

Replay raw archives without reexecuting authority. Publish exact revision, checkpoint/model/image
IDs, engine/applicability scope, task hash, seed, hardware, budgets, calibration version and artifact
hashes. Unknown hosted prices are unknown cost, not verified free usage. Local unsafe fixtures
intentionally expose attractive shortcuts; they establish gate mechanics rather than general agent superiority.

## Frozen local cohorts

`src/preact/datasets/` contains disjoint 6/12/24 development/calibration/held-out instances
per domain. Software uses two enumerated first-party patches per function with protected
executable probes. Physical uses actual Cartesian MuJoCo scenes with varied obstacles and
observed dynamics. Configuration functions are not dependency-resolution repositories;
scene instances are not real visual perception or robot hardware.

```bash
uv run preact freeze-cohort .preact/protocols/calibration.json --seeds 5
uv run preact evaluate-cohort .preact/protocols/calibration.json \
  --split calibration --output reports/cohort-calibration
uv run preact freeze-cohort .preact/protocols/held-out.json --seeds 5 \
  --calibration reports/cohort-calibration/report.json
uv run preact evaluate-cohort .preact/protocols/held-out.json \
  --calibration reports/cohort-calibration/report.json \
  --ablations --output reports/cohort-held-out
uv run python scripts/analyze_cohort.py reports/cohort-held-out
```

Manifest creation refuses overwrite. Evaluation validates source, fixtures, policies,
Python/MuJoCo versions, dependency lock and frozen calibration hash; rerunning the same
command resumes completed episode archives. Keep source fixed for the full protocol.
Direct receives selected-action forecasts solely for scoring, with their calls included;
forecasts never change its action choice. The primary condition reads a separate calibration
ledger and cannot learn from held-out labels. Online adaptation has its own chronological ledger.

The executed October 4 local evaluation comprises 1,920 episodes, 24 held-out tasks ×
5 seeds × 8 conditions × 2 domains. See [report](../reports/cohort-held-out-v16/report.md),
[summary and paired intervals](../reports/cohort-summary.json),
[per-engine scores](../reports/cohort-engine-scores.json), the two checked-in protocols and
[calibration snapshot](local-calibration-protocol-v16.json). Raw archives/artifacts are generated locally
and ignored by Git; rerun the commands to reconstruct them. This is local held-out evidence,
not the still-required equivalent-model live sponsor evaluation. Do not tune these held-out
cases and call the same cohort unseen in a later release.

## Prediction ledger audit

Audit completed archives against their frozen protocol and actual execution events:

```bash
uv run python scripts/audit_prediction_ledger.py .preact/protocols/held-out.json \
  reports/cohort-held-out --output reports/ledger-alignment.json
```

The auditor checks membership, fixture/policy hashes, ordered events, observed first-action execution, receipts,
input state/action identity, observation authority, metric versions, contextual engine
versions, success/risk labels and Brier scores. Comparisons and error rows must belong
to executed nodes. Every executed one-action prediction requires a comparison; known
success forecasts with matching metric versions require durable error labels. Unknown
or mismatched forecasts do not acquire invented labels. Unexecuted predictions remain
unlabeled. It fails on incomplete
cohorts; `--allow-partial` produces an explicitly partial snapshot for a running pilot.
Choose a new output path for each audit; existing reports are never overwritten.
This validates archived alignment, not new inference or sponsor infrastructure.

The actual-model v1 pilot snapshot audits 275/384 episodes, 680 aligned error rows and
1,678 unexecuted predictions without observed labels. See
[dated evidence](../reports/model-pilot-ledger-audit-v1.json). The auditor rejects
[in-memory corrupted copies](../reports/ledger-auditor-rejection-checks.json) with false
labels, unexecuted-branch labels, mismatched receipts and duplicate event sequences.
The original 384-episode pilot and accepted 1,920-episode five-seed replication are complete; real sponsor and public-release gates remain open.
The [expanded first-action audit](../reports/ledger-auditor-first-action-checks.json)
also passes all 24 model-calibration and 1,920 heuristic held-out episodes and rejects
a tampered hypothetical execution. One calibration forecast declares both probabilities
unknown; its actual outcome remains recorded without invented probability-error labels.
The [completeness audit](../reports/ledger-auditor-completeness-checks.json) passes
those complete cohorts and a 316-episode pilot snapshot. It rejects removal of both a
known label and its event, and removal of an executed comparison. Original archives
remain unchanged; the partial snapshot does not certify the full model evaluation.

## Complete actual-model pilot and repaired replication

The [v1 analysis](../reports/cohort-model-pilot-analysis-v1.md) and
[full summary](../reports/cohort-model-pilot-summary-v1.json) retain all eight
conditions, task failures, unsafe outcomes, the timeout, unknown costs and paired
intervals. The [complete ledger audit](../reports/model-pilot-ledger-audit-complete-v1.json)
checks all 384 archives, 1,057 error rows and 3,200 unexecuted predictions without
observed labels. Engine scoring is reproducible with:

```bash
uv run python scripts/analyze_cohort.py reports/cohort-model-held-out-v1   --output reports/cohort-model-pilot-engine-scores-v1.json
```

After sealing v1, the validated generic proposal/accounting repairs receive a new
source/model version and fresh five-seed calibration. V2 retains the same actions,
fixtures, evaluator, hard gates, sampling and 300s ceiling. It repeats already exposed
pilot/heuristic instances; treat it as replication, not a newly blind task population.
The [full 1,920-episode analysis](../reports/cohort-model-analysis-v2.md) preserves
all eight conditions, six Physical timeouts, 494 unsuccessful episodes, calibration
losses and unknown costs. Actual sponsor infrastructure remains a release gate.

## Reproduce complete findings

After a cohort seals, reproduce its metrics, paired intervals, per-engine calibration,
every unsuccessful episode and complete ledger audit from the original archives:

```bash
uv run python -m scripts.summarize_cohort benchmarks/model-held-out-protocol-v1.json \
  reports/cohort-model-held-out-v1 --output reports/model-pilot-reproduced-findings-v1.json
```

V2 is complete. Use its frozen protocol/directory and a new output path to reproduce
[all five-seed findings](../reports/model-v2-reproduced-findings.json); preserve the
original files. Its [private bundle proof](../reports/model-v2-bundle-validation.json)
checks CRC/member hashes and actual recorded imports, with no new truth or execution.
The command requires every planned task/seed/condition and checks aggregate rows,
error labels, metrics and paired intervals against archived data. Missing archives,
inconsistent reports and an existing output fail explicitly. It performs no inference
or execution and preserves the historical source/model identity. All eight conditions,
unknown costs and unfavorable engine scores remain visible. The reproduced v1
[findings](../reports/model-pilot-reproduced-findings-v1.json) cover all 384 episodes;
repeated authored tasks still do not become newly blind evaluation data.

## Package recorded evidence for review

Create a private replay bundle only after the cohort is complete:

```bash
uv run python -m scripts.bundle_cohort benchmarks/model-held-out-protocol-v1.json \
  reports/cohort-model-held-out-v1 \
  --calibration reports/cohort-model-calibration-v1/report.json \
  --output .cache/benchmark-review/model-pilot-complete-v1.zip
```

The exporter reproduces/audits findings, requires the exact frozen calibration
snapshot, and includes original episode events, report, protocol, referenced
content-addressed artifacts, attribution and a member checksum manifest. Missing
evidence or an existing output fails; unrelated files, weights, private runner
configuration and unreferenced artifacts are outside its member list. Export is
bounded to 32 MiB per member and 4 GiB input; ZIP ordering/timestamps are fixed.

After unpacking this owned bundle into a review directory, use
`uv run preact replay review/cohort/<episode>.json`; artifacts resolve from its
adjacent `artifacts/` directory. Replay imports recorded history and never performs
new execution or adds calibration labels. The included calibration report preserves
the prior data used; it is not a fresh calibration run. This archive is private
review material until its exact payload receives publication approval. Inspect
rights/disclosures before uploading; member integrity is not complete secret scanning
or live sponsor certification.
