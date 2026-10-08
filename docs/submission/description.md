# PreAct — Submission Description Draft

PreAct is a Composable World Model Runtime for AI agents. Before an agent acts, it builds a branching
Future Tree, asks appropriate engines what could happen, evaluates disagreement and uncertainty,
and verifies consequential choices. It executes one qualified action, observes reality and uses
prediction errors to calibrate future engine trust. It composes heterogeneous engines at runtime; it is not a monolithic learned world model.

Engineers can inspect why an attractive repair is rejected or why a trajectory needs stronger
verification. Software and Physical World share Core search, gate and ledger. Domain adapters
provide repository or robot state/actions; Future Engines supply reasoning, sandbox execution,
conditioned visuals or simulator measurements.

The current executable local lab repairs a Python checkout repository and moves a cube in
MuJoCo through the same interactive renderer. A 120-episode development regression comparison
also exercises a four-step repository migration/configuration/build workflow and demonstrates
constraint preservation, actual outcome recording and honest abstention. This narrow evaluation
is not a general superiority claim. A separate frozen local evaluation ran 1,920 episodes over
24 held-out instances × five seeds × eight conditions per world. Primary completion was software
100% and physical 83.3%, with no observed unsafe executions and 16.7% physical abstention.
Direct completed 87.5%/52.5%; PreAct used substantially more calls and time. Several ablations
tied; calibration worsened chosen-safe-action heuristic scores. These authored candidate-patch/
Cartesian scenes use local heuristics. Live Nebius/Cosmos/Isaac and operational
release acceptance remain pending; revise around release evidence.

An optional mode now runs the official NVIDIA Nemotron 3 Nano 4B checkpoint on Apple Metal
with verified weight/quantization/runner hashes and private loopback authentication.
The model ranks candidates and forecasts postconditions through the same Core; executable
and MuJoCo verifiers reject harmful alternatives. Both worlds completed real development
trials and live browser runs. The original 384-episode equivalent-model pilot completes
all eight conditions. Software Direct/PreAct completes 21/24 versus 24/24 tasks;
Physical completes 9/24 versus 17/24. Unsafe episodes fall from three/fifteen to zero,
but PreAct records six physical abstentions and a timeout. Physical median latency
increases from 35.2 to 200.9 seconds; several verified alternatives complete 18/24.
Selected-success calibration worsens and physical model risk forecasts are poor.
These one-seed authored instances were already exposed; they are not evidence of broad
generalization or a safety certificate. Allocated cost is unknown.

The repaired five-seed local-model comparison completes all **1,920 episodes**
(24 authored instances × five seeds × eight conditions per world). Software
Direct/PreAct completes 105/120 versus 120/120; Physical completes 45/120 versus
90/120. PreAct records zero observed unsafe episodes, 24 Physical abstentions and
six timeouts. Several ablations complete 96/120 Physical tasks, exceeding the
primary policy. Selected model calibration worsens; physical risk forecasts remain
poor, latency rises, and allocated hardware/energy cost is unknown. Existing task
instances were previously exposed; repeated seeds are not a newly blind population.
Original failures remain in the [complete analysis](../../reports/cohort-model-analysis-v2.md).

Both defensive evaluator binding fixes are applied. Main passes 330 tests, and a
private locked checkout independently passes all 330. Six browser cases pass on
fresh SQLite and an actual rebuilt non-root PostgreSQL image; the private development history
remains preserved separately from the fresh public candidate. [Applied-source release checks](../../reports/defensive-release-validation.json)
retain exact scope. Complete benchmark archive/artifact/calibration hashes and
read-only two-world replay are verified privately. Public release, actual Nebius,
Cosmos and Isaac GPU validation and entrant administration remain open.

## Built With — demonstrated locally

Python 3.12, FastAPI, Pydantic, SQLAlchemy, PostgreSQL/SQLite, NumPy/SciPy,
MuJoCo, React, TypeScript, React Flow, Vite, Playwright, pytest, uv, Docker,
NVIDIA Nemotron 3 Nano 4B and llama.cpp with Apple Metal.

Nemotron ranks candidate actions and predicts postconditions; protected Python probes
and MuJoCo independently measure consequences through the same Core. Nemotron's
official BF16 checkpoint is converted to measured Q4_K_M weights for local inference.
Apple Metal is the execution backend, not NVIDIA GPU hardware. Nebius Token Factory,
ConTree, Cosmos and Isaac remain implemented but unvalidated integration boundaries;
add them to release Built With only after actual successful use and evidence.

Planned category: Coding and Agentic Engineering with Token Factory Sandbox write/run/test
execution. Best Apps and Agents is the accepted alternative if beta Sandbox admission prevents
Coding qualification, retaining both worlds and real Nemotron reasoning. One project entry.

## Release fields to complete from evidence

Public repository URL, operational test URL, public <180-second YouTube URL, exact model/version
IDs and material roles, measured paired report, actual tool feedback, project development-period
statement, authorized representative and entrant attestations. No invented URLs or live usage claims.
