FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./
COPY src/ ./src/
COPY static/ ./static/

RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -e . \
    && python -c "import dms"

# Create non-root user and pre-create data directories (UID/GID 1000 matches default VM users)
RUN groupadd -g 1000 dms && useradd -r -u 1000 -g dms dms \
    && mkdir -p /app/data/work /app/data/logs \
    && chown -R dms:dms /app

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json,time,datetime; d=json.load(open('/app/data/work/health.json')); \
    lp=datetime.datetime.fromisoformat(d['last_poll']); \
    assert time.time()-lp.timestamp()<600, 'Stale health'" || exit 1

USER dms

CMD ["python", "-m", "dms"]
