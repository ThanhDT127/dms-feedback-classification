#!/bin/sh
# entrypoint.sh — fix volume-mount permissions then drop to non-root user
set -e

# When containers start, mounted volumes (./logs, ./work) are owned by the HOST user
# (typically root on Linux VMs). Fix ownership so the 'dms' user can write to them.
# This block only runs if we ARE root (i.e., default Docker behavior).
if [ "$(id -u)" = "0" ]; then
    chown -R dms:dms /app/data/logs /app/data/work 2>/dev/null || true
    # Drop privileges and exec the real command as 'dms'
    exec su -s /bin/sh dms -c 'exec "$@"' -- "$@"
fi

# Already non-root (e.g., docker-compose user: override), just exec
exec "$@"
