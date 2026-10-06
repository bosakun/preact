# Release Record

Updated October 6, 2026 (JST). **Not released or submitted.** The accepted architecture
remains in [implementation-plan.md](../implementation-plan.md); requirement IDs and
acceptance mappings remain in [submission-checklist.md](../submission-checklist.md).
Historical checkpoints are preserved in [agent-progress.md](../agent-progress.md),
Git history and their original reports; they are not evidence of the latest release.

## Current source and local evidence

Main runtime source is now
`44a3b88efeeef1e2daafb724a98d18c2503c2c5c6404864b1da396e416c7d9b1`.
Both defensive evaluator commits applied only after the original five-seed cohort
sealed and passed complete alignment. Main regression passes **330 tests in 32.14s**,
with lint/format covering **116 files**. [Defensive proof](../../reports/defensive-evaluator-bindings.json)
records actual source and log hashes. The benchmark retains its earlier source
`5720e2416adbf466488ce3aec800ac22906288e5b5b473eaeff0c36b5227415e`;
its archives and failures were preserved.

| Scope | Actual validation | Evidence |
| --- | --- | --- |
| Prior benchmark-source operational paths | Locked private checkout: 278 tests, unchanged contracts/build, five fresh-SQLite browser checks. Rebuilt non-root PostgreSQL app: five browser checks. Installed wheel: 87 boundary checks and safe two-world/release episodes. | [Operational proof](../../reports/proposal-repair-release-validation.json) |
| Prior private checkout | Pinned private clone imports its own sources; offline frozen Python/npm setup, 304 tests (77.79s), lint/format 115 files, unchanged contracts, TypeScript/build and six fresh-SQLite browser cases (15.6s). No public clone or remote CI claim. | [Checkout proof](../../reports/clean-checkout-trust-ui-validation-v1.json) |
| Distribution integrity | Repaired archive excludes private caches; actual wheel/sdist source, assets and licenses pass. Thirteen archive regressions and the then-current 291-test suite pass. Remote CI remains unexecuted. | [Archive proof](../../reports/distribution-integrity-v1.json) |
| SDK import bridge | Sixteen Python 3.11 CPU cases pass. No NVIDIA simulator or GPU ran. | [Bridge proof](../../reports/proposal-repair-sdk-bridge.json) |
| Prior renderer checkpoint | TypeScript/build and six fresh local browser cases pass (12.3s). First actions are readable; decision-scoped trust matches actual first/second-round events, and unexecuted branches/earlier replay have no later update. Actual exported model histories pass without new writes. Non-root PostgreSQL image passes six browser cases (17.9s), preserving prior runs. | [Recorded trust proof](../../reports/model-pilot-bundle-trust-browser-v1.json), [production proof](../../reports/trust-round-production-validation-v1.json) |
| Applied defensive fix | Immutable approved operation/helper bindings; 26 rejected structural fixtures remain unexecuted. Both commits applied to main; all 330 tests, lint/format 116 files pass. | [Binding proof](../../reports/defensive-evaluator-bindings.json) |
| Installed defensive package | Exact post-mitigation source, locked fresh environment, 32 copied regressions; safe repair/Physical/release episodes, nine actions and 18 aligned errors. | [Installed proof](../../reports/defensive-bindings-installed-wheel.json) |
| Applied-source release operations | Private clone `76b6123`: 330 tests (38.74s), lint/format 116 files, identical contracts/assets, build (1.07s), six fresh-SQLite browser cases (7.5s). Actual non-root PostgreSQL image: six browser cases (8.1s); all 116 prior histories preserved. | [Release proof](../../reports/defensive-release-validation.json) |
| Applied-source private distributions | Fresh offline wheel/sdist build and source/assets/license/private-path audit pass. Wheel bytes exactly match the prior installed defensive proof; its execution evidence is reused with that scope. Source archive pins `484f2b5`; no publication. | [Distribution proof](../../reports/defensive-distribution-validation.json) |

The installed defensive wheel and rebuilt local production container both match
the applied runtime source. The actual current image is
`sha256:36d1a6ae40625ee92eb309c23502be391bc5c116df93288c9ee380585815a99d`,
healthy under UID 10001, Python 3.12.15. Its served asset bytes match the private
Python 3.12.13 checkout. Tests add six runs, 16 executions and 30 aligned errors
without changing any prior run hash. This is loopback operational evidence, not
public or Nebius deployment. Private local footage is validated; final sponsor/category footage and public release remain open.
The current renderer fixes measured initial-tree readability and runs in the actual
local production image. Trust evidence is scoped to the inspected decision rather
than later-round updates. Earlier screenshot and footage receipts retain their original
frontend scope. Historical model
replay in the current UI is not new inference or human comprehension validation.
The guarded main binding regression gate now passes. Live admitted Sandbox
acceptance remains required before claiming cloud operation. Rejected structural
fixtures are never executed; these checks do not certify Sandbox isolation or
arbitrary-code security.

## Model benchmarks and footage

Actual official NVIDIA Nemotron 3 Nano 4B Q4_K_M inference runs through shared Core
on Apple M5 Metal. This is distinct from Nebius inference, Cosmos prediction and
Isaac GPU simulation. Model/checkpoint/runner identities are in the linked reports.

The original source's **384-episode, one-seed pilot is complete**, with wins, losses,
Physical abstentions/timeout, worse selected-success calibration and substantial
latency. [Pilot analysis](../../reports/cohort-model-pilot-analysis-v1.md) and
[reproduced findings](../../reports/model-pilot-reproduced-findings-v1.json) preserve
all eight conditions and their original source/model identity.
A complete private 17.5 MB bundle now preserves all 384 episode archives, 4,257
referenced artifacts and the exact calibration snapshot. CRC/member hashes and
actual two-world recorded replay pass without new execution or calibration labels;
see the bundle proof above. Export is not publication or fresh inference.

Repaired v2 **five-seed calibration completes all 120 episodes** and passes complete
ledger alignment: [calibration proof](../../reports/cohort-model-calibration-summary-v2.json).
Its **1,920-episode held-out/ablation comparison is complete**, with reproduced
[findings](../../reports/model-v2-reproduced-findings.json), [analysis](../../reports/cohort-model-analysis-v2.md)
and [complete alignment](../../reports/model-v2-ledger-audit-complete.json). Software
Direct/PreAct complete 105/120 versus 120/120; Physical 45/120 versus 90/120.
PreAct has zero observed unsafe episodes, 24 Physical abstentions and six timeouts;
several ablations complete 96/120. Selected model calibration worsens; costs remain
unknown. Both iterations reuse exposed authored instances, not a newly blind population.

The private **88,944,340-byte** bundle retains 1,920 archives, 21,511 referenced
artifacts, exact calibration and complete findings. CRC and all 23,437 member hashes
pass. Actual two-world recorded imports preserve payloads and add zero executions,
labels or jobs; see [bundle validation](../../reports/model-v2-bundle-validation.json).
Configured-key absence is bounded, not comprehensive disclosure review. Export is
not publication. [Separate bounded timeout diagnosis](../../reports/model-v2-deadline-diagnosis.json)
completes safely for both PreAct (142.3s) and frozen trust (159.1s) under the original
ceiling. It does not replace benchmark rows or establish causal latency effects.

Latest private model review footage decodes to **148.52 seconds / 3,713 frames**.
Actual local Nemotron reasoning and Python/MuJoCo verification execute both worlds
safely through Core; five first actions, ten aligned errors and 28 unlabeled
alternatives are audited. Saved/durable event hashes match. Waiting is explicitly
compressed 11.0x/26.7x; the original 291.48s raw footage/events remain intact. Current
UI, labels and failure/calibration claims were visually checked. [Model footage proof](../../reports/local-model-defensive-footage.json)
records source/model/run/event/media hashes and actual decoded duration. The initial
edit's scheduling fault was repaired, and its rejected output remains private.

This footage is local model/Python/MuJoCo evidence, not Nebius inference, Cosmos,
Isaac GPU, hardware, public YouTube or Physical-track qualification. Earlier 147s
Python-only and 144/142s clips retain their original scopes. Final technology/category
footage still requires actual integrations; follow the [final video plan](video-script.md).

## Remaining release gates in order

1. The separate deadline diagnosis and applied-source checkout/container/browser
   checks and actual model footage pass; all original benchmark failures remain.
   The pinned private source archive and complete-history bundle are verified through
   an actual clone/setup/import. Review the updated exact candidate before public
   approval; private artifacts do not establish public release or sponsor acceptance. The completed cohort, complete
   audit, reproduced findings and private replay proof retain historical identity.
2. Validate actual supported Token Factory identifiers/inference, admitted ConTree
   write/run/test, and accepted Nebius Jobs/storage/compute paths. Validate compatible
   action-conditioned Cosmos and Isaac GPU workflows with measured provenance.
   Missing credentials, beta admission, quota and GPU workers currently block these.
3. Run the [five-viewer study](../usability-study.md); all participant results remain
   uncollected. Provide the actual hosted judge path, external access checks, recovery
   rehearsal and funded availability. Configuration alone is not deployment evidence.
4. Publish the exact approved source/assets/license payload; run anonymous setup and
   remote CI. Produce the public English YouTube video under 180 seconds, matching
   release claims and permitted media. Complete description, Built With, actual-use
   feedback and judge instructions using demonstrated integrations only.
5. Confirm entrant registration, eligibility, prohibited conflicts, representation,
   ownership/third-party rights and development-period provenance. Inspect the real
   submission form, recheck official Rules/updates, submit and preserve the receipt.

Primary category is Coding and Agentic Engineering; real Token Factory write/run/test
and accepted ConTree verification remain its gates. Best Apps and Agents is the
accepted alternative if Sandbox admission fails, requiring actual Nemotron through
Token Factory. Both worlds retain shared Core. The optional Physical category also
requires its Jobs/operating-module footage conditions. Freeze category from actual
evidence, not dormant adapters. Personal AI and unqualified bonuses are not claimed.

## Public candidate and archived provenance

This publication candidate starts new Git history from the cleaned source tree.
The 177-commit private development history is preserved separately and is excluded.
A non-personal project identity is used. Historical revision hashes in reports
identify original private validation and benchmark runs; they are not ancestors of
this public repository. Machine-specific paths in copied receipts are normalized
with explicit notes. Measurements, failure counts and source/model/protocol/
artifact identities retain their original scopes; originals remain private.

Private caches, credentials, databases, model weights, raw evaluation archives,
benchmark review bundles and footage are excluded. Public summaries, calibration
snapshots, audit receipts, owned UI screenshots and third-party notices are retained.
Fresh staged checks and exact candidate hashes belong to the publication audit.

No remote repository, public push, judge deployment or YouTube upload has occurred.
Proposed destination remains `bosakun/preact`. Publication requires explicit approval
of the exact cleaned candidate after the audit. Manual entrant, employer/IP ownership,
media rights, selected-track and funded-access confirmations remain open.

Internal freeze target: **October 29, 2026 17:00 UTC**. Official deadline:
**October 30, 2026 17:00 UTC / October 31 02:00 JST**. Judge access must remain free
and operational through **December 15, 2026 20:00 UTC**. Rules and the actual form
override stale assumptions; public preparation does not certify submission readiness.

Fresh staged installation, regression, browser, package, CPU bridge and local-cohort
checks are recorded in [public candidate validation](../../reports/public-release-validation.json). These checks
do not close live sponsor, GPU, hosting, publication or submission gates.
