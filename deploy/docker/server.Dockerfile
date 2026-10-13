# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
COPY core ./core
COPY server ./server
COPY worker ./worker
RUN uv sync --frozen --no-dev --package rushbase-server

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 --create-home rushbase
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" RUSHBASE_HOST=0.0.0.0 RUSHBASE_PORT=8080
USER rushbase
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD ["python", "-c", "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ['RUSHBASE_PORT'])"]
CMD ["rushbase-server"]
