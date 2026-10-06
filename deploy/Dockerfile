FROM node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS frontend
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-trixie@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016
COPY --from=ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc /uv /uvx /usr/local/bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra physical --extra sandbox --no-install-project
COPY src/ src/
COPY workers/ workers/
COPY assets/ assets/
COPY docs/licenses/ docs/licenses/
COPY scripts/ scripts/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra physical --extra sandbox
COPY --from=frontend /app/web/dist/ web/dist/
RUN mkdir /app/.preact && useradd --uid 10001 --no-create-home preact && chown -R preact /app/.preact
USER preact
ENV PATH="/app/.venv/bin:$PATH" PREACT_MODE=local
EXPOSE 8000
CMD ["preact", "serve", "--host", "0.0.0.0"]
