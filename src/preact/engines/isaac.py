import asyncio
import json
import os
import tempfile
from importlib.resources import files
from pathlib import Path

from preact.core.interfaces import EngineFailure
from preact.core.io import durable_io
from preact.core.models import Capabilities, EvidenceKind, Prediction, State
from preact.engines.process import reap_cancelled
from preact.engines.sdk_bridge import make_sdk_bundle
from preact.engines.statistics import binomial_estimate
from workers.isaac_contract import SDK_TARGET, backend_digest


async def isaac_step(request: dict, authority=False, timeout=120):
    launcher = os.getenv("ISAAC_PYTHON", "/isaac-sim/python.sh")
    script = files("workers").joinpath("isaac_step.py")
    with tempfile.TemporaryDirectory(prefix="preact-isaac-") as folder:
        path = Path(folder)
        bridge = path / "shared-source.zip"
        bridge_digest = await durable_io(make_sdk_bundle, bridge)
        environment = {
            **os.environ,
            "PREACT_ISAAC_BRIDGE": str(bridge),
            "PREACT_ISAAC_BRIDGE_SHA256": bridge_digest,
            "PYTHONPATH": os.pathsep.join([str(bridge), *filter(None, [os.getenv("PYTHONPATH")])]),
        }
        await durable_io((path / "request.json").write_text, json.dumps(request))
        args = [launcher, str(script), str(path / "request.json"), str(path / "result.json")]
        if authority:
            args.append("--authority")
        process = await asyncio.create_subprocess_exec(
            *args,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        try:
            await asyncio.wait_for(process.communicate(), timeout)
        except (TimeoutError, asyncio.CancelledError) as error:
            await reap_cancelled(process, error)
        if process.returncode or not (path / "result.json").exists():
            raise EngineFailure("Isaac rollout failed; no substitute simulation used")
        return json.loads((path / "result.json").read_text())


class Isaac:
    def __init__(self):
        self.backend_hash = backend_digest()
        self.capabilities = Capabilities(
            engine_id="isaac-franka",
            version=f"{SDK_TARGET}+preact.{self.backend_hash}",
            family="isaac-physics",
            domains=["physical"],
            evidence=EvidenceKind.SIMULATION,
            tier=2,
            produces_successor=True,
            applicability="Reconstructed Franka scene; requires RTX, assets and live conformance validation",
        )

    async def predict(self, request):
        result = await isaac_step(request.model_dump(), timeout=request.deadline_seconds)
        self.validate_backend(result)
        checks = result["checks"]
        safe = all(checks.values())
        # One nominal simulation cannot establish <=5% residual risk at 95% confidence.
        # Keep bounds conservative until actual perturbation verification exists.
        return Prediction(
            engine_id=self.capabilities.engine_id,
            engine_version=self.capabilities.version,
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[a.id for a in request.actions],
            evidence=self.capabilities.evidence,
            success=binomial_estimate(int(safe), 1),
            risk=binomial_estimate(int(not safe), 1),
            mandatory_checks=checks,
            successor=State.model_validate(result["state"]),
            metrics=result["metrics"],
            vectors={"object_position": result["state"]["payload"]["object"]},
            violations=[k for k, v in checks.items() if not v],
            assumptions=[
                result["scope"],
                "Single nominal rollout; statistical perturbation evidence remains unresolved",
            ],
            raw={"cost_known": False, "sdk_bridge": result.get("sdk_bridge")},
        )

    def validate_backend(self, result):
        if result.get("worker_backend_sha256") != self.backend_hash:
            raise EngineFailure("Isaac worker source changed or lacks matching provenance")


class IsaacPerturbations(Isaac):
    """Measured verification over a declared bounded population, never hardware certification."""

    def __init__(self, samples=59):
        super().__init__()
        self.capabilities = self.capabilities.model_copy(
            update={
                "engine_id": "isaac-perturbations",
                "tier": 3,
                "max_samples": samples,
                "refines_engine_ids": ["isaac-franka"],
                "applicability": "IID bounded pose/mass/friction simulation population; requires live GPU conformance",
            }
        )

    async def predict(self, request):
        import time

        import numpy as np

        started, trials = time.monotonic(), []
        rng = np.random.default_rng(request.seed)
        n = min(request.sample_budget, self.capabilities.max_samples)
        for index in range(n):
            remaining = request.deadline_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Perturbation verification exhausted deadline")
            bundle = request.model_dump()
            bundle["perturbation"] = {
                "seed": int(rng.integers(0, 2**31)),
                "population": "pose-ball-mass-friction/v1",
                "sample": index,
            }
            result = await isaac_step(bundle, timeout=remaining)
            self.validate_backend(result)
            trials.append(result)
        checks = {
            key: all(t["checks"].get(key) is True for t in trials) for key in trials[0]["checks"]
        }
        good = sum(all(t["checks"].values()) for t in trials)
        return Prediction(
            engine_id=self.capabilities.engine_id,
            engine_version=self.capabilities.version,
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[a.id for a in request.actions],
            evidence=self.capabilities.evidence,
            success=binomial_estimate(good, n),
            risk=binomial_estimate(n - good, n),
            mandatory_checks=checks,
            violations=[k for k, v in checks.items() if not v],
            metrics={"goal_progress": min(t["metrics"]["goal_progress"] for t in trials)},
            sample_count=n,
            refines_engine_ids=self.capabilities.refines_engine_ids,
            # Perturbed successor samples are retained; none silently becomes the nominal state.
            raw={
                "population": "pose-ball-mass-friction/v1",
                "confidence_one_sided": 0.95,
                "trials": trials,
                "cost_known": False,
            },
            assumptions=[
                "IID declared simulation population; confidence excludes model mismatch",
                "No hardware safety claim; minimum progress across actual rollouts",
            ],
        )
