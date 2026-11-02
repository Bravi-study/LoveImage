FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN npx --yes pnpm@12.16.1 install --frozen-lockfile
COPY web/ ./
RUN npx --yes pnpm@12.16.1 run build

FROM python:3.15-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
# Editable on purpose: db.py finds migrations/ relative to its own source file.
COPY src/ src/
COPY migrations/ migrations/
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist web/dist
RUN useradd --create-home app && mkdir data && chown app data
USER app
ENV PATH=/app/.venv/bin:$PATH
CMD ["uvicorn", "--factory", "loveimage.api:create_app", "--host", "0.0.0.0", "--port", "8000"]
