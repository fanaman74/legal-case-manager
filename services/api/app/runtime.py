"""Everything a request needs that outlives it: settings, the database, case
storage and the sign-in throttle. Created once per app; the database opens on
first use so importing the app never touches the data folder."""

import threading
from functools import cached_property

from fastapi import Request

from . import settings
from .db import Database
from .storage import Storage
from .throttle import Throttle


class Runtime:
    def __init__(self, cfg: settings.Settings):
        self.cfg = cfg
        self.throttle = Throttle()
        self._lock = threading.Lock()

    @cached_property
    def db(self) -> Database:
        with self._lock:
            return Database(self.cfg.db_path)

    @cached_property
    def storage(self) -> Storage:
        return Storage(self.cfg.cases_dir)


def get(request: Request) -> Runtime:
    return request.app.state.rt
