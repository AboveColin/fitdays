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


# ----------------------------------------------------------------------
# Fitdays+ (package ``cn.icomon.fitdayspro``), read from the 1.14.4 APK.
# Fitdays 1.28.5 moves accounts to this backend and freezes the old one, so a
# migrated account only logs in here.
# ----------------------------------------------------------------------

PLUS_API_BASE_EU = "https://plus-eu.fitdays.cn"
PLUS_API_BASES = {
    "eu": PLUS_API_BASE_EU,
    "cn": "https://plus-cn.fitdays.cn",
    "us": "https://plus-us.fitdays.cn",
    "global": "https://plus.fitdays.cn",
}
PLUS_DEFAULT_API_BASE = PLUS_API_BASE_EU

PLUS_APP_VERSION = "1.14.4"
PLUS_PACKAGE_NAME = "cn.icomon.fitdayspro"
PLUS_USER_AGENT = f"FitdaysPlus-{PLUS_APP_VERSION}"
# One salt serves both the request signature and the password digests.
PLUS_SALT = "fitdayspro"

PLUS_TOKEN_INVALID_CODE = 10000
PLUS_ACCOUNT_FROZEN_CODE = 11008
# 11000 and 11022 are a wrong account or password, 11010 an unknown account.
PLUS_LOGIN_ERROR_CODES = frozenset({11000, 11008, 11010, 11022})
# The app follows a region redirect at most 3 times per request (i0/d.java).
PLUS_MAX_REGION_REDIRECTS = 3
