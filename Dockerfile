FROM ghcr.io/astral-sh/uv:0.11.2 AS uv
FROM python:3.13-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY antispam_bot ./antispam_bot
COPY main.py .

CMD ["/app/.venv/bin/python", "main.py"]
