# Repository Guidelines

## Project Structure & Module Organization

`src/preact/core/` contains domain-independent contracts, search, verification, gate,
ledger and calibration. Domain adapters live in `src/preact/domains/`; vendor and
simulation engines live in `src/preact/engines/`. FastAPI is in `src/preact/service/`,
the React Future Tree in `web/src/`, and GPU worker boundaries in `workers/`.
Tests are in `tests/` and `web/e2e/`; owned fixtures are in `assets/` and
`src/preact/datasets/`. Claim-scoped evidence/consequences and their new fixtures
live in Core and `tests/fixtures/`. Architecture and acceptance criteria are in `docs/`.
`src/preact/cognition/` adds an observation-grounded executive above Core; its memory
indexes committed execution receipts. See `docs/cognitive-architecture.md`.
Receipt-backed memory uses a rebuildable run cache and read-only Store change tokens;
its consistency boundary is documented in `docs/episodic-memory-index.md`.
Native World proposals also support Software cognitive rounds and explicit hypothetical
search, with optional episode-wide budgets. See `docs/software-cognition.md`; Core
verification and execution authority remain unchanged. The Software CLI uses trusted local fixtures.
Opt-in episode-local inference reuse retains fresh observation and receipt retrieval;
see `docs/belief-reuse.md`.
Opt-in queue v2 adds costly, one-tick service sensing through the same Gate and receipts;
see `docs/information-seeking-action.md`. Its tick-dependent Bayesian planner does not
opt in to inference reuse. Samples describe executed ticks and never certify safety.

## Build, Test, and Development Commands

- `uv sync --python 3.12 --frozen --extra dev --extra physical --extra sandbox` installs
  locked Python dependencies.
- `npm --prefix web ci` installs locked frontend dependencies.
- `uv run preact serve` starts the local API; `npm --prefix web run dev` starts Vite.
- `uv run pytest -q` runs Python regressions; `npm --prefix web run test:e2e` runs Chromium cases.
- `uv run ruff check src tests workers scripts` and `uv run ruff format --check src tests workers scripts`
  check Python style. `npm --prefix web run build` checks TypeScript and builds assets.
- `npm --prefix web run contracts` regenerates typed API contracts; commit intentional schema changes.
- `uv run python -m scripts.verify_composable_runtime --output NEW_DIR` retains new local consequence evidence.
- Set `PREACT_E2E_ARTIFACT_DIR` to keep browser captures outside historical reports.
- `uv run python -m preact.cognition.benchmark --output NEW_DIR` runs CPU cognitive comparisons.
  Reproduce and audit Japanese findings with `python -m scripts.summarize_cognition`.
- `uv run python -m scripts.bench_cognitive_memory --output NEW_DIR` compares full-reread,
  cold, warm and invalidated memory retrieval with the frozen memory protocol.
- `uv run python -m scripts.bench_belief_reuse --output NEW_DIR` measures paired queue
  inference reuse and checks normalized Gate/observation/learning equality.
- `uv run python -m scripts.bench_information_seeking --protocol benchmarks/information-seeking-v1.json --output NEW_DIR --report NEW_REPORT`
  audits paired v2 sensing, receipt/Gate integrity, semantic replication and budget controls.

## Coding Style & Naming Conventions

Use Python 3.12, four-space indentation, descriptive snake_case functions and PascalCase
classes. Ruff enforces formatting and a 100-character line target. Follow established
TypeScript/React component conventions. Keep Core independent of domain/vendor imports.

## Testing Guidelines

Use pytest, pytest-asyncio and Hypothesis; name tests `test_*.py` with behavioral names.
No numeric coverage threshold is configured. Verify meaningful state, authority, failure
and ledger properties. Security checks use bounded owned structural negatives; rejected
fixtures must not execute. Fixtures and CPU imports are not live sponsor validation.
Preserve hard constraints, aligned observation/calibration and one-action execution.
Never modify frozen tasks/protocols/evidence to fit new behavior; add new fixtures.

## Commit & Pull Request Guidelines

This publication candidate has fresh history. Use short imperative commit subjects.
Describe behavior, validation and limitations in pull requests; link issues and include
screenshots for visible changes. Preserve measured benchmark failures and unknown costs.

## Agent & Publication Instructions

Follow `docs/composable-world-model-runtime-plan.md` for the current architecture; maintain concise progress in `docs/agent-progress.md`.
Do not redesign working architecture without measured evidence. Keep credentials, private
caches, raw benchmark bundles, footage and model weights outside tracked files. Distinguish
local execution, archived evaluation and unvalidated Nebius/Cosmos/Isaac integrations.
Publication requires explicit approval of the exact candidate; never publish private history.

## Autonomous Development Rules

### Autonomous Decision Making

This repository is intended to be developed autonomously by Codex for long-running sessions.

Do not ask the user for routine implementation decisions.

When multiple reasonable implementation approaches exist:

1. inspect available evidence,
2. select the approach that best preserves the architecture and project goal,
3. implement it,
4. test it,
5. revise it if evidence shows the decision was poor.

Do not stop merely because:
- the task is large,
- the architecture is complex,
- multiple reasonable choices exist,
- implementation details were not explicitly specified.

Escalate to the user only when progress genuinely requires information or access that cannot be inferred, obtained, substituted, or deferred.

### Goal Persistence

When an active Goal exists, continue working toward it until its completion criteria are verifiably satisfied.

Do not treat any of the following as completion:

- scaffolding,
- placeholders,
- TODOs,
- mocked core behavior,
- documentation-only claims,
- disconnected demonstrations,
- code that has not been executed,
- tests that have not actually been run.

After each substantial change:

1. run the relevant validation,
2. inspect the evidence,
3. identify the highest-impact remaining gap,
4. continue working.

### Failure Handling

When a command, test, build, benchmark, integration, or implementation fails:

1. diagnose the failure,
2. attempt a repair,
3. rerun the relevant validation,
4. continue iterating.

Do not immediately ask the user how to fix ordinary engineering failures.

If one external integration is blocked, continue all independent work that can still be completed.

### External Dependencies

If credentials, paid services, unavailable hardware, quota limitations, or external infrastructure block one part of the project:

- preserve the real integration boundary,
- do not fake successful integration,
- document the blocker precisely,
- continue all other independent implementation and validation work.

Mocks may be used for isolated tests, but mocked behavior must never be represented as a completed real integration.

### Usage Limit Policy

Never use or request:

- banked resets,
- one-time resets,
- promotional resets,
- paid usage resets,
- extra-credit purchases,
- plan upgrades,

for the purpose of continuing autonomous development.

If the normal usage allowance is exhausted, stop cleanly and preserve progress so work can resume when the standard usage window becomes available again.

Do not intentionally consume any reset entitlement.

### Progress Persistence

Maintain:

docs/agent-progress.md

Update it during long-running work with:

- completed milestones,
- current implementation state,
- validation performed,
- known failures,
- blocked external dependencies,
- remaining work,
- next highest-priority action.

The repository must remain recoverable after interruption.

### Architecture Integrity

Avoid implementing Software World and Physical World as unrelated demos.

Both must use the same domain-independent PreAct Core.

Avoid shortcuts that make the demo appear functional while bypassing the intended architecture.

### Repository Instructions

Keep AGENTS.md synchronized with the actual repository.

If statements about the repository state become outdated as implementation progresses, update them.

Do not remove these autonomous-development rules unless explicitly instructed by the user.
