import asyncio
import json
import sys
from pathlib import Path

from preact.core.models import PredictionRequest
from preact.core.registry import Registry


async def run(bundle):
    request = PredictionRequest.model_validate(bundle["request"])
    if bundle["engine"] in {"isaac", "isaac-perturbations"}:
        from preact.engines.isaac import Isaac, IsaacPerturbations

        engine = Isaac() if bundle["engine"] == "isaac" else IsaacPerturbations()
    elif bundle["engine"] == "cosmos":
        from preact.engines.cosmos import Cosmos
        from preact.engines.storage import artifact_store

        engine = Cosmos(artifact_store())
    else:
        raise ValueError("Unknown batch engine")
    prediction, _ = await Registry([engine]).predict(engine, request)
    return {
        "prediction": prediction.model_dump(),
        "scope": "actual engine execution",
        "engine": engine.capabilities.model_dump(),
    }


if __name__ == "__main__":
    bundle = json.loads(Path(sys.argv[1]).read_text())
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run(bundle))
    output.write_text(json.dumps(result))
