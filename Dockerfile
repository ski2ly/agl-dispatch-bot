FROM python:3.10-slim

WORKDIR /app

# Don't write .pyc, flush stdout immediately (so Docker logs are live).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System packages (libpq for asyncpg, curl for healthcheck, tini for proper signal handling).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Python deps — copied first for cache reuse on code-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Project files
COPY . .

# Security: non-root user owns /app. Fixed UID 1000 so the host can chown the
# bind-mounted ./data and ./backups directories to match.
RUN useradd -m -u 1000 botuser && \
    mkdir -p /app/data && \
    chown -R botuser:botuser /app
USER botuser

EXPOSE 8000

# /health endpoint is registered in api/server.py (returns {"status":"ok"}).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# tini reaps zombie children and forwards SIGTERM/SIGINT to python — clean shutdowns.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "main.py"]
