# Deployment

These are reviewable deployment inputs; no cloud infrastructure has been provisioned.
The ARM Docker production image and PostgreSQL 17.11 have passed local execution,
concurrent event/error/job checks and actual database restart durability. Terraform
passes provider-schema validation. Nebius deployment and GPU acceptance remain pending.

## API, frontend and PostgreSQL

Copy .env.example to .env locally and configure a database password, real services and
PREACT_MODE. Build and start with `docker compose -f deploy/compose.yml up --build`.
The multi-stage image serves the production tree and API. App runs as UID 10001;
database and artifacts have persistent volumes. API binds localhost; a deployment
reverse proxy supplies TLS and documented judge access. Store secrets outside images.

The API applies server-owned safety/resource limits before creating a run or calling a
vendor. Clients can tighten limits but cannot raise budgets/risk/uncertainty thresholds,
lower minimum success or disable calibration. Set `PREACT_SERVICE_POLICY_JSON` to a
validated Policy object for the admitted worker budget; omitted fields use Core defaults.
This is per-episode admission control, not a cloud billing cap. The reverse proxy still
needs measured abuse controls and funded judge capacity before public deployment.

Before judging: clean-machine installation, PostgreSQL restart/receipt reconciliation,
external TLS URL, free judge access, backups and funding through December 15 20:00 UTC.
Never start paid GPU resources as part of a build or test command.

For isolated PostgreSQL verification, create a separate `preact_verification` database
inside the local Compose database container. Run `scripts/verify_postgres.py` inside
the app container, restart the database, then run it with `--resume`. The script refuses
SQLite and never targets the application database. It tests eight clients, duplicate
prediction-error insertion, exclusive leases, durable ordering and uncertain execution.

## GPU workers

Isaac and Cosmos use separate official Linux GPU environments. The Isaac worker API runs
in Python 3.12; its SDK launcher receives an automatic source-only bridge and compatible
SDK helper dependencies. Do not install the API lock into Isaac's Python 3.11. Run the
dry-run/install/import commands in docs/integrations.md before GPU conformance. Cosmos
uses its separately pinned official environment. Configure private endpoints, WORKER_TOKEN, database and
shared S3 artifact settings. No Docker socket or cloud control-plane secret belongs in
a code sandbox. See docs/integrations.md for SDK/checkpoint/RTX and compatibility gates.
Worker HTTP routes expose capabilities, prediction jobs/poll/cancel. Isaac also exposes
separate durable episode creation/observation/action authority routes.

Serverless job submission inputs must be derived from current official documentation
and admitted account capabilities. Actual worker completion and artifact return are a
release gate; a locally leased job is not a Nebius Serverless Job.
