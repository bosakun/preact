"""Cross-interpreter source/import proof, without loading Isaac or claiming GPU evidence."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from preact.core.models import Action, State
from preact.domains.physical import robust_clearance
from preact.engines.sdk_bridge import make_sdk_bundle

PROBE = """
import json, sys
from pathlib import Path
from preact.core.models import Action, State
from preact.domains.physical import robust_clearance
from preact.engines.sdk_bridge import sdk_import_evidence
from preact.engines.storage import artifact_store
evidence = sdk_import_evidence()
cases = json.loads(Path(sys.argv[1]).read_text())
results = []
for case in cases:
    state = State.model_validate(case['state'])
    action = Action.model_validate(case['action'])
    data = state.model_dump_json().encode()
    store = artifact_store()
    digest = store.put(data)
    assert store.read(digest) == data
    results.append({'state_id': state.id, 'fingerprint': action.fingerprint,
                    'clearance': robust_clearance(*case['geometry']),
                    'artifact_sha256': digest})
assert 'scipy' not in sys.modules and 'mujoco' not in sys.modules
evidence.update({'cases': results, 'scipy_or_mujoco_imported': False})
Path(sys.argv[2]).write_text(json.dumps(evidence))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", required=True, help="Dedicated Python 3.11 interpreter/SDK launcher"
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    cases = []
    for index in range(16):
        state = State.create("physical", {"object": [index / 10, 0, 0.2]}, "CPU-bridge-probe")
        action = Action(
            id=f"probe-{index}",
            name="Lift",
            kind="lift",
            state_id=state.id,
            payload={"waypoints": [[index / 10, 0, 0.5]]},
        )
        geometry = [[0, 0, index / 10], [1, 0, index / 10], [0.5, 0, 0.4], [0.1, 0.1, 0.1]]
        cases.append(
            {
                "state": state.model_dump(),
                "action": action.model_dump(),
                "geometry": geometry,
                "expected_fingerprint": action.fingerprint,
            }
        )
    with tempfile.TemporaryDirectory(prefix="preact-sdk-proof-") as folder:
        root = Path(folder)
        bundle = root / "shared-source.zip"
        checksum = make_sdk_bundle(bundle)
        (root / "cases.json").write_text(json.dumps(cases))
        environment = {
            **os.environ,
            "PYTHONPATH": str(bundle),
            "PREACT_ISAAC_BRIDGE": str(bundle),
            "PREACT_ISAAC_BRIDGE_SHA256": checksum,
            "PREACT_ARTIFACTS_DIR": str(root / "artifacts"),
        }
        # Never use configured live storage/credentials in this CPU-only proof.
        for key in tuple(environment):
            if key.startswith(("S3_", "AWS_", "NEBIUS_")):
                del environment[key]
        subprocess.run(
            [args.python, "-c", PROBE, str(root / "cases.json"), str(root / "result.json")],
            check=True,
            env=environment,
            timeout=60,
            capture_output=True,
        )
        report = json.loads((root / "result.json").read_text())
        assert report["bundle_sha256"] == checksum
        for case, result in zip(cases, report["cases"], strict=True):
            assert result["state_id"] == case["state"]["id"]
            assert result["fingerprint"] == case["expected_fingerprint"]
            assert result["clearance"] == robust_clearance(*case["geometry"])
            data = State.model_validate(case["state"]).model_dump_json().encode()
            assert result["artifact_sha256"] == hashlib.sha256(data).hexdigest()
        report.update(
            {
                "scope": "CPU Python 3.11 imports/identity/geometry/local-artifacts only",
                "api_python": __import__("sys").version.split()[0],
                "nvidia_validated": False,
                "case_count": len(cases),
                "validation_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            }
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(
            f"Passed {len(cases)} cross-interpreter cases; no NVIDIA validation. Report: {args.report}"
        )


if __name__ == "__main__":
    main()
