"""
Endpoints, client identifiers and signing salts for the Fitdays cloud API.

Every value here comes from the Fitdays Android app itself (package
``cn.fitdays.fitdays`` by GUANGDONG ICOMON) — none of it is personal data.
There is no public/documented API; these constants are what the app sends.
"""

from __future__ import annotations

#: Default (European) API root. The server may hand out a different host for
#: accounts registered in another region — see :data:`REGION_REDIRECT_CODE`.
API_BASE_EU = "https://online-eu.fitdays.cn/api"

#: Known regional roots, keyed by the short code the app uses.
API_BASES = {
    "eu": API_BASE_EU,
    "cn": "https://online.fitdays.cn/api",
    "us": "https://online-us.fitdays.cn/api",
}

DEFAULT_API_BASE = API_BASE_EU

#: App version string sent as ``app_ver``/``capp_ver``.
APP_VERSION = "1.27.6"

#: Salt appended to the sorted query string before hashing it into ``sign``.
SIGN_SALT = "hxsign"

#: Salt used by the login password hash (``MD5(MD5(password + PASSWORD_SALT))``).
PASSWORD_SALT = "hx"

#: Sent as ``device_model``. The server does not validate it.
DEVICE_MODEL = "HomeAssistant"

#: Query parameters that are *not* part of the signed string.
UNSIGNED_PARAMS = ("capp_ver", "sign")

#: The app talks OkHttp; a default urllib/aiohttp agent also works, but this
#: keeps requests indistinguishable from the real client.
USER_AGENT = "okhttp/4.9.1"

DEFAULT_HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "User-Agent": USER_AGENT,
}

#: ``code`` value returned on success.
SUCCESS_CODE = 0

#: ``code`` returned when the account belongs to another region; the payload
#: then carries a ``domain`` to retry against.
REGION_REDIRECT_CODE = 302

#: ``code`` values that mean "your token is no longer valid".
AUTH_ERROR_CODES = frozenset({401, 1002, 1003, 10001})

#: How far back :meth:`FitdaysClient.get_measurements` reaches by default.
DEFAULT_HISTORY_DAYS = 400

DEFAULT_TIMEOUT = 20

DEFAULT_COUNTRY = "NL"
DEFAULT_LANGUAGE = "en"
