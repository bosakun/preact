# OSC Interactive Cognitive Playground

This exhibition adds a Queue view to the existing Future Tree. It uses the real
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

1. Open the default audited stable-high Replay. It opens at the first task decision,
   after readiness probes; the current Tick remains visible.
2. Press Start or Next Step. Watch real saved events highlight the architecture.
3. Click a candidate. Compare its saved numerical futures with the selected action,
   its Gate and the green actual outcome marker after the receipt.
4. Click Memory & Learning. Explain that only completed executions become experience.
5. Use the round selector, timeline or First button to explore earlier/later evidence.

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
contains belief, prediction, simulation and value ranking in a single record; these
modules highlight together when present. Separate phase timestamps or measured
durations are not invented. A readiness Probe has no invented prediction of its
future measurement. The inspector displays the actual original class/record context.

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
