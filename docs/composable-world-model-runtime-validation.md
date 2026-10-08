# Composable Runtime local validation — October 8, 2026

This records executed local evidence for the
[accepted plan](composable-world-model-runtime-plan.md) and
[implemented architecture](composable-world-model-runtime.md). No public source,
cloud deployment, merge or submission was performed.

## Final checks

Use `UV_CACHE_DIR=.cache/uv` for offline uv commands in this checkout.

| Executed command | Actual outcome |
| --- | --- |
| `uv run --offline pytest -q` | **361 passed**, 34.83s; includes actual protected software and MuJoCo, new claim/routing/consequence/sequence/observation/authorization regressions |
| `uv run --offline pytest -q tests/test_claim_routing.py tests/test_observation_sources.py tests/test_worker_claim_compatibility.py tests/test_sandbox_release.py tests/test_sandbox_checkpoints.py` | **17 passed**, 2.93s; final scope/source and SDK boundary checkpoint |
| `uv run --offline ruff check src tests workers scripts` | Passed |
| `uv run --offline ruff format --check src tests workers scripts` | 127 files already formatted |
| `git diff --check` | Passed |
| `npm --prefix web run contracts` | Shared Pydantic JSON schema and TypeScript regenerated successfully |
| `npm --prefix web run build` | TypeScript and Vite production assets passed |
| `npm --prefix web run test:e2e` | **6 Chromium cases passed**, final run 8.9s, actual local API with fresh SQLite |
| `uv run --offline preact demo software` | Complete, safe, 2 actions with observations between actions |
| `uv run --offline preact demo physical` | Complete, safe, 3 actions with observations between actions |
| `uv run --offline preact demo software --task release` | Complete, safe, 4 actions with observations between actions |
| `uv run --offline preact benchmark --seeds 5 --ablations --workflows --output .cache/composable-validation/local-regression-sealed` | **120 episodes** completed; separate current-source local regression, not a newly blind population |
| `uv run --offline python -m scripts.verify_composable_runtime --output .cache/composable-validation/consequence-fixture-sealed` | Passed; rejects immediate-safe delayed overflow, executes bounded action once, retains protocol/events/ledger |
| `uv run --offline python scripts/verify_isaac_bridge.py --python .cache/isaac-import-env/bin/python --report .cache/composable-validation/sdk-imports-sealed.json` | 16 Python 3.11 CPU bridge cases passed; no NVIDIA validation |
| `uv build --offline --out-dir .cache/composable-validation/distributions-sealed` | Wheel and sdist built |
| `uv run --offline python scripts/audit_distributions.py .cache/composable-validation/distributions-sealed --output .cache/composable-validation/distribution-audit-sealed.json` | Passed source/assets/license/private-path integrity; 58 Python sources match; not installed execution or complete secret scanning |

E2E initially could not bind localhost under the sandbox. A permitted local execution
escalation resolved this; no external listener/publication was configured. Runs use
`PREACT_MODE=local`, fresh `PREACT_DATABASE_URL` and `PREACT_ARTIFACTS_DIR`. Set
`PREACT_E2E_ARTIFACT_DIR=../.cache/composable-validation/browser` to preserve historical
tracked screenshots. Vite retains the upstream ignored `use client` directive warning;
it does not fail TypeScript or asset generation.

## Semantic acceptance

New tests demonstrate separate Claim Definition/Instance identity, exact definition
matching, task-local legacy checks, role applicability, predictor/simulator/narrow
verifier cooperation (including three distinct roles in one actual run), cache/source deduplication and conservative family handling.
Executable software check verifiers cannot resolve other checks or success/risk.
Registry preserves requested Claim Instances on each accepted Prediction; conditional
horizon-1 legacy fields and unrequested checks neither solve unconditional claims nor
receive comparison/calibration labels. The regression executes the actual Software
Runtime and checks its emitted predictions, comparisons and prediction errors.

The new queue dynamics demonstrates no-intervention delayed overflow at horizon 3.
Immediate risk remains 0 in its separate summary; the future check rejects fast
input. Missing future verification yields VERIFY_MORE/ABSTAIN. A versioned mandatory
check at horizon 1 also stays a separate obligation. Three-action software sequence
lineage is verified, and its optional harmful last patch does not veto the safe root.
Ambiguous lists, forged source/state bindings and missing explicit source context
fail closed. Reuse declines unknown assumptions or mismatched context/depth.

Observation evidence references actual completed receipt/outcome separately from
Prediction. Pending/other-action/unexecuted/long-horizon evidence receives no actual
label. Calibration remains aligned immediate-only; long-horizon trust is not certified
from immediate errors. Post-intent changes to State contents, Action, required claims,
source records, Evaluation/evidence IDs or Policy prevent dispatch.

## Failures found and repaired

- Duplicate Prediction entries created spurious zero-disagreement entries. Deduplicate
  before evaluation and reject conflicting reuse of one source identity.
- Observation event projection treated a RunEvent as a dict. Use its typed seq.
- A final full-suite run exposed an API timeout-status race: interrupted Runtime
  results could temporarily lack cost_known before outer cleanup. Stamp unknown cost
  immediately on cancellation/error; retain pending execution and add a direct
  cancellation regression. The failing run was 355 passed / one failed; the final
  361-test suite passes. No gate or test deadline was weakened.

- Claim-scope review found legacy envelope fields could leak into unconditional
  findings/calibration after a conditional horizon-1 request. Registry now captures
  exact admitted scope; projection, comparison and calibration use it. A direct
  legacy request remains compatible. Initial targeted failures exposed the missing
  comparison guard and empty-scope compatibility; both were repaired.
- The stricter scope check exposed undeclared check coverage in the Sandbox SDK unit
  fixture. It now advertises the same roles/check definitions as the actual Sandbox.
  The previously failing four-step SDK boundary execution passes with unchanged hard
  gate conditions. A newly written event assertion also used the wrong payload key;
  corrected to the existing data/prediction envelope. Two diagnostic invocations used
  nonexistent test paths and ran no tests; the final targeted/full commands above ran.

## Reviewable current-source evidence

Current-source demo results are in .cache/composable-validation/demo-{software,
physical,release}-sealed.json with separate durable SQLite/artifact directories.
The new regression and consequence protocols pin the same runtime source hash:
`b6f5359886440045413309c38aa0a6f98201064ff5f76ad34cf03f22c906be38`.
The benchmark completes 120 episodes including ablations/direct baselines. Its
primary PreAct conditions complete all 15 local cases (10 software, 5 physical),
with zero observed unsafe/infrastructure-failure episodes; this does not establish
a population safety guarantee. Direct/ablated results remain in the full report.
Distribution integrity checks match 58 Python sources in both wheel and sdist;
archives are rebuilt after this record is saved.

## Preservation and limits

All **137** baseline historical benchmark/report/dataset files checked by SHA-256
remain unchanged; dataset Python sources also match HEAD. Existing October 6 public
verification progress is preserved. New evidence stays in separate ignored cache
paths, and frozen source/protocol hashes are not bypassed.

The scope is a reviewable local runtime, not real sponsor/GPU acceptance. Doctor
reports absent Nebius credentials/model, ConTree credentials/image, Isaac/Cosmos
endpoints and worker token. Actual Nebius/ConTree/Isaac/Cosmos/GPU/cloud validation
remains pending. Mock transports and CPU bridge tests certify only their boundaries.

Remaining technical debt: legacy immediate evaluation compatibility layer; conservative
family boundary and limited reuse; finite single-path environmental traces; no
automatic combined-sequence synthesis or long-horizon actual-observation alignment/
calibration. Next work is real admitted provider/worker conformance when access exists,
then separately specified branching environment dynamics and longer-trajectory labels.
