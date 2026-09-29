"""Container health check for the web app: GET /health over HTTPS or HTTP."""

import os
import ssl
import sys
import urllib.request

port = os.environ.get("PORT", "8443")
scheme = "https" if os.environ.get("DEPLOY_MODE", "local") == "local" and os.path.exists(os.environ.get("TLS_CERT", "/certs/server.crt")) else "http"
ctx = ssl._create_unverified_context()  # localhost only; the cert names the LAN address
try:
    with urllib.request.urlopen(f"{scheme}://127.0.0.1:{port}/health", timeout=3, context=ctx) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
