"""Token Factory Sandbox SDK integration, isolated from Core dependencies."""

import hashlib
import json
import os
import secrets
import time
from importlib.resources import files

from preact.core.interfaces import EngineFailure
from preact.core.models import Capabilities, EvidenceKind, Prediction, State, identity
from preact.domains.evaluation import checkout_evaluator
from preact.domains.software import PROBE
from preact.engines.local import certain


class Sandbox:
    def __init__(self, world):
        from contree_client.httpx import ContreeAsyncClient
        from contree_sdk import Contree

        key, image = os.getenv("CONTREE_API_KEY"), os.getenv("CONTREE_IMAGE")
        if not key or not image:
            raise EngineFailure("CONTREE_API_KEY and a provisioned CONTREE_IMAGE are required")
        self.world, self.image_name = world, image
        self.client = ContreeAsyncClient(
            token=key,
            base_url=os.getenv("CONTREE_BASE_URL", "https://api.tokenfactory.nebius.com/sandboxes"),
        )
        self.sdk = Contree(self.client)
        self.checkpoints = {}
        self.capabilities = Capabilities(
            engine_id="token-factory-sandbox",
            version="contree-sdk-0.3.6",
            family="sandbox-execution",
            domains=["software"],
            evidence=EvidenceKind.EXECUTABLE,
            tier=2,
            max_samples=2,
            produces_successor=True,
            applicability="Python repair probes in disposable cloud images",
        )

    async def aclose(self):
        await self.client.close()

    async def measure_release(self, state, action, deadline=20, sample_budget=2):
        """Run bounded code/migration/config/build operations in a sibling checkpoint.

        Protected evaluator inputs never enter the WorldState/proposer request.
        Every receipt represents actual SDK work; this method has no local fallback.
        """
        from preact.domains.repository import MIGRATIONS

        successor = self.world.materialize(state, action)
        parent_files = {"/" + name: body.encode() for name, body in state.payload["files"].items()}
        parent_files["/database.b64"] = state.payload["database_b64"].encode()
        key, started, operations = identity(parent_files_to_text(parent_files)), time.monotonic(), 0
        image = self.checkpoints.get(key)
        if image is None:
            if sample_budget < 2:
                raise EngineFailure("Checkpoint and verification require two operation units")
            base = await self.sdk.images.use(self.image_name)
            image = await base.run(
                command="python3",
                args=["-I", "-c", "import ast; ast.parse(open('/checkout.py').read())"],
                files=parent_files,
                disposable=False,
                preserve_env=False,
                timeout=deadline,
            )
            if image.exit_code or not image.uuid:
                raise EngineFailure("Release checkpoint preparation failed")
            self.checkpoints[key] = image
            operations += 1
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("Checkpoint exhausted the release verification deadline")
        seed = secrets.randbits(32)
        protected = json.dumps(
            {
                "expected_rows": self.world.expected_rows,
                "migrations": MIGRATIONS,
                "checkout_probe": checkout_evaluator(PROBE, seed),
            }
        ).encode()
        evaluator = files("preact.domains").joinpath("release_probe.py").read_bytes()
        request = json.dumps({"payload": successor.payload, "action": action.model_dump()}).encode()
        result = await image.run(
            command="python3",
            args=["-I", "/release.py", "/request.json", "/protected.json"],
            files={
                "/release.py": evaluator,
                "/request.json": request,
                "/protected.json": protected,
            },
            disposable=False,
            preserve_env=False,
            timeout=remaining,
            truncate_output_at=65536,
        )
        operations += 1
        if result.exit_code or result.result.truncated or not result.uuid:
            raise EngineFailure("Release verification failed or lost its checkpoint")
        measurement = json.loads(result.stdout)
        checks = measurement["checks"]
        if set(checks) != set(self.world.task.required_checks) or any(
            type(v) is not bool for v in checks.values()
        ):
            raise EngineFailure("Sandbox returned invalid release checks")
        payload = measurement["payload"]
        if set(payload["files"]) != {"checkout.py", "config.json"}:
            raise EngineFailure("Sandbox returned an invalid release source tree")
        # Validate the returned source/database before it can condition another action.
        self.world.validate(
            State.create("software", payload, "sandbox-result"),
            action.model_copy(
                update={"state_id": identity({"domain": "software", "payload": payload})}
            ),
        )
        measurement["provenance"] = {
            "platform": "nebius-token-factory-sandbox",
            "image": self.image_name,
            "parent_checkpoint": str(image.uuid),
            "result_image": str(result.uuid),
            "operation_count": operations,
            "protected_seed": seed,
            "evaluator_hash": hashlib.sha256(evaluator + protected).hexdigest(),
            "sdk_native_cost": result.result.cost,
            "cost_known": False,
        }
        return measurement

    async def measure(self, source, deadline=20, parent=None, sample_budget=2):
        started, operations = time.monotonic(), 0
        parent_files = parent.payload["files"] if parent else {"checkout.py": source}
        key = identity(parent_files)
        image = self.checkpoints.get(key)
        if image is None:
            if sample_budget < 2:
                raise EngineFailure("Checkpoint and verification require two operation units")
            base = await self.sdk.images.use(self.image_name)
            image = await base.run(
                command="python3",
                args=["-I", "-c", "import ast; ast.parse(open('/checkout.py').read())"],
                files={"/" + name: body.encode() for name, body in parent_files.items()},
                disposable=False,
                preserve_env=False,
                timeout=deadline,
            )
            if image.exit_code or not image.uuid:
                raise EngineFailure("Sandbox checkpoint preparation failed")
            self.checkpoints[key] = image
            operations += 1
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("Checkpoint exhausted the verification deadline")
        seed = secrets.randbits(32)
        evaluator = checkout_evaluator(PROBE, seed).encode()
        result = await image.run(
            command="python3",
            args=["-I", "/probe.py", "/checkout.py"],
            files={"/checkout.py": source.encode(), "/probe.py": evaluator},
            timeout=remaining,
            disposable=False,
            preserve_env=False,
            truncate_output_at=8192,
        )
        operations += 1
        if result.exit_code or result.result.truncated or not result.uuid:
            raise EngineFailure("Sandbox probe failed, was truncated or lost its checkpoint")
        # Strict decoding: syntax/errors/timeout are infrastructure/task findings, never a pass.
        checks = json.loads(result.stdout)
        expected = {
            "ordinary",
            "zero",
            "over_discount",
            "negative_total",
            "protected_invariants",
            "protected_goal",
        }
        if set(checks) != expected or any(type(v) is not bool for v in checks.values()):
            raise EngineFailure("Sandbox returned an invalid measurement")
        self.checkpoints[identity({"checkout.py": source})] = result
        return checks, {
            "image": self.image_name,
            "result_image": str(result.uuid),
            "platform": "nebius-token-factory-sandbox",
            "evaluator_hash": hashlib.sha256(evaluator).hexdigest(),
            "protected_seed": seed,
            "protected_cases": 68,
            "parent_checkpoint": str(image.uuid),
            "operation_count": operations,
            "sdk_native_cost": result.result.cost,
            "cost_known": False,
        }

    async def predict(self, request):
        action = request.actions[0]
        if getattr(self.world, "release_workflow", False):
            result = await self.measure_release(
                request.state, action, request.deadline_seconds, request.sample_budget
            )
            safe = all(result["checks"].values())
            return Prediction(
                engine_id=self.capabilities.engine_id,
                engine_version=self.capabilities.version,
                family=self.capabilities.family,
                state_id=request.state.id,
                action_ids=[action.id],
                evidence=self.capabilities.evidence,
                success=certain(safe),
                risk=certain(not safe),
                successor=State.create(
                    "software",
                    result["payload"],
                    "token-factory-release-measurement",
                    kind="hypothetical",
                    parent_id=request.state.id,
                ),
                mandatory_checks=result["checks"],
                violations=[k for k, v in result["checks"].items() if not v],
                metrics=result["metrics"],
                raw={**result["provenance"], **result["evidence"]},
                sample_count=result["provenance"]["operation_count"],
                assumptions=[
                    "Bounded migration/configuration/build and independently sampled Python invariants"
                ],
            )
        successor = self.world.materialize(request.state, action)
        measured, provenance = await self.measure(
            successor.payload["files"]["checkout.py"],
            request.deadline_seconds,
            request.state,
            request.sample_budget,
        )
        checks = {
            "syntax": True,
            "regressions": all(
                measured[k] for k in ["ordinary", "zero", "negative_total", "protected_invariants"]
            ),
        }
        successor = State.create(
            "software",
            {**successor.payload, "goal_complete": all(measured.values())},
            "token-factory-probe-measurement",
            kind="hypothetical",
            parent_id=request.state.id,
        )
        safe = all(checks.values())
        return Prediction(
            engine_id=self.capabilities.engine_id,
            engine_version=self.capabilities.version,
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[action.id],
            evidence=self.capabilities.evidence,
            success=certain(safe),
            risk=certain(not safe),
            successor=successor,
            mandatory_checks=checks,
            violations=[k for k, v in checks.items() if not v],
            metrics={"goal_progress": float(measured["over_discount"])},
            raw=provenance,
            sample_count=provenance["operation_count"],
            assumptions=["Passing probes certify tested invariants only"],
        )


def parent_files_to_text(mapping):
    return {name: content.decode() for name, content in mapping.items()}
