"""
Request signing for the Fitdays API.

Every call carries a set of public query parameters plus a ``sign``. The app
builds it as::

    sign = MD5( urlencode( "<params sorted by key, joined k=v with &>" + "hxsign" ) )

``capp_ver`` is appended to the URL *after* signing and is deliberately not
part of the signed string. The login password is hashed separately with a
double MD5 and its own salt, so the plaintext never leaves the device.
"""

from __future__ import annotations

import hashlib
import time
import urllib.parse
import uuid

from .constants import (
    APP_VERSION,
    DEFAULT_COUNTRY,
    DEFAULT_LANGUAGE,
    DEVICE_MODEL,
    PASSWORD_SALT,
    SIGN_SALT,
)


def md5_upper(value: str) -> str:
    """Uppercase hex MD5 — the app's ``v.a()`` helper."""
    return hashlib.md5(value.encode("utf-8")).hexdigest().upper()


def md5_lower(value: str) -> str:
    """Lowercase hex MD5."""
    return hashlib.md5(value.encode("utf-8")).hexdigest().lower()


def hash_password(plaintext: str) -> str:
    """
    Hash a password the way the login screen does.

    ``MD5(MD5(password + "hx"))``, uppercase hex. Only this digest is ever
    transmitted or stored, never the plaintext.
    """
    return md5_upper(md5_upper(plaintext + PASSWORD_SALT))


def new_client_id() -> str:
    """
    Generate a per-installation client id.

    The server does not pin this to anything, so a fresh random value is fine;
    reusing one across calls just makes the traffic look more like one install.
    """
    return md5_upper(str(uuid.uuid4()))


def new_request_id() -> str:
    """Generate the per-request id."""
    return md5_lower(str(uuid.uuid4()))


def signed_query(
    token: str = "",
    uid: object = 0,
    *,
    client_id: str | None = None,
    country: str = DEFAULT_COUNTRY,
    language: str = DEFAULT_LANGUAGE,
    device_model: str = DEVICE_MODEL,
    timestamp: int | None = None,
) -> dict[str, str]:
    """
    Build the public query parameters, including ``sign``.

    Returns a plain dict ready to be url-encoded onto the request.
    """
    params = {
        "app_ver": APP_VERSION,
        "client_id": client_id or new_client_id(),
        "country": country or DEFAULT_COUNTRY,
        "device_model": device_model,
        "language": language or DEFAULT_LANGUAGE,
        "os_type": "0",
        "request_id": new_request_id(),
        "source": "0",
        "timestamp": str(int(timestamp if timestamp is not None else time.time())),
        "token": token or "",
        "uid": str(uid or 0),
    }
    joined = "&".join(f"{key}={params[key]}" for key in sorted(params)) + SIGN_SALT
    params["sign"] = md5_lower(urllib.parse.quote_plus(joined, safe="*"))
    params["capp_ver"] = APP_VERSION
    return params
