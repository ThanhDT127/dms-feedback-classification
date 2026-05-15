FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./
COPY src/ ./src/

RUN pip install --no-cache-dir . \
    && python -c "import dms"

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json,time,datetime; d=json.load(open('/app/data/work/health.json')); \
    lp=datetime.datetime.fromisoformat(d['last_poll']); \
    assert time.time()-lp.timestamp()<600, 'Stale health'" || exit 1

CMD ["python", "-m", "dms"]
