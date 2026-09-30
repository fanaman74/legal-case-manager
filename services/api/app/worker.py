"""Background worker. Phase 1 only proves the worker is alive: it writes a
heartbeat file that the launcher reads. Real jobs (conversion, chunking,
embedding) arrive in phase 3, with a job queue kept in SQLite."""

import logging
import signal
import sys
import time

from . import settings

INTERVAL = 5

log = logging.getLogger("worker")


def beat(cfg: settings.Settings) -> None:
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.heartbeat_file.with_suffix(".tmp")
    tmp.write_text(f"{int(time.time())} {cfg.version}\n")
    tmp.replace(cfg.heartbeat_file)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = settings.load()
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker started, version %s", cfg.version)
    healthy = None
    while not stop:
        try:
            beat(cfg)
            if healthy is not True:
                log.info("writing heartbeats to the data folder")
            healthy = True
        except OSError as exc:
            if healthy is not False:
                log.warning("can't write to the data folder: %s", exc)
            healthy = False
        time.sleep(INTERVAL)
    log.info("worker stopping")
    return 0


def healthcheck(cfg: settings.Settings | None = None) -> int:
    """Exit 0 if the heartbeat is fresh."""
    cfg = cfg or settings.load()
    try:
        age = time.time() - int(cfg.heartbeat_file.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 1
    return 0 if age < INTERVAL * 6 else 1


if __name__ == "__main__":
    sys.exit(healthcheck() if sys.argv[1:] == ["health"] else main())
