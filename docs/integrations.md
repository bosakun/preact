# Integration Setup and Evidence Gates

The [accepted plan](implementation-plan.md) remains authoritative. Configuration is
not validation. No Nebius, Cosmos or Isaac GPU live result is claimed in the current environment.
The separate [local NVIDIA model workflow](local-model.md) has actual Apple Metal
inference and shared-Core/UI evidence; it does not replace these hosted or simulator gates.

## Token Factory / Nemotron

Configure `NEBIUS_API_KEY`, exact `NEBIUS_MODEL`, optional `NEBIUS_STRONG_MODEL`,
base URL and actual per-million-token prices. Discover admitted NVIDIA models through
the account's `/v1/models` before choosing IDs; do not assume a marketing name is an API ID.
`engines/nebius.py` calls `/v1/chat/completions`, validates JSON and keeps request ID,
model and usage. It ranks the shared domain candidates and predicts local postconditions.
Self-confidence never satisfies measured constraints. Pricing unset means unknown cost,
not a verified free hosted run. Set prices before budget/cost benchmark acceptance.

The completion boundary requires the exact requested/reported model ID, a bounded ID,
one assistant JSON answer at index zero and `finish_reason=stop`. Truncation, filtering,
tool/refusal output and mismatched models fail before prediction/ranking or calibration.
Malformed prediction schemas produce a generic failure without publishing rejected content.
Usage retains only non-negative integer token counts; missing/malformed charges stay unknown.
One registered set of finite, non-negative operator rates supplies both ledger and prediction
cost. Example all-zero rates remain an unknown-cost sentinel. An API model ID is not a
measured checkpoint revision. The public [official API schema](https://api.tokenfactory.nebius.com/openapi.json)
inspected October 4 reports version `20260930-cfb76be12`; transport/Core tests are not
live inference evidence. `reports/token-factory-completion-contract.json` records scope.

Run `uv run preact validate-token-factory --catalog-only --output .preact/catalog.json`
to discover admitted IDs, then configure an exact ID and run
`uv run preact validate-token-factory --output .preact/token-factory-evidence.json`.
The latter validates structured action-conditioned predictions for both world contracts,
retains catalog hash/model/request IDs/usage/latency, and never executes an action.
Evidence paths refuse overwrite. Missing keys, unlisted IDs, HTTP errors and malformed
predictions fail explicitly without a local substitute. October 4's actual invocation
recorded a missing-key blocker in `reports/token-factory-access-2026-10-04.json`;
unit mock transports certify only the validation boundary. The official
[Token Factory API](https://api.tokenfactory.nebius.com/docs) documents model discovery.

Engine capabilities can declare supported verification checks and advisory per-request
USD/latency estimates. Core ranks evidence by unresolved claim coverage, stakes,
uncertainty/disagreement and contextual reliability relative to remaining resources.
Unknown estimates stay unknown; neither estimates nor model confidence authorize actions.
Measured cloud costs must populate estimates before cost-sensitive live acceptance.

## Token Factory Sandboxes / ConTree

Install the `sandbox` extra. Configure beta credentials and a provisioned Python image
through `CONTREE_API_KEY`, `CONTREE_IMAGE`, `CONTREE_BASE_URL`.
`engines/sandbox.py` uses the real installed 0.3.6 SDK/0.4.0 client, uploads source/probe
files, executes Python with a deadline and validates results. Parent filesystem checkpoints
are retained; sibling futures execute from the same parent without mutating it. Authoritative
execution reruns the chosen action with fresh outcome checks. Bounded pure model-generated
patches are supported. The bounded release task also runs actual SQLite migration/data/schema
checks, configuration compatibility and bytecode builds in sibling ConTree checkpoints.
`domains/release_probe.py` is a standalone protected worker evaluator; its private tests never
enter proposer state. Authoritative execution reruns independently and returns a separate receipt.
SDK checkpoint behavior is unit-validated only. Live branching, retained-image garbage collection,
service quotas and SDK native cost currency remain release gates; unknown currency is not USD.
The server-owned randomized evaluator checks 68 input cases and is omitted from agent state.
Prediction and authoritative execution receive distinct seeds; bounded pure candidate code
cannot use imports/I/O to inspect the evaluator. This restriction is not a substitute for ConTree
isolation. Exact tests certify only the sampled behavioral contract, not arbitrary repository safety.
The release SDK fixture executes the actual standalone evaluator in isolated local processes;
this validates serialization, branching and all four workflow steps, not Nebius service access.
Owned HTTP/SDK clients close on run completion, cancellation and partial setup failure.
[Official SDK](https://github.com/nebius/contree-sdk) defines branching and client configuration.

## Isaac

`workers/isaac_step.py` launches actual SimulationApp, Franka joints/gripper, RMPflow,
physical cube/obstacle, contact reports and camera frames. Each prediction reconstructs
its own scene; the authority endpoint stores independent observed state and receipts.
Run `workers/isaac.py` under the API Python environment; `ISAAC_PYTHON` points to the
separate Isaac SDK launcher, conventionally `/isaac-sim/python.sh`. Configure a private
`ISAAC_ENDPOINT` and `WORKER_TOKEN`; persistent jobs require `WORKER_DATABASE_URL`.

The [official 5.1 environment guide](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/install_python.html)
specifies Python 3.11 for that SDK. PreAct's API and locked NumPy/SciPy require Python 3.12.
Each launcher invocation now builds a deterministic, checksummed ZIP of the exact installed
shared models, geometry and artifact helpers (`engines/sdk_bridge.py`). It prepends that
source-only archive to the SDK's import path; the SDK script checks Python 3.11, every module
hash/origin and required dependencies before SimulationApp. API binaries, SciPy, MuJoCo,
credentials and an alternate decision runtime are absent. Bridge hashes/dependency versions
are retained with nominal predictions and perturbation trials.

Provision the SDK-side dependencies separately, preserving its installed NumPy:

```sh
/isaac-sim/python.sh scripts/install_isaac_bridge.py           # resolver dry-run
/isaac-sim/python.sh scripts/install_isaac_bridge.py --install # SDK environment only
uv run python scripts/verify_isaac_bridge.py --python /isaac-sim/python.sh --report .preact/sdk-imports.json
```

The installer pins four direct dependencies from `workers/isaac-bridge-requirements.txt`
and constrains NumPy to the actual SDK's installed version; incompatible resolution fails.
It checks installed dependency consistency before and after the operation. Rebuild the
SDK environment if a conflict remains; do not start a GPU job from a failed setup.
The full SDK/container/transitive lock must be captured after real GPU conformance.
[Executed CPU evidence](../reports/isaac-sdk-imports.json) uses isolated Python 3.11.15,
Pydantic 2.13.5, SQLAlchemy 2.1.3, boto3 1.43.108, mediapy 1.2.7 and **CPU test NumPy
1.26.4**, not a claimed NVIDIA dependency manifest. Sixteen source identity/fingerprint,
clearance and local artifact cases match API Python 3.12.13. No SDK/GPU loads; this does
not pass the NVIDIA milestone. `mediapy` still needs a working FFmpeg on the real worker.
The worker rejects a missing FFmpeg executable before importing/starting SimulationApp.
That negative startup path is CPU-tested. FFmpeg is absent from this development host PATH; camera
encoding and codec compatibility have not been validated by the import probe.

The current compatibility target is Isaac Sim 5.1.0, whose official docs now label it
unsupported. Live GPU conformance must decide whether to migrate to a supported version;
do not claim compatibility from syntax compilation. RTX GPU, Linux, licensed assets,
contact subscription, camera convention, gripper reach and reconstruction/reset need testing.
The single nominal prediction deliberately cannot pass the 5%/95% experimental risk gate.
`workers/isaac_perturbations.py` exposes a separate strong verifier at `ISAAC_STRONG_ENDPOINT`.
It requests up to 59 actual independent pose/mass/friction rollouts within reserved call and
wall-clock budgets. Exact one-sided 95% binomial bounds require at least 59 zero-failure
samples for risk below 5%; a single failure still vetoes. Declared same-family refinements
resolve nominal intervals without erasing contradictory checks. This certifies only the
stated simulation population; model mismatch/hardware remain outside the claim.
Live perturbation runs, persisted dynamics and warm SDK worker optimization remain unvalidated.
`workers/isaac_authority.py` reserves episodes in SQL before dispatch, blocks unresolved
episodes across worker instances/restarts and atomically publishes state plus matching
receipt. Duplicate completed receipts return their original observation. All authority
SQL runs off-loop through Store; a process-local lock only limits local SDK concurrency.
[Actual PostgreSQL boundary evidence](../reports/isaac-authority-transactions.json)
uses an injected SDK and does not validate NVIDIA. Five regressions cover competing
workers, stale/mismatched receipts, SDK failure/cancellation and publication conflicts.
[Franka motion reference](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/manipulators/manipulators_rmpflow.html).

## Cosmos

Clone the official Cosmos-Predict2.5 repository into the GPU environment, pin an exact
commit in `COSMOS_REVISION`, install its official dependencies/checkpoint and accept
applicable model terms. Set `COSMOS_REPO_PATH`, `COSMOS_PYTHON`, `COSMOS_ENDPOINT`.
Launch `workers/cosmos.py` with Uvicorn from that environment after installing PreAct.
`engines/cosmos.py` invokes the official `examples/action_conditioned.py` with our custom
loader; it consumes a hashed camera video and 7D Cartesian deltas, explicitly transforms
to a fixed downward tool frame, records scale/FPS/gripper/version and persists generated video.
It rejects absent conditioning/frame information rather than inventing a camera observation.

The current transform supports **already-held transport only**. Isaac supplies a separate
endpoint snapshot of object, joints, actual end-effector pose, active bilateral finger contact
and SDK runtime. The adapter requires that snapshot to match the requested state, selects
the final camera frame and rejects grasp/release or non-downward orientation. Isaac transport
ticks sum to the declared action duration; approach/grasp/release settling are explicit
additional phases. Cosmos uses 20Hz rounded transport plus stationary closed-finger padding
to complete native twelve-action chunks (e.g. 2 seconds becomes 40 commands + 8 holds,
2.4 seconds generated horizon). No zero-padding finger opening is silently introduced.
The official generator currently uses chunk-index seeds; requested CLI seeds are recorded
separately and do not establish independently sampled video rollouts. Both upstream revision
and wrapper/contract hashes are versioned, tracked upstream modifications fail validation,
and raw native console output is omitted from public artifacts.

Isaac records actual `isaacsim.core.version.get_version()` build fields and Linux x86-64
`nvidia-smi` device/driver/memory inventory; missing or mismatched measurements fail. All
declared keep-out boxes and cube dimensions are reconstructed. Worker/contract hashes version
the engine, and changed authority runtime requires reset/re-observation. Device inventory
does not certify the active renderer GPU or RTX compatibility. These contracts pass CPU
tests; no compatible SDK/GPU/checkpoint has executed locally.

Primary references: [Isaac 5.1 version API](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/py/source/extensions/isaacsim.core.version/docs/index.html),
[render/blocking API](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/py/source/extensions/isaacsim.core.api/docs/index.html),
[PhysX contact events](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/latest/extensions/runtime/source/omni.physx/docs/dev_guide/contact_reports.html),
and [native Cosmos action-conditioned implementation](https://github.com/nvidia-cosmos/cosmos-predict2.5/blob/main/cosmos_predict2/action_conditioned.py).

[Official action-conditioned guide](https://github.com/nvidia-cosmos/cosmos-predict2.5/blob/main/docs/inference_robot_action_cond.md)
currently describes single-GPU execution. Validate checkpoint/action scaling, camera and
robot transfer, memory and latency with real outputs. Native video generation provides no
success probability or collision geometry. Visual predicate extraction/applicability and
sample uncertainty are unresolved; output claims remain unknown until measured.

## Storage, jobs and deployment

`S3_BUCKET`, `S3_ENDPOINT` and scoped AWS credentials select Nebius Object Storage.
All artifacts are SHA-256 addressed and checked on read; database events retain ordering.
`PREACT_DATABASE_URL=postgresql+psycopg://...` selects PostgreSQL for Core.
The production ARM container and PostgreSQL 17.11 have passed local concurrent event/error/job
tests and actual restart durability; storage-aware API health probes issue a real SQL query.
Worker job endpoints use leases and authentication; cancellation suppresses publication,
though already-running GPU subprocess cancellation needs end-to-end conformance.
Run cancellation interrupts active prediction work. A pending execution receipt keeps the run
interrupted for reconciliation; cancellation before side effects is recorded as cancelled.

Remote admission and polling share one deadline. A known unfinished job triggers one
bounded DELETE request, drained through repeated cancellation. Unknown POST outcomes are
unconfirmed and never resubmitted blindly. Terminal failure never supplies a prediction;
completed predictions retain worker job IDs. Core records bounded cleanup diagnostics in
`engine_failed`/`engine_cancelled` events. An accepted DELETE does not confirm remote termination.
Eighteen transport/runtime tests cover this boundary; none validates a hosted GPU worker.

Job protocol version 2 separates cancellation request, operation drain and termination
evidence. Cancelling queued work is confirmed before dispatch; cancelling running work
immediately vetoes publication and retains the owner's lease in `cancelling`. The claiming
consumer records `operation_finished`, bounded `cleanup` and explicit
`terminal_state_confirmed` after draining. Failed or unavailable cleanup remains
unconfirmed even if the coroutine ended. Expired cancellations become `interrupted`,
never executable queued work. Legacy responses without explicit confirmation stay unknown;
a cancelled/failed status alone does not prove resource termination. Cleanup flags are
strict booleans under a fixed diagnostic schema version, never coerced text confidence.

Cosmos camera preparation and artifact persistence run off-loop and drain before temporary
directory removal. Each CLI invocation owns a POSIX session; cancellation/deadline kills that
process group and drains bounded launcher reaping through repeated cancellation. Cleanup
denial/timeouts remain unconfirmed. Eight lifecycle tests use local fixture launchers/storage,
including an actual child process inheriting its parent's pipes; no model/checkpoint executes.
Isaac uses the same SDK-independent process helper; six launcher/nominal boundary tests
cover actual fixture processes and conservative single-sample/unknown-cost handling.
GPU memory reclamation and actual SDK end-to-end cancellation remain M7/M8 gates. Hosted cost
is unknown until measured; visual claims remain uncalibrated and unmeasured.

Container/Terraform files are deployment candidates, not provisioned infrastructure.
`engines/jobs.py` implements the current official `ai/v1/jobs` submit/status/cancel API,
including bounded injected input and an output volume. `workers/batch.py` executes the same
real engine contracts as interactive workers. The payload unit test validates the API contract,
not service access. Complete a real batch/artifact round trip before M8 acceptance.
`NebiusJobs.wait` requires a finite positive budget and bounds the whole status poll.
Deadline, cancellation or failed/malformed status triggers one separately bounded
cancellation request. Exceptions expose non-secret `preact_cleanup` with request
acknowledgment/failure; `terminal_state_confirmed` remains false. Cleanup drains
through repeated cancellation and may extend beyond the polling budget. Use
`async with NebiusJobs(...)` or `aclose()` for owned clients; borrowed clients stay open.
Sixteen mock-transport/lifecycle cases validate this boundary, not hosted Jobs execution.
[Official Jobs quickstart](https://docs.nebius.com/serverless/quickstart/jobs) and
[management API](https://docs.nebius.com/serverless/jobs/manage) define this boundary.
Terraform 1.13.5 plus provider 0.6.64 passes actual provider-schema validation; provisioning
and account quotas remain unverified. Provider 0.6.57 was listed but its release assets were
unavailable, so dependency validation required the newer downloadable pin.

## Live acceptance evidence

Retain model/checkpoint/image IDs, raw predictions, independent observations, platform/job
request IDs, artifact hashes, memory/latency/cost, licenses and quota. Tests with mocked HTTP
are only adapter unit validation. M1/M3/M7/M8 remain open until actual services execute.

## Persistence latency and failure behavior

Core and API storage calls use an async facade; blocking SQL/filesystem/S3 work runs
off-loop. Cancellation drains an in-flight write before recording final status or
reconciling intent. PostgreSQL connections use 5-second statement/connection/pool limits,
1-second lock limits and a 10-second idle-transaction limit. S3 defaults use a 5-second
connect timeout, 10-second read timeout and two total attempts. These are per-operation
limits; safe cleanup can extend beyond an action budget. SQL storage failures return HTTP 503
without SQL details; absent S3 keys differ from permission/service failures.

Prediction workers retry failed SQL leases/commits; uncertain result writes retain their
lease until expiry. Authenticated `/health` checks storage and consumer liveness. This
does not validate remote GPUs. The actual local PostgreSQL lock/rollback/deadline proof
is [recorded here](../reports/postgres-storage-deadlines.json).
