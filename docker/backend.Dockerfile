# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel

WORKDIR /build
COPY backend/ /build/

ARG BACKEND_EXTRAS=""
RUN if [ -n "${BACKEND_EXTRAS}" ]; then \
        /opt/venv/bin/python -m pip install ".[${BACKEND_EXTRAS}]"; \
    else \
        /opt/venv/bin/python -m pip install .; \
    fi

FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    HOME=/home/app \
    XDG_CACHE_HOME=/app/runtime/model-cache \
    OMP_NUM_THREADS=4 \
    OPENBLAS_NUM_THREADS=4 \
    MKL_NUM_THREADS=4 \
    NUMEXPR_NUM_THREADS=4

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && mkdir -p \
        /app/runtime/data \
        /app/runtime/artifacts \
        /app/runtime/temp \
        /app/runtime/logs \
        /app/runtime/model-cache \
        /app/secrets \
    && chown -R app:app /app /home/app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app backend/ /app/
COPY --chown=root:root docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint
RUN chmod 0755 /usr/local/bin/backend-entrypoint

WORKDIR /app
USER app

VOLUME ["/app/runtime", "/app/secrets"]
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=4 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).read(1024)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/backend-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
