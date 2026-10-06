"""Launch inside the pinned Cosmos repository's GPU Python environment."""

import os

from preact.core.store import Store
from preact.engines.cosmos import Cosmos
from preact.engines.storage import artifact_store
from preact.service.worker import worker_app

app = worker_app(
    Cosmos(artifact_store()),
    Store(os.getenv("WORKER_DATABASE_URL", "sqlite:///.preact/cosmos-jobs.db")),
    os.environ["WORKER_TOKEN"],
)
