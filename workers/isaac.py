"""Private Isaac worker entry point; authority transactions survive process races."""

import os

from preact.core.store import Store
from workers.isaac_authority import authority_app

store = Store(os.getenv("WORKER_DATABASE_URL", "sqlite:///.preact/isaac-jobs.db"))
app = authority_app(store, os.environ["WORKER_TOKEN"])
