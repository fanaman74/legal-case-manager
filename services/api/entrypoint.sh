#!/bin/sh
# Serve HTTPS with the launcher's certificate when running locally. In cloud
# mode the platform terminates TLS, so serve plain HTTP on $PORT.
set -e
PORT="${PORT:-8443}"
if [ "${DEPLOY_MODE:-local}" = "local" ] && [ -f "${TLS_CERT:-/certs/server.crt}" ]; then
  exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
    --ssl-certfile "${TLS_CERT:-/certs/server.crt}" --ssl-keyfile "${TLS_KEY:-/certs/server.key}" \
    --proxy-headers --no-server-header
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --no-server-header
