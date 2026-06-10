FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

WORKDIR /app

ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --locked

COPY . .

CMD ["uv", "run", "pytest", "-v"]
