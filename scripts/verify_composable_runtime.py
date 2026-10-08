"""Execute the new bounded consequence fixture and retain reviewable local evidence.

Run from the checkout with python -m scripts.verify_composable_runtime --output DIR.
This is a development fixture, not a held-out benchmark or cloud/GPU validation.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from preact.core.models import Policy, identity
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from tests.fixtures.consequences import QueueEngine, QueueWorld


async def verify(output):
    output.mkdir(parents=True, exist_ok=False)
    world = QueueWorld()
    protocol = {
        "version": "new-consequence-fixture/v1",
        "runtime_source_hash": identity(
            {
                str(p.relative_to("src/preact")): p.read_text()
                for p in sorted(Path("src/preact").rglob("*.py"))
            }
        ),
        "plan_sha256": hashlib.sha256(
            Path("docs/composable-world-model-runtime-plan.md").read_bytes()
        ).hexdigest(),
        "scope": "Bounded development queue dynamics",
        "task": world.task.model_dump(),
        "policy": Policy().model_dump(),
        "fixture_sha256": hashlib.sha256(
            Path("tests/fixtures/consequences.py").read_bytes()
        ).hexdigest(),
    }
    (output / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    store = Store("sqlite:///" + str((output / "ledger.db").resolve()))
    runtime = Runtime(
        store,
        Artifacts(str(output / "artifacts")),
        Registry([QueueEngine(world), QueueEngine(world, True)]),
    )
    result = await runtime.run(world)
    events = store.read_events(runtime.run_id)
    fast = next(n for n in runtime.nodes if n.action and n.action.payload["inflow"] == 3)
    assert result["success"] and not result["unsafe"] and result["steps"] == 1
    assert world.executed[0].payload["inflow"] == 1
    assert fast.evaluation.risk_upper == 0
    assert any(a.claim.horizon == 3 and a.check is False for a in fast.evaluation.claim_assessments)
    report = {
        "status": "passed-local-consequence-fixture",
        "result": result,
        "immediate_fast_risk": fast.evaluation.risk_upper,
        "delayed_fast_claims": [
            a.model_dump() for a in fast.evaluation.claim_assessments if a.claim.horizon > 1
        ],
        "errors": store.error_rows(),
        "events": events,
        "scope": "Actual shared-Core development execution; not sponsor/GPU validation",
    }
    (output / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
    store.db.dispose()
    return {
        "status": report["status"],
        "success": result["success"],
        "steps": result["steps"],
        "output": str(output),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.output)), indent=2))
