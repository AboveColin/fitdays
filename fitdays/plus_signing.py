"""
Request signing and password digests for the Fitdays+ API.

Fitdays+ moved the signature from the query string into headers. The app
signs nine headers plus the ``country`` query value::

    sign = MD5( urlencode( "<k=v sorted by key, joined with &>" + "fitdayspro" ) )

then adds ``timezone`` and ``sign`` as unsigned headers. The login sends two
digests of the password, never the plaintext.
"""

from __future__ import annotations

import time
import urllib.parse

from .constants import (
    DEFAULT_COUNTRY,
    DEFAULT_LANGUAGE,
    DEVICE_MODEL,
    PLUS_APP_VERSION,
    PLUS_PACKAGE_NAME,
    PLUS_SALT,
    PLUS_USER_AGENT,
)
from .signing import md5_lower, new_request_id


def java_urlencode(value: str) -> str:
    """
    Encode like ``java.net.URLEncoder.encode(value, "UTF-8")``.

    Java leaves ``*`` alone and encodes ``~`` and ``/``. Python's
    ``quote_plus`` does the opposite for all three by default, and a password
    containing one of them would otherwise produce a digest the server rejects.
    """
    return urllib.parse.quote_plus(value, safe="*").replace("~", "%7E")


def plus_access_code(plaintext: str) -> str:
    """``MD5(MD5(password + "fitdayspro"))``, lowercase hex."""
    return md5_lower(md5_lower(plaintext + PLUS_SALT))


def plus_access_sign(plaintext: str) -> str:
    """``MD5(MD5(urlencode(password + "fitdayspro")))``, lowercase hex."""
    return md5_lower(md5_lower(java_urlencode(plaintext + PLUS_SALT)))


def new_plus_client_id() -> str:
    """A per-installation client id, lowercase hex like the app's."""
    return new_request_id()


def plus_query(country: str = DEFAULT_COUNTRY,
               language: str = DEFAULT_LANGUAGE) -> dict[str, str]:
    """The query parameters every Fitdays+ request carries."""
    return {
        "os_type": "2",
        "bapp_ver": "1.0.0",
        "country": country or DEFAULT_COUNTRY,
        "language": language or DEFAULT_LANGUAGE,
        "source": "0",
    }


def plus_headers(
    *,
    token: str = "",
    account_id: str = "",
    client_id: str,
    country: str = DEFAULT_COUNTRY,
    device_model: str = DEVICE_MODEL,
    timezone_offset_ms: int = 0,
    timestamp: int | None = None,
    request_id: str | None = None,
) -> dict[str, str]:
    """
    Build the signed headers for one request.

    ``timezone_offset_ms`` is the raw UTC offset in milliseconds, as Java's
    ``TimeZone.getRawOffset()`` reports it. It is sent but not signed.
    """
    headers = {
        "user-agent": PLUS_USER_AGENT,
        "request-id": request_id or new_request_id(),
        "client-id": client_id,
        "account-id": account_id or "",
        "app-ver": PLUS_APP_VERSION,
        "device-model": device_model,
        "package-name": PLUS_PACKAGE_NAME,
        "timestamp": str(int(timestamp if timestamp is not None else time.time())),
        "token": token or "",
    }
    signed = dict(headers, country=country or DEFAULT_COUNTRY)
    joined = "&".join(f"{key}={signed[key]}" for key in sorted(signed)) + PLUS_SALT
    headers["timezone"] = str(int(timezone_offset_ms))
    headers["sign"] = md5_lower(java_urlencode(joined))
    return headers
