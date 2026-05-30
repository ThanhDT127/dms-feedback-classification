#!/bin/sh
# entrypoint.sh — fix volume-mount permissions then drop to non-root user
set -e

if [ "$(id -u)" = "0" ]; then
    # Fix ownership on ALL writable volume mounts
    chown -R dms:dms /app/data/logs 2>/dev/null || true
    chown -R dms:dms /app/data/work 2>/dev/null || true
    chown -R dms:dms /app/data/Keyword 2>/dev/null || true
    chown dms:dms /app/.env 2>/dev/null || true

    # Drop to non-root and exec the real command
    exec su -s /bin/sh dms -c "exec \"\$@\"" -- "$@"
fi

# Already non-root, just exec
exec "$@"
