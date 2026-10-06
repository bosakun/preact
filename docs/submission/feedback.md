# Technology Feedback Evidence

No real Nebius, Cosmos or Isaac GPU integration has executed in this environment.
Actual local NVIDIA model use on Apple Metal is recorded below. Do not invent feedback.
For every technology actually used in the submitted release, record:

- Date, exact tool/model/image/version and task.
- Onboarding steps, time to first real result and admitted quota/access.
- Useful capabilities with an actual example.
- Limitations/errors, reproducible request/job IDs and workarounds (redact credentials).
- Latency, costs, failure/recovery behavior and willingness to reuse.

Current verified SDK-development observation: ConTree's 0.3.6 Python SDK accepts an already
configured 0.4.0 HTTP client; its image run/upload/timeout interface was inspected locally.
That is an interface observation, not experience using the hosted Sandbox service.
Official Nebius documentation exposed current `nebius ai` / `/ai/v1/jobs` APIs through the
Markdown index after obsolete cached CLI references failed. The real Job adapter follows
those documented fields; service access and round trip remain untested.

## October 4: actual local NVIDIA Nemotron feedback

Official Nemotron 3 Nano 4B revision `dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f`
was downloaded anonymously; original weight hash matches NVIDIA metadata. Model card's
governing license link was usable, while its repository LICENSE file was empty. Pinned
llama.cpp converted/quantized weights locally and measured 43/43-layer Apple M5 offload.

The server README's abbreviated JSON-schema example did not enforce our schema;
the actual pinned source required nested `response_format.json_schema.schema`.
Initial output had boolean success/numeric risk and failed validation. Corrected
requests completed in 5.4/6.0 seconds. Model outputs claimed measurement authority;
PreAct strips it and uses independent executable/simulation checks.

An initial unconstrained ranking failed. Explicit zero-based candidate indexes and
bounded schema repaired the integration. Full Physical search exceeded 120 seconds;
an equal 300-second Direct/PreAct development trial completed in 121 seconds. Software
completed in 42 seconds; both PreAct executions stayed safe while Direct failed unsafely.
These are single-seed development examples, not broad benchmark results. Allocated
hardware/energy cost remains unknown. Requests, errors, versions and actual UI evidence
are in [the measured report](../../reports/local-nemotron-feasibility.json).

This feedback concerns a real local model workflow. It makes no claim about Token Factory
acceleration, beta Sandbox usability, NVIDIA GPU compatibility or hosted-service cost.

## October 5: complete pilot and repaired replication

The original 384-episode pilot retains the full tradeoff: PreAct completes 24/24
software and 17/24 physical tasks with no observed unsafe episodes, while Direct
completes 21/24 and 9/24 with three/fifteen unsafe episodes. PreAct also records six
physical abstentions and a 300.005-second timeout; several verified alternatives
complete 18/24. Physical median latency is 200.9 seconds versus Direct's 35.2.
Model risk Brier is 0.5135 on selected safe physical actions, so an apparently capable
reasoning model still needs measured evidence. Selected-success calibration worsens.
This authored one-seed population does not establish general model performance.

Tracing the timeout revealed seven redundant one-candidate ranking calls. Skipping
rank work with no ordering impact and metering certified completed proposal receipts
repairs unnecessary work; a cancellation injection additionally exposed lost completed
fees, now accounted before cancellable event writes. Root regression passes 278 tests.
The repaired four-episode development trial takes longer than the original trial,
so we claim no isolated speedup. Fresh calibration completes 120 episodes with no
infrastructure errors and unknown price coverage; the five-seed comparison was then running.

Source/model/request evidence and original failures are retained in
[repair validation](../../reports/proposal-repair-validation.json) and the
[complete pilot](../../reports/cohort-model-pilot-analysis-v1.md). Actual local model
use is useful for testing evidence coordination and structured-model boundaries.
Hosted inference onboarding, throughput, quotas and pricing still require real access.

## October 6: completed five-seed comparison

All 1,920 local-model episodes and eight conditions now complete. Software primary
completion is 120/120 versus Direct 105/120; Physical is 90/120 versus 45/120.
Zero observed unsafe primary episodes coexist with 24 Physical abstentions and six
300-second deadlines. Several verified alternatives complete 96/120. Selected
model calibration worsens and measured hardware/energy cost remains unknown.
See [complete analysis](../../reports/cohort-model-analysis-v2.md) for retained losses,
engine-specific scores, task-cluster intervals and exposed-instance limitations.

A separate bounded timeout diagnosis completes safely in 142.3/159.1 seconds with
unchanged policies and matching action semantics. It does not replace the six original
failures or establish a causal speedup. The completed comparison provides actual
local NVIDIA-model feedback; Token Factory, Cosmos and Isaac service feedback still
requires real access. The private release now passes 330 independent-checkout tests
and both six-case fresh-SQLite/PostgreSQL browser suites.
