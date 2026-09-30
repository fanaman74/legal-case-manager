"""Starts the web app. The launcher runs `python -m app.serve` on Windows; the
Railway image runs the same command.

Locally the app serves HTTPS with the launcher's certificate and ignores
forwarding headers (nothing sits in front of it). In cloud mode the platform
terminates TLS, so it serves plain HTTP and trusts the platform's proxy."""

import logging

import uvicorn

from . import settings


class QuietHealthChecks(logging.Filter):
    """The launcher checks /health every few seconds; logging each check would
    bury everything else in the log."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        return not (len(args) >= 3 and args[2] in ("/health", "/ready"))


def options(cfg: settings.Settings) -> dict:
    opts = {"host": cfg.host, "port": cfg.port, "server_header": False, "proxy_headers": cfg.is_cloud}
    if not cfg.is_cloud:
        if not (cfg.tls_cert and cfg.tls_key):
            raise SystemExit("TLS_CERT and TLS_KEY must be set in local mode. The launcher sets them; start the app from the Control Center.")
        opts.update(ssl_certfile=cfg.tls_cert, ssl_keyfile=cfg.tls_key)
    return opts


def main() -> None:
    logging.getLogger("uvicorn.access").addFilter(QuietHealthChecks())
    uvicorn.run("app.main:app", **options(settings.load()))


if __name__ == "__main__":
    main()
