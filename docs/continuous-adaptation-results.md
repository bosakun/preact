# Continuous adaptation results

Two artifacts are kept separate because the first pilot failed to show a model-
dependent action difference. Neither result overwrites PR #8–#13 protocols or reports.

| Run | Submit cost | Model A/B/C executed amounts | Continuous transitions | Reward / delivered | Fixed `submit(1)` reward / delivered | Result |
|---|---:|---|---:|---:|---:|---|
| v1 pilot | 0.10 | 3 / 3 / 3 | 100 | -0.5 / 4 | Not measured | Adaptation completed; no action divergence |
| v2 fixed | 0.15 | 1 / 3 / 1 | 100 | -1.6 / 4 | 84.0 / 99 | Model changes selected actions; task return is much worse |
| high→low→high v1 | 0.15 | 3 / 1 / 3 | 100 | -1.9 / 4 | 84.0 / 99 | Direction-reversal adaptation completed; task return is much worse |

The v2 fixed protocol starts one World instance at tick 0 and does not reset it.
Model A is learned from a separate source episode, then the continuous episode sees
Low service on ticks [0,24), High on [24,64), Low on [64,104), and High on
[104,160). In this execution the first two changes are detected at ticks 36 and 75.
After each invalidation, 16 post-cutoff informative receipt-backed probe outcomes
train the next candidate. Each candidate is evaluated on the existing independent
H3 recovery cases (8 action comparisons, coverage 1.0, unknown rate 0), then the
continuous World is freshly observed and 8 disjoint monitoring probe receipts are
collected before explicit promotion. Candidate Model B and C each achieved state
MAE 0 and Action-difference MAE 0 on the fixed independent evaluation. For B, the
parent and prior had state MAE 2.444 and 1.000, and difference MAE 1.111 and 0.569.
For C, the parent and prior had state MAE 2.444 and 1.444, and difference MAE
1.111 and 0.542. All listed coverages were 1.0. The apparently perfect candidate
errors reflect a deterministic owned fixture whose regime matches its training data;
they do not establish performance under stochastic or gradual shift.

The final independent auditor recomputes the transition dataset and model fits,
checks epoch ancestry and explicit promotion records, replays monitoring Health at
the promotion cutoff, verifies independent evaluation receipt separation, and
links each model prediction to the Runtime's complete candidate batch, selected
qualified action, authorization, durable intent, complete receipt and actual
observation. It passed for v2: 100/100 complete source transitions, three
model-grounded executed decisions and zero unsafe executions. Restore was exercised
after both B and C promotions; subsequent decisions used the reconstructed lifecycle
instance. The result JSON contains summary digests; the underlying ledger and model
artifacts remain in a private temporary directory and are not committed.

The fixed `submit(1)` control uses another World instance with the same seed,
initial conditions, service schedule, task and 100-tick horizon. It executes one
formal Runtime action each tick. It is a deterministic paired scenario, not a
distribution-level estimate. The learned lifecycle uses probes for drift monitoring,
recovery training and monitor readiness, spending many ticks without task delivery.
The control therefore exposes a major cost: the tested adaptation procedure is not
utility-improving. Reward already includes declared probe, submission and holding
costs. Model adaptation success and task success must remain separate claims.
This sprint did not run full closed-loop no-recovery, fixed-prior, or prior
episode-recovery policies on paired Worlds; `prior` and `parent` are forecast
baselines inside candidate H3 evaluation only. The single submit(1) policy is the
only closed-loop non-learning reward control, so no broader policy ranking is claimed.

A separate fixed high→low→high protocol uses the same seed, horizon, costs,
training/evaluation sizes and promotion thresholds, with a high-service A, low-service
B and high-service C. It detected changes at ticks 36 and 75, trained/promoted B/C,
passed the independent auditor over 100 continuous receipts, and selected actual
amounts 3/1/3. Candidate H3 state and Action-difference MAE were again 0/0 at coverage
1.0; the fixed submit(1) control again delivered 99 with reward 84.0 while adaptation
delivered 4 with reward -1.9. This remains a single deterministic fixture direction
replication, not an independent random-seed estimate.

Reference CPU/wall/memory measurements were 44.9 CPU seconds / 50.4 wall seconds /
139,722,752 bytes peak RSS for low→high→low, and 49.6 CPU seconds / 55.4 wall seconds /
139,706,368 bytes peak RSS for high→low→high on the recorded platform. No external
API was called and the queue environment declared monetary API cost zero;
compute-dollar cost is unknown. The fixed baseline took 3.29 and 3.37 wall seconds;
its CPU time was not separately measured. These timings are observations, not
optimized or cross-platform estimates.

The initial v1 pilot used submission cost 0.10 and left the objective function
unchanged; all three models chose amount 3. Because it showed no action difference,
the separately identified v2 pilot scenario used cost 0.15 to include a meaningful
tradeoff between action sizes. The v1 result remains recorded, and no reward weights,
model values, or selection thresholds were changed after observing v1. v2 is the
fixed result for this PR. No stochastic multi-seed, noisy/partial-observation, or
high→low→high evaluation was run. Failed promotion and a full crash campaign at every
write boundary remain untested; the automated suite covers receipt/anchor negatives
and invalidated restart replay but cannot substitute for those experiments.

Reproduction commands and lifecycle details are in
[`continuous-adaptation.md`](continuous-adaptation.md). Compact result files are
[`v1 pilot JSON`](../reports/queue-continuous-v1-pilot.json) and
[`low→high→low result JSON`](../reports/queue-continuous-v2-results.json) and
[`high→low→high result JSON`](../reports/queue-continuous-v3-results.json).
