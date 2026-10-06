# Local Operational Validation

Archived execution report: statements below retain the original checkpoint scope.
Current completed v2 status is documented in [the v2 analysis](cohort-model-analysis-v2.md) and
[public progress](../docs/agent-progress.md); historical measurements and failures are unchanged.


October 4, 2026. ARM Docker production service and PostgreSQL 17.11; no Nebius deployment.
The rebuilt image serves the production UI and the shared runtime. All five Chromium
scenarios passed, including both worlds, release workflow, physical history/state,
explicit recorded replay and honest disconnected-API handling.

A real database outage returned HTTP 503 from `/api/health`; restarting the database restored
HTTP 200 without restarting the application. Previously tested concurrent ordering,
exclusive leases and pending-intent recovery remain covered by `verify_postgres.py`.

A custom-format `pg_dump` of the application database was restored into the separate
`preact_backup_verify` database. Original and restored contents matched ordered row digests
for **20 runs, 2,690 events, 90 prediction errors and 50 execution receipts**.
A tar backup of the artifact volume was extracted separately and all **360 artifacts**
matched their SHA-256 filenames. Private backups remain ignored under `.cache/backup/`.
This proves local recovery of those contents, not remote S3/cloud backup operations.

External TLS, public judge access, capacity/funding through judging, real cloud backup and
sponsor/GPU execution remain unvalidated. Do not use this report as cloud deployment proof.

The latest installed wheel safely completed software repair, physical manipulation,
the four-step release and both development cohort adapters outside the checkout.
Wheel inspection found all protected probe/worker resources; source distribution excludes
cache/provider state/private configuration. Public repository and remote CI are still pending.
PostgreSQL compare-and-set receipt checks reject aborted and duplicate completion; known
non-dispatch aborts clear pending intent without creating an outcome or prediction labels.

## Storage stall and cancellation evidence

A real row-lock probe exposed 0.6226 seconds of event-loop blocking under a 0.1-second
episode budget. The repair moves persistence off-loop and drains in-flight writes before
reconciliation; per-connection PostgreSQL statement/lock waits are bounded natively.
[Machine-readable evidence](postgres-storage-deadlines.json) and
`scripts/verify_storage_deadlines.py` retain the actual proof:

- Locked append failed after **1.0076 seconds**, rolled back both event and sequence increment,
  and the next append succeeded at sequence one. A concurrent health read succeeded.
- **141 heartbeat intervals**, maximum gap **0.0077 seconds**, while the row lock was held.
- A 0.1-second episode deadline interrupted a real delayed PostgreSQL intent. Cleanup drained
  for **0.4312 seconds**, dispatched **zero actions**, retained interrupted pending intent,
  created no outcome and rejected blind retry.
- All **112 Python tests** and **five production Chromium scenarios** passed on the repair.
  Eight-client 800-event ordering, eight-writer error deduplication, 40 exclusive job leases,
  and preservation of the exact marker after actual database restart passed again.

These are local operational measurements. Cleanup can extend past the episode deadline;
the runtime cannot forcibly roll back a running Python thread. Failed verification/result
writes do not become success; worker consumers survive SQL outages and leases expire before
reclaim. Native timeouts apply per I/O operation, not as a hard real-time service guarantee.

## Authority transaction evidence

The Isaac authority boundary previously allowed two independent instances to dispatch
from the same observed state. The actual SQLite reproduction used an injected SDK.
The repair adds a SQL reservation/revision fence and atomic state/receipt publication.
[PostgreSQL evidence](isaac-authority-transactions.json) records three independent clients,
200/409 concurrent responses and one dispatch; duplicate/stale handling, unresolved
cancellation after reconnect and transactional conflict rollback all passed.
The full Python suite passes **117 tests**. The installed wheel passed its actual
authority HTTP/SQLite boundary and duplicate-receipt check outside the checkout.
All SDK calls in these boundary checks are injected; real NVIDIA/GPU execution is unvalidated.
