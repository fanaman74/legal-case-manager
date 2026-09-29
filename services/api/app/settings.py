"""Settings come only from environment variables, so the same image runs on
the local machine (DEPLOY_MODE=local) and on a cloud host such as Railway
(DEPLOY_MODE=cloud) without code changes."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    redis_url: str
    deploy_mode: str
    version: str

    @property
    def is_cloud(self) -> bool:
        return self.deploy_mode == "cloud"


def load() -> Settings:
    mode = os.environ.get("DEPLOY_MODE", "local")
    if mode not in ("local", "cloud"):
        raise ValueError(f"DEPLOY_MODE must be 'local' or 'cloud', got {mode!r}")
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        redis_url=os.environ.get("REDIS_URL", "redis://queue:6379/0"),
        deploy_mode=mode,
        version=os.environ.get("APP_VERSION", "0.1.0-dev"),
    )
