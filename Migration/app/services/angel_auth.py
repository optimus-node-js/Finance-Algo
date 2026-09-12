"""
Angel One SmartAPI multi-key session manager.

Productionized version of the AngelKeyManager pattern from
Trading/Stocks/main.ipynb (cell 1). Same behavior — round-robins across up to
5 registered API keys so a single day of heavy scanning across 500 stocks
doesn't hit Angel One's per-key rate limit — but:

  * credentials are read from environment variables instead of being
    hardcoded in a notebook cell
  * a session is (re)established lazily and reused, with automatic
    re-login if Angel One reports the session as expired
  * exposes a single get_session() call for the rest of the app to use,
    so callers don't need to know about key rotation at all

Env vars expected (same names already in Migration/.env):
    ANGEL_API_KEY_1..5
    ANGEL_CLIENT_CODE_1..5
    ANGEL_PASSWORD_1..5   (this is the MPIN, not a login password)
    ANGEL_TOTP_SECRET_1..5

Only keys 1..N that are actually present in the environment are used, so
this works fine with just ANGEL_API_KEY_1 set (single key) too.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

import pyotp
from SmartApi import SmartConnect

logger = logging.getLogger("angel_auth")


@dataclass
class AngelCredential:
    api_key: str
    client_code: str
    password: str
    totp_secret: str


def load_credentials_from_env(max_keys: int = 5) -> list[AngelCredential]:
    creds = []
    for i in range(1, max_keys + 1):
        api_key = os.environ.get(f"ANGEL_API_KEY_{i}")
        if not api_key:
            continue
        creds.append(
            AngelCredential(
                api_key=api_key,
                client_code=os.environ[f"ANGEL_CLIENT_CODE_{i}"],
                password=os.environ[f"ANGEL_PASSWORD_{i}"],
                totp_secret=os.environ[f"ANGEL_TOTP_SECRET_{i}"],
            )
        )
    if not creds:
        raise RuntimeError(
            "No Angel One credentials found in environment. "
            "Set ANGEL_API_KEY_1 / ANGEL_CLIENT_CODE_1 / ANGEL_PASSWORD_1 / ANGEL_TOTP_SECRET_1 "
            "(and _2.._5 for additional rotation keys)."
        )
    return creds


class AngelSessionPool:
    """Thread-safe round-robin pool of authenticated SmartConnect sessions."""

    def __init__(self, credentials: Optional[list[AngelCredential]] = None):
        self._creds = credentials or load_credentials_from_env()
        self._sessions: list[SmartConnect] = []
        self._lock = threading.Lock()
        self._index = 0
        self._logged_in = False

    def login_all(self) -> None:
        """Establish a session for every credential. Call once at startup."""
        with self._lock:
            self._sessions = []
            for cred in self._creds:
                try:
                    obj = SmartConnect(api_key=cred.api_key)
                    totp_code = pyotp.TOTP(cred.totp_secret).now()
                    data = obj.generateSession(cred.client_code, cred.password, totp_code)
                    if data.get("status"):
                        self._sessions.append(obj)
                        logger.info("Angel One session active for key ...%s", cred.api_key[-4:])
                    else:
                        logger.warning(
                            "Angel One login failed for key ...%s: %s",
                            cred.api_key[-4:],
                            data.get("message"),
                        )
                except Exception:
                    logger.exception("Angel One login error for key ...%s", cred.api_key[-4:])

            if not self._sessions:
                raise RuntimeError(
                    "No Angel One sessions could be established with the provided credentials."
                )
            self._logged_in = True

    def get_session(self) -> SmartConnect:
        """Return the next session in the rotation (round-robin)."""
        if not self._logged_in:
            self.login_all()
        with self._lock:
            session = self._sessions[self._index % len(self._sessions)]
            self._index += 1
            return session

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)


# Module-level singleton the rest of the app can import directly:
#   from app.services.angel_auth import angel_pool
#   session = angel_pool.get_session()
angel_pool = AngelSessionPool.__new__(AngelSessionPool)
angel_pool._creds = []  # populated lazily on first real use to avoid import-time failures
angel_pool._sessions = []
angel_pool._lock = threading.Lock()
angel_pool._index = 0
angel_pool._logged_in = False


def get_angel_session() -> SmartConnect:
    """Lazy-init the module-level pool from env vars on first call."""
    if not angel_pool._creds:
        angel_pool._creds = load_credentials_from_env()
    return angel_pool.get_session()
