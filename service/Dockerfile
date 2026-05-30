FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./
COPY src/ ./src/
COPY static/ ./static/
COPY entrypoint.sh /entrypoint.sh

RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir . \
    && python -c "import dms"

# Create non-root user for security.
# Directories are created here so they exist when no volume is mounted.
# When volumes ARE mounted (e.g. ./logs:/app/data/logs), the entrypoint
# fixes ownership at runtime before dropping privileges.
RUN groupadd -r dms && useradd -r -g dms -d /app -s /sbin/nologin dms \
    && mkdir -p /app/data/work /app/data/logs \
    && chown -R dms:dms /app \
    && chmod +x /entrypoint.sh

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json,time,datetime; d=json.load(open('/app/data/work/health.json')); \
    lp=datetime.datetime.fromisoformat(d['last_poll']); \
    assert time.time()-lp.timestamp()<600, 'Stale health'" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "dms"]
