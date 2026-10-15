# Reviewed Queue exhibition recordings

`recordings/*.json.gz` are read-only exhibition projections of six actual seed11
Queue cases from the frozen `common-learning-v2` protocol. The initial public source
is main `5dd9372c69b2dcc2a49590d02e8642685c79b1bf` (PR #18 merged).

They contain selected original Runtime and learning/lifecycle events and allowlisted
lifecycle artifact summaries, not source databases, private service schedules, model
weights or protected evaluator inputs. All original event references and timestamps
survive. Numeric forecast paths are retained as numbers; no State is synthesized.

Each case contains56 actual task/probe receipts. The existing independent common
learning auditor passed each case before export. Its report, source/protocol hashes,
case bundle hash and checksum are retained inside the projection. `catalog.json`
pins the approved uncompressed projection digests. A separate projection audit
checks every exported event against the original source and every Actual against
its completed receipt. Hashes are integrity checks, not execution authority.

The catalog includes stable high, stable low, High-to-Low and Low-to-High; both
change cases include recovery and no-recovery. Stable cases have zero promotions.
Changed/recovered cases contain one historical explicit operator promotion each.
Low-to-High recovery Reward32.9 versus no-recovery115.4 is deliberately retained.
Goal999 is the original continuous fixture and is not achieved by these sessions.

The UI never imports replay into a live receipt/calibration/learning ledger.
See [the operating and reproduction guide](../../docs/osc-playground.md).
