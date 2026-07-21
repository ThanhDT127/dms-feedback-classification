#!/bin/sh
# Fix file permissions on bind-mounted directories before switching to dms user.
# Needed because files created on Windows host (via Git/Explorer) are mapped
# as root:root 644 in Linux containers, blocking writes by the "dms" user.
chmod -R g+w /app/data/Keyword/ 2>/dev/null || true
exec "$@"
