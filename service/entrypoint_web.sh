#!/bin/bash
# entrypoint_web.sh: Khởi động Web app bằng Gunicorn + Uvicorn Workers

set -e

# Đảm bảo CWD là thư mục /app (nơi chứa .env và các tài nguyên runtime)
cd /app

echo "🚀 Khởi động Gunicorn Web Server..."
echo "👥 Số lượng workers: ${GUNICORN_WORKERS:-2}"

exec gunicorn "dms.web.app:create_app()" \
    -w "${GUNICORN_WORKERS:-2}" \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8501 \
    --timeout 600 \
    --graceful-timeout 120 \
    --keep-alive 120 \
    --access-logfile - \
    --error-logfile -
