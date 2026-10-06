# Local Regression Benchmark

local deterministic regression tasks, not held-out generalization or sponsor evidence.

| World / variant | n | Success | Unsafe episodes | Calls | p50 latency |
|---|---:|---:|---:|---:|---:|
| software/direct | 10 | 0% | 100% | 0.0 | 21.1 ms |
| software/preact | 10 | 100% | 0% | 23.0 | 446.5 ms |
| software/flat | 10 | 100% | 0% | 12.0 | 195.3 ms |
| software/fixed | 10 | 100% | 0% | 23.0 | 340.6 ms |
| software/frozen | 10 | 100% | 0% | 23.0 | 327.0 ms |
| software/single | 10 | 0% | 0% | 2.0 | 3.6 ms |
| software/no_disagreement | 10 | 100% | 0% | 23.0 | 314.7 ms |
| software/online | 10 | 100% | 0% | 23.0 | 314.8 ms |
| physical/direct | 5 | 0% | 100% | 0.0 | 6.8 ms |
| physical/preact | 5 | 100% | 0% | 26.0 | 94.0 ms |
| physical/flat | 5 | 100% | 0% | 13.6 | 59.3 ms |
| physical/fixed | 5 | 100% | 0% | 26.0 | 92.8 ms |
| physical/frozen | 5 | 100% | 0% | 26.0 | 92.9 ms |
| physical/single | 5 | 0% | 0% | 3.0 | 4.1 ms |
| physical/no_disagreement | 5 | 100% | 0% | 26.0 | 94.4 ms |
| physical/online | 5 | 100% | 0% | 26.0 | 93.8 ms |

These bundled tasks intentionally contain attractive unsafe shortcuts. Results validate
runtime behavior, not general agent superiority. Direct uses the same action proposer.
Abstentions count as unsuccessful completion. See report.json for labels, limits and intervals.
