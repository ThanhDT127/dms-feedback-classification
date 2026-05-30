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

# Create non-root user + fix entrypoint line endings (CRLF→LF for Windows→Linux)
RUN groupadd -r dms && useradd -r -g dms -d /app -s /sbin/nologin dms \
    && mkdir -p /app/data/work /app/data/logs /app/data/Keyword \
    && chown -R dms:dms /app \
    && sed -i 's/\r$//' /entrypoint.sh \
    && chmod +x /entrypoint.sh

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json,time,datetime; d=json.load(open('/app/data/work/health.json')); \
    lp=datetime.datetime.fromisoformat(d['last_poll']); \
    assert time.time()-lp.timestamp()<600, 'Stale health'" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "dms"]
