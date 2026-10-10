"""Settings come only from environment variables, so the same code runs on the
local Windows machine (DEPLOY_MODE=local, started by the launcher) and on a
cloud host such as Railway (DEPLOY_MODE=cloud) without changes."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    deploy_mode: str
    version: str
    host: str = "0.0.0.0"
    port: int = 8443
    tls_cert: str = ""
    tls_key: str = ""
    max_upload_bytes: int = 2 * 2**30

    @property
    def is_cloud(self) -> bool:
        return self.deploy_mode == "cloud"

    @property
    def run_dir(self) -> Path:
        """Small runtime files (worker heartbeat) the launcher reads."""
        return self.data_dir / ".run"

    @property
    def heartbeat_file(self) -> Path:
        return self.run_dir / "worker-heartbeat"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def cases_dir(self) -> Path:
        return self.data_dir / "cases"

    @property
    def admin_account_file(self) -> Path:
        """Written by the launcher when the Admin account is created in the
        setup wizard (launcher/internal/server/appadmin.go)."""
        return self.run_dir / "admin-account.json"


def load() -> Settings:
    mode = os.environ.get("DEPLOY_MODE", "local")
    if mode not in ("local", "cloud"):
        raise ValueError(f"DEPLOY_MODE must be 'local' or 'cloud', got {mode!r}")
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        deploy_mode=mode,
        version=os.environ.get("APP_VERSION", "0.1.0-dev"),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8443")),
        tls_cert=os.environ.get("TLS_CERT", ""),
        tls_key=os.environ.get("TLS_KEY", ""),
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_MB", "2048")) * 2**20,
    )
