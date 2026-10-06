"""Private strong verifier, separate from authoritative episodes and nominal workers."""

import os

from preact.core.store import Store
from preact.engines.isaac import IsaacPerturbations
from preact.service.worker import worker_app

app = worker_app(
    IsaacPerturbations(),
    Store(os.getenv("WORKER_DATABASE_URL", "sqlite:///.preact/isaac-perturbations.db")),
    os.environ["WORKER_TOKEN"],
)
