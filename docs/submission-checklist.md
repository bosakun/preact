# Final Acceptance Checklist

Source: [requirements inventory](hackathon-requirements.md). Empty boxes are deliberate:
local tests do not certify cloud technology, eligibility or submission. Unchecked boxes
identify incomplete acceptance, including live infrastructure and administration. Audit each ID at M12, retain dated evidence.

Public requirements rechecked October 6 against Overview, Rules, Resources, Updates,
Dates and the linked judging/winning-project/kickoff announcements. The Updates index
still lists seven announcements. No applicable gate was relaxed; recorded conflicts
remain governed by Rules. The October 7 maintenance notice is not a deadline extension.

| IDs | PreAct feature / requirement | Implementation location | Verification method | Submission artifact / status |
| --- | --- | --- | --- | --- |
| H1, S1 | Registration and complete fields on time | docs/submission/release.md | Entrant registration + receipt timestamp | [ ] Devpost receipt |
| H2, J0 | Working counterfactual runtime in two worlds | src/preact/core, domains; web | Independent clean setup, live two-world runs | [ ] Release and operational demo |
| H3, I1 | Real Nebius runtime | engines/nebius.py, sandbox.py; deploy | Successful requests/jobs with IDs and artifacts | [ ] Live integration evidence |
| H4, I2 | NVIDIA model materially predicts/ranks futures | engines/nebius.py, llamacpp.py; workers/cosmos | Actual model/version/usage and effect on decision; local Metal reasoning is separate from cloud/GPU simulation | [ ] Release-matched model evidence + narration; local development inference verified |
| H5, T1 | Coding category, real write/run/test loop | domains/cloud.py; engines/sandbox.py | Sandbox sibling branch + authority repair round trip | [ ] Category + footage |
| T2 | Alternative Best Apps track uses Nemotron | engines/nebius.py; service/app.py | Real Token Factory Nemotron run | [ ] Freeze category if needed |
| T3 | Personal AI scope | implementation-plan.md | No claim of Personal AI qualification | [ ] Confirm not selected |
| T4 | Optional Physical track / retained Physical demo | domains/cloud.py; workers/isaac; deploy/jobs | Real simulator actions + Jobs IDs; ≥60s modules footage | [ ] Physical footage, selected-track obligations |
| T5 | Optional bonus categories | docs/submission/release.md | Actual Tavily use/attendance/feedback evidence if claimed | [ ] Bonuses omitted unless qualified |
| H6, D2 | Public complete licensed repository | LICENSE, src, assets, workers, web | Anonymous clone; platform detects Apache-2.0 in About | [ ] Public repository URL |
| H7, D3, I5 | Setup, operation, integration guidance | README.md; docs/integrations.md | Clean machine follows instructions successfully | [ ] README + exact technology roles |
| H8, D1, H12 | Operational, free judge test access | service/app.py; deploy; docs/submission/testing.md | External URL tests incl. private credentials if needed | [ ] Hosted URL + judge instructions |
| H9, D5 | <180s public YouTube real demo | docs/submission/video-script.md | Duration/visibility/rights + footage-to-release audit | [ ] YouTube URL |
| H10, D4 | Description, features, category | docs/submission/description.md | Every statement backed by release behavior | [ ] Devpost description |
| H10, D6, G4 | Tool-specific feedback from real use | docs/submission/feedback.md | Dated real issue/onboarding examples, no invented experience | [ ] Feedback fields |
| H11 | English materials | README; docs/submission; UI | Language audit and captions/translations if needed | [ ] English text/video/instructions |
| S2, H12 | Service available through Dec 15 20:00 UTC | deploy; docs/submission/operations.md | Scheduled availability, funding and recovery rehearsal | [ ] Operations/access evidence |
| S3 | Deadline freeze and permitted edits | docs/submission/release.md | Hash release + artifacts; check organizer policy | [ ] Frozen tag/manifest |
| S4, D7 | New/significant-update provenance; one system | docs/submission/release.md; git history | Dates and actual pre-existing-work declaration | [ ] Development-period explanation |
| S5 | Entrant eligibility/representative/rights | docs/submission/release.md | Entrant attests age, residence, conflicts, authority, ownership | [ ] Administrative attestations |
| I3 | Dependency/model/asset rights | LICENSE; docs/third-party.md; locks | License/model terms and permitted media review | [ ] Attribution/license inventory |
| I4 | Selected track's sponsor obligations | implementation-plan.md; release checklist | Recheck current Rules and actual integrations | [ ] Verified track selection |
| J1 | Technological implementation | Core, domains, engines, workers | Shared runtime, tree/gate/error tests + live workers | [ ] Architecture and measured demo |
| J2 | Coherent design | web/src; web/e2e; docs/usability-study.md | 4/5 unfamiliar viewers distinguish evidence within 30s using the fixed rubric + browser checks | [ ] Human results/screenshots; protocol prepared only |
| J3 | Specific potential impact | benchmarks; docs/submission/description.md | Paired tasks incl. failures/abstention/cost | [ ] Report with limits and intended users |
| J4 | Quality of idea | Core search/calibration; same-domain contracts | Distinct multistep consequences + trust affects later decision | [ ] Evidence, tree and ledger footage |
| G1 | Problem/audience/use case | README; description; video | Demonstrates repository and embodied action risk | [ ] Problem narrative |
| G2, G3 | Model names, roles and concrete benefit | README; description; video | Match actual request/version IDs, no cosmetic calls | [ ] Built With + narration |
| A1, A2 | Rules override city/hardware summaries | hackathon-requirements.md | Rules reread; attendance/hardware claims checked | [ ] Ambiguities closed |
| A3, A4 | Sandbox wording/access, credits and judging capacity | docs/integrations.md; operations.md | Beta access and funding confirmed; stricter Coding interpretation | [ ] Capability/funding evidence |
| A5, A6 | Authenticated form and unavailable discussions | docs/submission/release.md | Inspect real fields; retry official pages before freeze | [ ] Final official-source audit |
| A7 | Model/GPU/service applicability and quota | workers; docs/integrations.md | Real checkpoint memory/latency/frame/action compatibility | [ ] Live feasibility report |

## Product acceptance beyond organizer requirements

- [ ] Both worlds execute through one Core; no alternate domain-specific decision loop.
- [ ] Depth-three search changes a first-action choice; bounded work and stale-state protection.
- [ ] Disagreement, missing measurements and high uncertainty trigger useful escalation or abstention.
- [ ] Hard violations veto; failing infrastructure never relaxes authorization.
- [ ] Executed futures receive aligned errors; alternatives remain unlabeled.
- [ ] Historical errors alter trust and subsequent verification; held-out calibration is assessed.
- [ ] Real Cosmos action-conditioned futures and real Isaac contacts/action execution are recorded.
- [ ] Full held-out paired benchmarks, ablations and reproducible manifests are published.
- [ ] Restart, cancellation, PostgreSQL durability, artifact integrity and cloud worker isolation validated.

## Current local evidence (not release certification)

- Applied defensive source `44a3b88e…` passes **330 main tests (32.14s)** and
  lint/format 116 files. Both commits applied after the complete frozen comparison
  sealed; 26 structural negative fixtures never execute. See
  ../reports/defensive-evaluator-bindings.json. Live Sandbox acceptance is still open.
- V2 completes **1,920 episodes**, all eight conditions and 120 calibration episodes.
  Complete alignment verifies 5,385 errors and 16,119 unexecuted predictions without
  labels; all 1,920 archive bytes and 1,847 prior hashes were independently rechecked.
  Reproduced findings retain 494 unsuccessful episodes, six Physical primary
  timeouts, ablation losses, worse model calibration and unknown costs. See
  ../reports/model-v2-ledger-audit-complete.json and ../reports/cohort-model-analysis-v2.md.
- The private 88,944,340-byte v2 bundle passes CRC/all 23,437 member checksums and
  actual two-world recorded import, with zero executions, labels or jobs. See
  ../reports/model-v2-bundle-validation.json. This does not satisfy publication,
  real sponsor infrastructure, GPU acceptance or a newly blind population.
- Applied-source private clone passes 330 tests (38.74s), lint/format 116 files,
  identical contracts/assets, build and six fresh-SQLite browser cases (7.5s).
  Actual non-root PostgreSQL image passes six browser cases (8.1s), preserves all
  116 prior histories and adds six test runs. See
  ../reports/defensive-release-validation.json. Fresh private distributions pass integrity, and actual model footage decodes to
  148.52s with explicit compressed waiting; five actions/ten aligned errors/28
  unlabeled alternatives audit. See ../reports/defensive-distribution-validation.json
  and ../reports/local-model-defensive-footage.json. Pinned `b9a38f8` artifacts additionally pass full 333-file source-archive matching,
  complete-history bundle validation and actual fresh clone/setup/import; see
  ../reports/local-release-artifact-freeze.json. Exact public approval and external
  release gates remain; older receipts retain their scope.

## Completion audit against the accepted goal

October 6 audit. "Local verified" describes executed evidence, not full milestone
acceptance. Sponsor boundaries have fixture tests; their live gates remain open.
Architecture and milestone dependencies remain those in implementation-plan.md.
Main runtime source is `44a3b88e…`, with applied defensive mitigation and all 330
main tests passing. The completed five-seed cohort retains its original source
`5720e241…`. Current evidence is linked below; older checkpoints retain their scope.

| Goal | Implementation and actual evidence | Remaining acceptance / artifact |
| --- | --- | --- |
| 1 Shared Core | `core/models.py`, `interfaces.py`, `runtime.py`, `registry.py`; both local worlds and actual local NVIDIA-model episodes run this loop. `test_core_independence.py` prohibits domain/vendor imports; two-world model archives and installed-package episodes are retained. | Real sponsor/GPU runs must preserve this loop; retain their original event archives and version evidence. |
| 2 Search | `core/runtime.py`: depth-three bounded widening, stochastic branches, reuse/pruning/backups and first-action traces. `test_search.py`, authority/deadline tests and physical flat-search ablation run. | Cloud latency/budgets must support measured multistep verification; do not relax safety to fit cold starts. |
| 3 Verification | `core/verification.py`, `decision.py`; allocation tests exercise stakes, coverage, reliability, disagreement and declared price/latency. Missing verifiers abstain. Actual model pilot and local cohorts retain verification use, abstentions and latency losses. | One authoritative measured engine per local world; cloud allocation performance and causal adaptive-verification gains remain unproven. Real service profiles and causal component benefits remain unverified. |
| 4 Gate | `core/decision.py`, runtime authorization/receipt checks; violation veto, uncertainty, completion and stale/post-intent expiry tests pass. | Confirm the same authority checks with real Sandbox/Isaac execution receipts. |
| 5 Ledger/calibration | `core/store.py`, `comparison.py`, `calibration.py`; SQLite/PostgreSQL durability, deduplication, contextual trust/drift and no alternative labels tested. Complete original model pilot audits 1,057 errors and 3,200 unexecuted predictions without labels (`model-pilot-ledger-audit-complete-v1.json`); v2 calibration audits all 120 episodes, and all 1,920 v2 evaluation archives audit 5,385 errors/16,119 unlabeled alternatives. | Selected-success calibration losses and poor physical risk forecasts remain in pilot findings. V2 ablations and calibration losses are reported; real Nebius calibration transfer is unmeasured. |
| 6 Software | `domains/software.py`, `repository.py`, `program_probe.py`, `release_probe.py`; actual repair, SQLite migration/config/build and protected randomized evaluation execute. `domains/cloud.py` binding fix is applied to main; 26 structural negative cases, 330 main tests and installed-wheel validation pass. | Real admitted Sandbox/checkpoint workflow remains unvalidated; arbitrary dependency-resolution repositories remain outside current bounded adapter scope. |
| 7 Physical | `domains/physical.py`, `scene.py`; native Cartesian MuJoCo actions/observations flow through Core. Authority transaction tests use injected SDK with real SQLite/PostgreSQL. | Franka sensing/contact/execution and visual applicability require a real GPU worker; local weld/mocap is explicitly labeled. |
| 8 Nebius | `engines/nebius.py`, `sandbox.py`, `jobs.py`, `storage.py`; catalog/request contracts, Sandbox branching and 16 Jobs lifecycle cases tested. Actual Token Factory access command records missing-key blocker. | No configured inference key/model, Sandbox admission, cloud token/project or S3 credentials/profile. Real request/job/artifact evidence is absent; keep M1/M3/M8 open. |
| 9 NVIDIA | `engines/llamacpp.py`: actual official Nemotron 3 Nano 4B Q4_K_M inference on Apple M5 Metal, pinned checkpoint/conversion/build/usage evidence (`local-nemotron-feasibility.json`, `proposal-repair-validation.json`). `engines/isaac.py`, `sdk_bridge.py`, `cosmos.py`, `workers/` preserve distinct measured/visual claims; 16 Python 3.11 CPU bridge cases pass. | Local NVIDIA model use is genuine; it is not NVIDIA GPU, Cosmos or Isaac validation and does not satisfy Nebius runtime requirements. Compatible GPU endpoints/checkpoint/worker access, actual Isaac conformance and Cosmos conditioning/predicate extraction remain open. |
| 10 Frontend | `web/src/`: shared actual tree/events, readable first actions, expandable descendants, evidence/error and decision-scoped trust. Six browser cases pass on applied-source fresh SQLite (7.5s) and actual rebuilt PostgreSQL image (8.1s); see `defensive-release-validation.json`. Real model archive replay matches exact trust payloads without new execution/calibration rows; see `model-pilot-bundle-trust-browser-v1.json` and `trust-round-production-validation-v1.json`. | 4/5 unfamiliar-viewer comprehension study, live sponsor media and judge-facing URL remain open. Automation is not human usability evidence. |
| 11 Benchmarks | `datasets/`, `cohorts.py`, frozen protocols; complete heuristic comparisons, original 384-episode model pilot/all eight conditions, reproduced paired intervals/per-engine scores/all failures, complete private bundle and actual recorded replay. Repaired v2 calibration completes 120 episodes; all 1,920 evaluation episodes, complete audit, reproduced findings and private recorded bundle are validated. Six primary Physical timeouts and ablation/calibration losses remain. | Bounded diagnosis completes safely without replacing any frozen deadline failure (`model-v2-deadline-diagnosis.json`). Actual Nebius comparisons and broader independently collected repositories/perception remain open. Existing authored instances were exposed; reruns are not a newly blind population. |
| 12 Reliability | `tests/`, scripts: main Python regression passes 330 tests (32.14s), lint/format 116 files on applied defensive source. Applied-source pinned private clone independently passes all 330 tests (38.74s), lint/format 116 files, contracts/build and six fresh-browser cases (7.5s). Installed main/defensive packages and PostgreSQL/restart/failure proofs retain exact scopes. | Fresh distributions and actual model footage pass with pinned scope; pinned source archive/history bundle and actual clone/setup/import pass (`local-release-artifact-freeze.json`). Applied-source checkout and actual local container/browser checks pass. Remote CI, anonymous public setup and real sponsor failure paths remain unexecuted. |
| 13 Operations | `deploy/`, local non-root app/PostgreSQL: applied-source image healthy, six browser cases pass, all 116 prior histories preserved (`defensive-release-validation.json`); earlier real health/outage recovery, backup/restore and artifact hashes retain their scope. Terraform provider schema validates configuration only. | No public deployment, anonymous judge path, funded inference/GPU capacity or cloud/S3 restore. Never mark these complete from configuration. |
| 14 Rules | Eight-section `hackathon-requirements.md` and requirement-ID table cover Rules, Resources, dates, judging, updates and linked organizer clarifications, officially rechecked October 6. Maintenance notice is not a deadline extension. | Recheck before release; authenticated form, unavailable discussion pages and administrative ambiguities remain open. |
| 15 Submission | Apache-2.0, SPDX wheel metadata, locked notices, setup/testing/Built With/actual-use feedback/English description/video/operations drafts. Latest private actual-model clip is 148.52s (`local-model-defensive-footage.json`), with current source/model/run/event/video hashes, audited five actions/ten errors/28 unlabeled alternatives and explicit compressed waiting; raw video is retained. | Actual sponsor/category footage and public repository/YouTube/judge URLs, entrant eligibility/rights/registration, sponsor footage/feedback and final receipt remain absent. The clip is not continuous robot/Isaac operation or Physical-track qualification. Claims must match evidence. |
| 16 Persistent state | `implementation-plan.md` remains source of truth; `agent-progress.md` records repairs, actual checks, failures, blockers and next work. The public AGENTS.md describes the current repository and retains autonomous-development rules. | Keep documents/source/protocols aligned through release; do not reinterpret missing integration as completion. |

Current external gates require account/service access, compatible GPU resources, publication
approval or entrant administrative action. No configured access was discovered by the
non-secret environment/profile audit. No paid resources have been provisioned. Public
publication was rejected by automatic approval review and has not been retried or bypassed.
These external gates remain separate from local publication preparation.

## Publication provenance

This candidate uses new public history; private development history is excluded.
Retained report source revisions identify archived executions and are not public
Git ancestors. Original private receipts are retained privately. Machine paths in
public receipt copies are normalized and explicitly marked; measured outcomes,
model/source/protocol identities and artifact hashes are unchanged. The public
publication audit records the staged validation scope and remaining manual gates.

Fresh staged installation, regression, browser, package, CPU bridge and local-cohort
checks are recorded in [public candidate validation](../reports/public-release-validation.json). These checks
do not close live sponsor, GPU, hosting, publication or submission gates.

## October 8 — Composable Runtime local architecture evolution

[Runtime semantics](composable-world-model-runtime.md) and
[local validation](composable-world-model-runtime-validation.md) supersede the
narrow product framing. Claim-specific heterogeneous routing, separate Prediction/
Observation evidence and bounded no-intervention consequences pass actual local
shared-Core tests/demos. This does not close hosted provider, GPU, public release
or administrative submission gates. Existing historical evidence remains unchanged.
