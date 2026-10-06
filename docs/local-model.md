# Real Local NVIDIA Model

This optional mode ranks actions and predicts futures with the official
[Nemotron 3 Nano 4B checkpoint](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16).
Both worlds use the existing Core and actual trusted Python/MuJoCo verifiers.
It establishes local NVIDIA model use. It does **not** establish Nebius inference,
Sandbox admission, Cosmos prediction, Isaac measurements or NVIDIA GPU execution.

## Prepare and serve

The tested machine is Apple M5, 32 GiB, macOS 27.0.1. Install Xcode command-line
tools and allow about 25 GB for weights, conversion and build artifacts. This recipe
uses an isolated conversion environment; application dependencies remain unchanged.

```bash
uv venv .cache/local-nvidia-env --python 3.12
uv pip install --python .cache/local-nvidia-env/bin/python -r workers/local-model-requirements.txt
PATH="$PWD/.cache/local-nvidia-env/bin:$PATH" .cache/local-nvidia-env/bin/python scripts/prepare_local_nemotron.py
uv run python scripts/serve_local_model.py
```

Preparation downloads anonymously from NVIDIA's pinned public repository and verifies
the original weight hash, governing license PDF, BF16 conversion and Q4_K_M derivative.
It builds pinned llama.cpp with Metal and without its UI. Existing mismatched files
fail explicitly. Preparation alone is not inference evidence.

The supervisor owns a loopback-only runner, generates a private token file (0600),
measures actual Metal offload and writes `.cache/local-model/manifest.json`. Ctrl-C
terminates the owned process group. Never commit token files, weights or generated logs.
The manifest contains checkpoint, quantization, runner/library hashes and hardware;
it is trusted local operator provenance, not remote cryptographic attestation.

## Run PreAct

In another terminal, set non-secret paths and the explicit operating mode:

```bash
export PREACT_MODE=local-model
export PREACT_LOCAL_MODEL_MANIFEST=.cache/local-model/manifest.json
export PREACT_LOCAL_MODEL_KEY_FILE=.cache/local-model/runner-token
export PREACT_LOCAL_MODEL_URL=http://127.0.0.1:8125
export PREACT_SERVICE_POLICY_JSON='{"max_seconds":300}'
uv run preact serve --port 8115
```

The Future Tree says “Local NVIDIA model lab.” Missing access, changed serving
identity, incomplete JSON and mismatched models fail without heuristic fallback.
Model confidence remains inference; it cannot claim measured safety. Allocated hardware
and energy cost are unknown. The original 120-second development trial timed out in
Physical World; the equal-budget 300-second trial completed in 121 seconds. Both
failures and successes are retained; larger budgets are never applied only to PreAct.

## Frozen comparisons

After completing UI validation, give the runner exclusive model traffic during evaluation:

```bash
uv run preact freeze-cohort benchmarks/model-calibration-protocol.json --engine local-model --seeds 1 --max-seconds 300
uv run preact evaluate-cohort benchmarks/model-calibration-protocol.json --split calibration --output reports/cohort-model-calibration
uv run preact freeze-cohort benchmarks/model-held-out-protocol.json --engine local-model --seeds 1 --max-seconds 300 --calibration reports/cohort-model-calibration/report.json
uv run preact evaluate-cohort benchmarks/model-held-out-protocol.json --calibration reports/cohort-model-calibration/report.json --output reports/cohort-model-held-out --ablations
```

The checked-in v1 run is a completed one-seed pilot: 24 calibration and 384
held-out/ablation episodes. The repaired v2 comparison is also **complete**:
120 calibration episodes and 1,920 held-out/ablation episodes over five seeds,
eight conditions and both worlds. Original source/model/protocol identities and
failures are retained in the public reports. The current runtime has the applied
binding fix and passes 330 development regressions; staged validation is reported
separately in the publication audit.

To reproduce a fresh run, freeze new protocol destinations with `--seeds 5` and
keep source/model/fixtures/policies fixed until execution seals. V2 repeats authored
instances exposed in earlier work; it is replication rather than a newly blind
population. Preserve equal 300-second budgets, candidates, evaluators and sampling.
Original archives are private and are not distributed with this source tree.

Protocol freezing queries the real runner; resume rejects changed source, tasks, policy,
model, sampling or serving identity. Predictions omit random action IDs and observation
timestamps so paired agent inputs match. Existing authored families were previously
evaluated with heuristics; these are not newly sourced unseen repositories or robot tasks.
Metal scheduling may remain nondeterministic. Every episode retains requests, events,
aligned errors, abstentions, failed work and actual latency. Never infer cohort completion
from these commands: consult the dated progress log and completed report.

## Licensing

The pinned model card links the [NVIDIA Nemotron Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/),
dated December 15, 2025. Its repository `LICENSE` file is empty; the governing PDF was
retrieved separately and hashed. llama.cpp is MIT. PreAct does not bundle or relicense
the checkpoint or native runner; preserve their licenses/notices if redistributing them.
This model was chosen instead of older Mini 4B weights whose separate license restricts
production runtime use. NVIDIA model license permission does not establish entrant eligibility.

The complete [pilot analysis](../reports/cohort-model-pilot-analysis-v1.md) retains
all failures and calibration/latency losses. The [repair evidence](../reports/proposal-repair-validation.json)
records non-secret actual requests and the repaired source. The completed
[v2 analysis](../reports/cohort-model-analysis-v2.md) records Software Direct/PreAct
completion 105/120 versus 120/120 and Physical 45/120 versus 90/120. PreAct has
zero observed unsafe episodes, 24 Physical abstentions and six timeouts; several
ablations complete 96/120. Selected model calibration worsens, physical risk
forecasts remain poor, latency increases and allocated cost is unknown. These are
local NVIDIA-model results, not Nebius, Cosmos or Isaac GPU validation.
