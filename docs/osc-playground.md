# OSC Interactive Cognitive Playground

PreAct modularly reproduces world-model functions and integrates cognition,
prediction, simulation, decision and execution. The exhibition presents this closed
loop: the environment is observed, an internal representation supplies uncertain
action futures, a decision becomes one real action, and its confirmed experience is
retrieved by the following cognitive round. It uses the real
`CognitiveAgent`, `DeliberationPlanner`, `LearningCoordinator`, `QueueLearningBackend`
and domain-independent Core Runtime. It does not implement a second agent, safety
gate, training coordinator or executor.

## Prepare before the exhibition

Use the repository checkout, Python3.12 and Node22+ on the exhibition computer.
Install dependencies and compile the UI while networking is available:

```sh
uv sync --python 3.12 --frozen --extra dev --extra physical --extra sandbox
npm --prefix web ci
npm --prefix web run contracts
npm --prefix web run build
```

The reviewed replay catalog is included in `assets/playground/recordings/`.
No benchmark reproduction or API key is needed to view it. Keep the checkout,
`.venv`, built `web/dist`, local artifacts and replay assets on the computer.
The standalone wheel does not contain the website/assets; this exhibition launch
procedure uses the source checkout. No external fonts, CDN or model download is
required at run time.

## Launch on localhost

From the repository root, with the prepared dependencies:

```sh
PREACT_MODE=local PREACT_KIOSK=1 \
PREACT_DATABASE_URL=sqlite:///.preact/exhibit.db \
PREACT_ARTIFACTS_DIR=.preact/exhibit-artifacts \
PREACT_PLAYGROUND_ROOT=.preact/exhibit-sessions \
.venv/bin/preact serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/#playground`. Kiosk mode also opens the playground from
the root URL. It uses local artifact storage even if an unrelated S3 bucket is in
the operator's environment. The health endpoint reports the validated replay count
and any rejected archives:

```sh
curl --fail http://127.0.0.1:8000/api/health
```

Expected: `status=ok`, `kiosk=true`, `playground_replays=6`,
`playground_replay_errors=[]`. Check the actual values before opening the booth.
Binding remains localhost; a network-facing deployment is outside this setup.

Normal service mode, without `PREACT_KIOSK=1`, retains Software/Physical execution,
the default Future Tree and a link to the playground. Queue preparation/evaluation
runs do not displace Software/Physical history in that UI. Kiosk mode rejects the
generic run, cancellation, arbitrary artifact, ledger and API documentation routes
on the server. Public Queue requests accept scenario and bounded seed only. They
cannot provide policies, code, state, private schedules, models or promotions.
Live preparation, evaluation and cognitive runs retain the server-owned Policy
limits, including calibration. The historical benchmark replays retain their
original policies and are explicitly recorded, not newly executed.

## A thirty-second visitor path

1. Read the task: an AI controls how much work to accept for a machine. More completed
   work is desirable, but accepting too much creates a backlog and risks overflow.
   The machine's current capacity is hidden from the AI. The four boxes represent
   accepted work in transit, work waiting for processing, the machine and completed
   work. A token is one fungible unit of work, not an individually tracked job.
2. The default audited stable-high Replay opens at the first completed round with
   real processed work, after readiness probes. The World shows the actual result;
   the adjacent story states what was observed before that decision.
3. Read the three Japanese alternative cards: accept3, accept1 or accept nothing.
   Their numbers are additional completed work predicted from the input observation
   over the saved horizon, assuming no further acceptance. They are means, not
   successor States. Clicking an alternative inspects it and never executes it.
4. Compare the selected action's next-tick forecast with the actual processed amount.
   Newly submitted work arrives after two ticks: the just-processed work may have
   been accepted earlier. Click `次の判断を見る` to advance one actual cognitive cycle.
   `自動で進める` visits original observation, deliberation, selection, outcome and
   cognitive-update records at a presentation cadence. Polling does not reset it.
5. Select stable-low in the scenario menu. Replay loads that scenario's independently
   audited execution, with its own observed World, model parameters and decisions.
   Live instead applies the scenario only when constructing a new World.

The six plain-language function buttons explain cognition, prediction, simulation,
decision, execution and memory in the context of this task. Technical class names,
event kinds, Goal999/Reward/Receipt counters, model parameters and raw JSON are behind
`技術者向け詳細`. That view retains the integrated dependency diagram, five stage
buttons, previous/next-cycle navigation and original-event timeline. In that view,
Next Step advances one saved event; in ordinary Replay, the Japanese next-decision
button advances a complete cycle. Live always admits one real round, regardless of
the view. Switching views does not move the replay cursor or create executions.

For a change example, select High-to-Low or Low-to-High. Both recovery and
no-recovery records are included. Technical details expose the original event
reference, timestamp, implementation classes, inputs/outputs, candidate fingerprints,
authorization, receipt, model version and available lifecycle artifact summaries.
Automated/browser rehearsal is not evidence of first-time visitor comprehension;
have a colleague perform this path before the exhibition.

## Live controls

- Select Live, choose a scenario and press Start. Sixteen formal probe actions in a
  separate preparation World train the initial model with the existing trainer.
  This is declared preparation, not fabricated previous experience or a public
  promotion. Its run IDs, model version and process CPU/wall interval are inspectable.
- Start admits the first actual cognitive round. Next Step admits one further round.
  The same one-shot Agent run owns the World across every round; pauses occur in
  the existing round observer, outside Runtime authorization deadlines.
- Auto Play continuously admits subsequent rounds. Pause requests a boundary stop:
  an admitted action and its receipt/learning checkpoint may finish first. A paused
  session admits no next prediction/learning prepare until explicitly released.
- Reset / Stop prevents further admissions and retains the current records. Wait for
  the stopped status, then use New Session and Start to create a fresh World.
- Repeated/stale control requests cannot enqueue additional rounds: commands use a
  server revision and one boolean round permit. At most one live Queue session is
  admitted per service process. The catalog lists the eight newest local sessions;
  older IDs remain readable through the session endpoint.
- Recorded Replay changes only the local browser cursor. Exploring history from an
  active session first requests Pause. Returning to Live shows the current evidence.

Stable-high, stable-low, High-to-Low and Low-to-High are constructor-only presets.
Change scenarios switch at Tick8 using existing World construction. No runtime
environment-edit API is provided. Public scenario labels are presentation settings,
not predictor inputs. The World executor alone reads the private schedule.

An observation window has56 cognitive rounds with three extra World ticks available
for H3 forecasting. Goal999 deliberately keeps this continuous fixture active.
Completing the observation window is not Goal completion or general task success.
Abstention, failure and interruption are reported as such, not window completion.

## What the display means

Each token represents one fungible work unit. The simulation does not retain task
identity or per-job completion time. Submitted work has the existing two-tick arrival
delay. Pending work, waiting queue and delivered total come from authoritative
observations; processed work and reward come from completed outcomes. Queue capacity
and service rate are different quantities. The current service rate is private;
the last executed Probe measurement is labelled with its measurement Tick.

Animations interpolate the visual presentation of recorded quantities and start
from actual receipt events. They create no additional tick, observation or reward.
Reduced-motion mode shows the same quantities without interpolation.

The six modules highlight only for corresponding saved evidence. A `deliberation`
contains belief, prediction, simulation, value ranking and receipt-backed memory
references in a single record; these modules highlight together when present.
Memory highlighting here means retrieval, not training or promotion.
Separate phase timestamps or measured
durations are not invented. A readiness Probe has no invented prediction of its
future measurement. The inspector displays the actual original class/record context.

The primary workspace is a single-round view, not six unrelated status lights.
`web/src/playground/cycle.ts` is a read-only presentation adapter over original
events. It binds the input observation, belief, model use, candidates, selection,
authorization, intent, complete outcome and cognitive update by the same Runtime
run ID. Its feedback links are the intersection of that deliberation's exact
`memory_references` and receipt-verified previous outcomes. Entering a new observed
round clears predictions/selection/Actual from the previous round. Unknown model
parameters remain unknown until a saved forecast actually uses them.

The numerical service parameter is read from `Prediction.raw.p_high`, with its
recorded model version, informative training count and Health. It is a historical
binary-service marginal under stationary/iid assumptions, not a learned full
environment, a calibrated safety probability or access to the private schedule.
Known queue-conservation/arrival rules were not learned. A labelled fixed-prior
fallback stays a fixed prior. The internal representation is composed across
existing modules; the display does not add a second world-model engine.
The current Queue `Belief.inferred` is empty: it retains authoritative observations
and declared unknowns. The learned service parameter belongs to the existing
Prediction adapter and is not relabelled as an observed hidden state.

Data-flow connectors carry original source references and become supported only
when their corresponding record exists. These are logical data dependencies,
not measured module timings. The selected module also exposes concise input/output
in the main workspace; technical raw records stay in the Inspector. The separate
Future → Decision → Actual table matches the selected candidate's saved H1 vectors
to its receipt. It never attaches an Actual to an unselected candidate.

Queue futures are saved metric paths/distributions, not complete successor State
objects. React Flow connects the observed root to existing candidate Actions, and
the inspector plots only retained path values/weights/vectors. Mean values never
become synthetic States. Only the selected, executed candidate receives Actual.

The displayed H1 error is the mean absolute queue/delivered error for matched
selected actions, with matching count and all executed actions as the coverage
denominator. Probes remain in the denominator and have no predicted measurement.
H3 assumes no additional intervention; it is not scored against the agent's next
three closed-loop actions. Independent H3 evaluation is a separate retained artifact.
Support paths, Wilson assumptions, soft values and Model Health do not certify safety.

Experience counts derive from receipt-confirmed source outcomes. Learning manifests,
health, candidate/evaluation records and explicit promotion remain distinct. Public
Live controls never promote. A recorded Promotion is a historical explicit operator
call, not a Replay button effect. If the next generation's Health is not yet recorded,
the display waits for its checkpoint rather than copying the previous Health label.

The frozen Low-to-High comparison worsened with recovery: Reward32.9 versus115.4.
The display preserves this adverse result, Probe opportunity cost, absent predictions
and stable cases with no promotion. These finite fixtures do not demonstrate general
learning benefit, unknown-task generalization or broad agent superiority.

## Replay provenance and reproduction

Six seed11 Queue cases cover all four conditions and both recovery settings for
change conditions. They were selected by the approved exhibition plan, not by score.
The32-case frozen common-learning-v2 experiment was reproduced in an unchanged
public-main checkout into a new private bundle. Existing `audit_case` independently
revalidated each exported case's model, values, qualification, Gate, dispatch and
complete receipt before export. Original DBs, model weights and private caches are
excluded from the PR; only allowlisted Queue event/lifecycle projections are shipped.

Each compressed recording retains original event IDs/sequences/timestamps and a
source bundle hash, source code hash, protocol hash, source audit and export checksum.
`catalog.json` pins reviewed export checksums. The catalog refuses missing, changed
or malformed evidence. Checksums provide archival integrity, not authentication,
distributed consistency, new execution truth or an authorization to learn from replay.
No archived events are imported into the live execution/calibration/learning ledger.

To generate a new bundle and a new export directory, run outside booth hours:

```sh
uv run python -m scripts.bench_common_learning \
  --protocol benchmarks/common-learning-v2.json \
  --output .cache/osc-new-source --report .cache/osc-new-results.json
uv run python -m scripts.audit_common_learning \
  --bundle .cache/osc-new-source --output .cache/osc-new-audit.json
uv run python -m scripts.export_playground \
  --bundle .cache/osc-new-source --output .cache/osc-new-recordings
uv run python -m scripts.audit_playground \
  --bundle .cache/osc-new-source --recordings .cache/osc-new-recordings \
  --output .cache/osc-new-projection-audit.json
```

Use `PREACT_PLAYGROUND_RECORDINGS=.cache/osc-new-recordings` to inspect that new
catalog after restarting the service. The exporter runs the existing independent
case auditor itself, fences source file hashes and verifies every retained event
against its original source. The projection auditor separately checks exact source
event coverage/content and complete receipts. Keep historical outputs unchanged.

Preparation and independent evaluation are not instantaneous. Live waiting is shown
honestly; use the shipped Replay for an immediate learning/promotion story. Process
CPU intervals include other work in the service process, and session wall time
includes pauses. External API cost is zero for this CPU fixture; local monetary
compute cost is not estimated.

## Recovery and validation

On a browser disconnect, the screen retains the last retrieved evidence and visibly
reports disconnection. The active session remains bounded and can finish; a stopped
browser is not a paused World. Reconnection requests evidence after the last source
reference and resets the presentation stream if ordering changed. Reloading the page
can reopen the active session or any retained recorded history.

On process restart, interrupted sessions become historical views. Pending/aborted
receipts are retained, not treated as experience and never automatically redispatched.
Do not delete or edit the SQLite ledger/artifacts to recover the booth. Relaunch with
the same paths, open an audited Replay immediately, then use New Session for a fresh
World. An interrupted in-memory World is not claimed to have resumed. Back up the
ledger and artifact/session directories after stopping the process.

Relevant checks:

```sh
uv run pytest -q tests/test_playground.py tests/test_playground_replay.py
uv run pytest -q
uv run ruff check src tests workers scripts
uv run ruff format --check src tests workers scripts
npm --prefix web run contracts
git diff --exit-code -- web/src/contracts.schema.json web/src/contracts.ts
npm --prefix web run build
PREACT_E2E_ARTIFACT_DIR=../.cache/osc-browser-artifacts npm --prefix web run test:e2e
```

Playwright covers real API execution, controls, event seeking, candidate/Actual
binding, original promotion records, adverse results, disconnect/reconnect and
reduced-motion Replay with external requests blocked. Existing Software/Physical
browser cases remain in the suite. Live environment editing, public promotion,
extensive cross-model comparisons and Physical linking remain outside P0.

## Local acceptance evidence

[Validation summary](../reports/osc-playground-validation.json) records actual commands,
counts, source/projection audits and the bounded13-session Live soak. Python regression
completed864 passed/8 optional skips; the post-adjustment API suite passed45 tests.
The12 Chromium cases include actual kiosk process restart with networking limited to
localhost. Core and historical frozen evidence are unchanged.

![Recorded Queue Tick12 with real candidate selection and completed receipt](../reports/osc-playground-queue.png)
