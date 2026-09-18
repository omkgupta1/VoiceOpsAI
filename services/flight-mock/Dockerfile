FROM python:3.12-slim

# uv resolves and installs far faster than pip, and is already the toolchain
# used for the repo's scripts.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Dependencies are their own layer so that editing application code does not
# invalidate the install on every rebuild.
#
# The telemetry package is copied in rather than mounted: this is the one service
# that runs inside the compose network, so it cannot reach a path outside its own
# build context. `make up` syncs the copy — see the `vendor` target in the
# Makefile — which keeps one source of truth for the shared code.
COPY pyproject.toml ./
COPY vendor ./vendor
RUN uv pip install --system --no-cache -r pyproject.toml

COPY app ./app

EXPOSE 8002

# urllib rather than curl, to avoid an apt layer for a one-line health check.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8002/health', timeout=2).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
