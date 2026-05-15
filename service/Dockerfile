FROM python:3.11-slim

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY config.py auth.py sharepoint.py notification.py watcher.py \
     logging_config.py metrics.py gemini_client.py ./
COPY pipeline/ ./pipeline/

# Health check: verify watcher wrote a recent health timestamp
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json,time,datetime; d=json.load(open('/app/data/work/health.json')); \
    lp=datetime.datetime.fromisoformat(d['last_poll']); \
    assert time.time()-lp.timestamp()<600, 'Stale health'" || exit 1

# Run the watcher
CMD ["python", "watcher.py"]
