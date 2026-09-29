"""Background worker. Phase 1 only proves the worker is alive and can reach
the job queue: it writes a heartbeat file that the container health check
reads. Real jobs (conversion, chunking, embedding) arrive in phase 3."""

import logging
import signal
import sys
import time
from pathlib import Path

import redis

from . import settings

HEARTBEAT = Path("/tmp/worker-heartbeat")
INTERVAL = 5

log = logging.getLogger("worker")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = settings.load()
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    client = redis.Redis.from_url(cfg.redis_url, socket_timeout=3)
    log.info("worker started, version %s", cfg.version)
    connected = None
    while not stop:
        try:
            client.set("worker:heartbeat", int(time.time()), ex=60)
            HEARTBEAT.write_text(str(int(time.time())))
            if connected is not True:
                log.info("connected to the job queue")
            connected = True
        except redis.RedisError as exc:
            if connected is not False:
                log.warning("job queue unreachable: %s", exc)
            connected = False
        time.sleep(INTERVAL)
    log.info("worker stopping")
    return 0


def healthcheck() -> int:
    """Exit 0 if the heartbeat is fresh (used by the Docker health check)."""
    try:
        age = time.time() - int(HEARTBEAT.read_text())
    except (OSError, ValueError):
        return 1
    return 0 if age < INTERVAL * 6 else 1


if __name__ == "__main__":
    sys.exit(healthcheck() if sys.argv[1:] == ["health"] else main())
