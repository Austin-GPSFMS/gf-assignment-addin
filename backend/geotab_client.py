"""
MyGeotab access for the GF-assignment backend.

Two very different credentials are involved:

1. The DRIVER's session (database / userName / sessionId / server) arrives
   from the Drive add-in. We never trust it blindly — `validate_driver_session`
   replays it against MyGeotab with a `Get User` for that user name. If the
   session is forged or expired, MyGeotab rejects it and so do we. If it is
   genuine, the returned User.id is who the caller really is.

2. The GPSFMS SERVICE ACCOUNT (from .env) performs every write. It is
   authenticated once and re-authenticated transparently when MyGeotab says
   the session expired (every 14 days).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any

import mygeotab
from mygeotab.exceptions import AuthenticationException, MyGeotabException

log = logging.getLogger("gf.geotab")

_GEOTAB_HOST = re.compile(r"^[a-z0-9.-]+\.geotab\.com$", re.IGNORECASE)


class SessionInvalid(Exception):
    """The driver's session could not be verified."""


def normalise_server(server: str | None) -> str:
    s = (server or "my.geotab.com").strip()
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE)
    s = s.split("/", 1)[0]
    if not _GEOTAB_HOST.match(s):
        raise SessionInvalid(f"server {s!r} is not a Geotab host")
    return s


def validate_driver_session(server: str, database: str, user_name: str, session_id: str, timeout: float = 15.0) -> dict:
    """
    Replay the driver's session against MyGeotab. Returns the User entity
    (as seen by the driver's own clearance) or raises SessionInvalid.
    """
    host = normalise_server(server)
    api = mygeotab.API(username=user_name, database=database, session_id=session_id, server=host, timeout=timeout)
    try:
        users = api.get("User", name=user_name, resultsLimit=1)
    except AuthenticationException as e:
        raise SessionInvalid("MyGeotab rejected the session (expired or not valid)") from e
    except MyGeotabException as e:
        # InvalidUserException etc. also mean "this session is not good"
        raise SessionInvalid(f"MyGeotab rejected the session: {e}") from e
    if not users:
        raise SessionInvalid("session is valid but the user record could not be read")
    user = users[0]
    if (user.get("name") or "").strip().lower() != user_name.strip().lower():
        raise SessionInvalid("session user does not match")
    return user


class ServiceAccount:
    """Thread-safe wrapper around a service-account `mygeotab.API`."""

    def __init__(self, username: str, password: str, database: str, server: str | None = None, timeout: float = 30.0):
        self._username = username
        self._password = password
        self._database = database
        self._server = normalise_server(server) if server else None
        self._timeout = timeout
        self._api: mygeotab.API | None = None
        self._lock = threading.Lock()

    # -- auth --------------------------------------------------------------
    def _connect(self) -> mygeotab.API:
        api = mygeotab.API(
            username=self._username,
            password=self._password,
            database=self._database,
            server=self._server,
            timeout=self._timeout,
        )
        api.authenticate()
        log.info("service account authenticated to %s on %s", self._database, api.credentials.server)
        return api

    def api(self) -> mygeotab.API:
        with self._lock:
            if self._api is None:
                self._api = self._connect()
            return self._api

    def _retry_auth(self, fn):
        try:
            return fn(self.api())
        except AuthenticationException:
            log.warning("service session expired — re-authenticating")
            with self._lock:
                self._api = self._connect()
            return fn(self.api())

    # -- reads -------------------------------------------------------------
    def get(self, type_name: str, **search) -> list[dict]:
        return self._retry_auth(lambda api: api.get(type_name, **search))

    def call(self, method: str, **params) -> Any:
        return self._retry_auth(lambda api: api.call(method, **params))

    def get_user_by_id(self, user_id: str) -> dict | None:
        users = self.get("User", id=user_id)
        return users[0] if users else None

    def get_user_by_name(self, name: str) -> dict | None:
        users = self.get("User", name=name, resultsLimit=1)
        return users[0] if users else None

    # -- writes ------------------------------------------------------------
    def set_user(self, user: dict) -> None:
        self._retry_auth(lambda api: api.set("User", user))


class GroupCache:
    """Cache of the full Group list (and derived GF list) for a few minutes."""

    def __init__(self, svc: ServiceAccount, ttl_seconds: int = 300):
        self._svc = svc
        self._ttl = ttl_seconds
        self._groups: list[dict] | None = None
        self._at = 0.0
        self._lock = threading.Lock()

    def groups(self, force: bool = False) -> list[dict]:
        with self._lock:
            if force or self._groups is None or (time.monotonic() - self._at) > self._ttl:
                self._groups = self._svc.get("Group")
                self._at = time.monotonic()
            return self._groups


def service_account_from_env() -> ServiceAccount:
    user = os.environ.get("GEOTAB_USERNAME")
    pw = os.environ.get("GEOTAB_PASSWORD")
    db = os.environ.get("GEOTAB_DATABASE")
    if not (user and pw and db):
        raise RuntimeError("GEOTAB_USERNAME / GEOTAB_PASSWORD / GEOTAB_DATABASE must be set")
    return ServiceAccount(user, pw, db, os.environ.get("GEOTAB_SERVER") or None)
