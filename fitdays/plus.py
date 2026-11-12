"""
Async client for the Fitdays+ cloud API (package ``cn.icomon.fitdayspro``).

Fitdays 1.28.5 offers to move an account to Fitdays+. The move freezes the old
account and then deletes it, after which :class:`~fitdays.FitdaysClient` gets
``code 11000`` on login. This client speaks the Fitdays+ protocol and returns
the same models, so a caller switches backends by switching the class.

It behaves like :class:`~fitdays.FitdaysClient` in the two places that matter:

* **Region redirects.** A login on the wrong region answers ``code 302`` with
  the right host in ``data.domain``. The client moves there and retries, at
  most :data:`~fitdays.constants.PLUS_MAX_REGION_REDIRECTS` times.
* **Self-healing login.** ``code 10000`` means the token is no longer valid.
  When the session carries the password digests, the client logs in again,
  retries the call once and fires ``token_updated``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional, Union

import aiohttp

from .constants import (
    DEFAULT_COUNTRY,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_LANGUAGE,
    DEFAULT_TIMEOUT,
    DEVICE_MODEL,
    PLUS_ACCOUNT_FROZEN_CODE,
    PLUS_DEFAULT_API_BASE,
    PLUS_LOGIN_ERROR_CODES,
    PLUS_MAX_REGION_REDIRECTS,
    PLUS_TOKEN_INVALID_CODE,
    REGION_REDIRECT_CODE,
    SUCCESS_CODE,
)
from .exceptions import (
    FitdaysAPIError,
    FitdaysAuthError,
    FitdaysNetworkError,
    FitdaysValidationError,
)
from .models import Measurement, PlusSession, ScaleDevice, SyncResult, UserProfile
from .plus_signing import (
    new_plus_client_id,
    plus_access_code,
    plus_access_sign,
    plus_headers,
    plus_query,
)

_LOGGER = logging.getLogger(__name__)

PlusTokenUpdatedCallback = Callable[[PlusSession], Union[None, Awaitable[None]]]

LOGIN_PATH = "/api/account/login"
SYNC_PATH = "/api/sync/sync_from_server"
INCREMENTS_PATH = "/api/sync/sync_increments"


def _raw_utc_offset_ms() -> int:
    """The local standard-time UTC offset in ms, like Java's ``getRawOffset()``."""
    return -time.timezone * 1000


class FitdaysPlusClient:
    """Talks to the Fitdays+ cloud on behalf of one account."""

    def __init__(
        self,
        session: PlusSession,
        *,
        http_session: Optional[aiohttp.ClientSession] = None,
        token_updated: Optional[PlusTokenUpdatedCallback] = None,
        timeout: int = DEFAULT_TIMEOUT,
        device_model: str = DEVICE_MODEL,
    ):
        self.session = session
        self._http = http_session
        self._owns_http = http_session is None
        self._token_updated = token_updated
        self._timeout = timeout
        self._device_model = device_model
        self._relogin_lock = asyncio.Lock()

        if not session.api_base:
            session.api_base = PLUS_DEFAULT_API_BASE
        if not session.country:
            session.country = DEFAULT_COUNTRY
        if not session.language:
            session.language = DEFAULT_LANGUAGE
        if not session.client_id:
            session.client_id = new_plus_client_id()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    @classmethod
    async def login(
        cls,
        email: str,
        password: str,
        *,
        http_session: Optional[aiohttp.ClientSession] = None,
        token_updated: Optional[PlusTokenUpdatedCallback] = None,
        country: str = DEFAULT_COUNTRY,
        language: str = DEFAULT_LANGUAGE,
        api_base: str = PLUS_DEFAULT_API_BASE,
        timeout: int = DEFAULT_TIMEOUT,
        device_model: str = DEVICE_MODEL,
    ) -> "FitdaysPlusClient":
        """
        Authenticate with email + password and return a ready client.

        The plaintext password is turned into the two login digests at once and
        then dropped. Only the digests are kept on the session.
        """
        if not email or not password:
            raise FitdaysValidationError("email and password are required")

        client = cls(
            PlusSession(
                email=email,
                access_code=plus_access_code(password),
                access_sign=plus_access_sign(password),
                api_base=api_base,
                country=country,
                language=language,
            ),
            http_session=http_session,
            token_updated=token_updated,
            timeout=timeout,
            device_model=device_model,
        )
        try:
            await client.async_login()
        except Exception:
            await client.aclose_if_owned()
            raise
        return client

    @classmethod
    def from_session(
        cls,
        session: Union[PlusSession, dict[str, Any]],
        *,
        http_session: Optional[aiohttp.ClientSession] = None,
        token_updated: Optional[PlusTokenUpdatedCallback] = None,
        timeout: int = DEFAULT_TIMEOUT,
        device_model: str = DEVICE_MODEL,
    ) -> "FitdaysPlusClient":
        """Rebuild a client from a stored :class:`PlusSession` (or its dict form)."""
        if isinstance(session, dict):
            session = PlusSession.from_dict(session)
        if not session.token and not session.can_relogin:
            raise FitdaysValidationError(
                "session needs either a token or an email + access_code + access_sign"
            )
        return cls(
            session,
            http_session=http_session,
            token_updated=token_updated,
            timeout=timeout,
            device_model=device_model,
        )

    async def __aenter__(self) -> "FitdaysPlusClient":
        return self

    async def __aexit__(self, *_exc_info: Any) -> None:
        await self.aclose_if_owned()

    async def aclose_if_owned(self) -> None:
        """Close the HTTP session, but only if this client created it."""
        if self._owns_http and self._http is not None and not self._http.closed:
            await self._http.close()
        if self._owns_http:
            self._http = None

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _http_session(self) -> aiohttp.ClientSession:
        """Lazily create an HTTP session when the caller did not supply one."""
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
            self._owns_http = True
        return self._http

    async def _post(self, path: str, body: dict[str, Any], *,
                    authenticated: bool) -> dict[str, Any]:
        """POST one signed request and return the decoded envelope."""
        headers = plus_headers(
            token=(self.session.token or "") if authenticated else "",
            account_id=(self.session.account_id or "") if authenticated else "",
            client_id=self.session.client_id or "",
            country=self.session.country or DEFAULT_COUNTRY,
            device_model=self._device_model,
            timezone_offset_ms=_raw_utc_offset_ms(),
        )
        try:
            async with self._http_session().post(
                f"{self.session.api_base}{path}",
                params=plus_query(self.session.country or "", self.session.language or ""),
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as response:
                if response.status >= 500:
                    raise FitdaysAPIError(
                        "Fitdays+ cloud returned a server error",
                        status_code=response.status,
                    )
                try:
                    payload = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise FitdaysAPIError(
                        "Fitdays+ returned a non-JSON response",
                        status_code=response.status,
                    ) from err
                if not isinstance(payload, dict):
                    raise FitdaysAPIError("Unexpected response shape from Fitdays+")
                payload.setdefault("_http_status", response.status)
                return payload
        except asyncio.TimeoutError as err:
            raise FitdaysNetworkError(f"Timeout talking to Fitdays+: {err}") from err
        except aiohttp.ClientError as err:
            raise FitdaysNetworkError(f"Network error talking to Fitdays+: {err}") from err

    @staticmethod
    def _code(payload: dict[str, Any]) -> Optional[int]:
        """Pull the numeric ``code`` out of an envelope. Fitdays+ sends it as a string."""
        try:
            return int(str(payload.get("code")))
        except (TypeError, ValueError):
            return None

    def _switch_region(self, payload: dict[str, Any]) -> bool:
        """
        Follow a ``code 302`` region redirect. True when the base changed.

        The app accepts only a full URL here (``y.b.S0``) and stores it without
        a trailing slash. The paths already start with ``/api``.
        """
        data = payload.get("data")
        domain = str(data.get("domain") or "") if isinstance(data, dict) else ""
        if not domain.startswith("http"):
            return False
        new_base = domain.rstrip("/")
        if new_base == self.session.api_base:
            return False
        _LOGGER.debug("Fitdays+ region redirect -> %s", new_base)
        self.session.api_base = new_base
        return True

    async def _request(self, path: str, body: dict[str, Any], *,
                       authenticated: bool = True,
                       allow_relogin: bool = True) -> dict[str, Any]:
        """
        Signed request with region-redirect and expired-token handling.

        Returns the ``data`` block on success.
        """
        token_in_use = self.session.token
        payload = await self._post(path, body, authenticated=authenticated)
        code = self._code(payload)

        redirects = 0
        while code == REGION_REDIRECT_CODE and self._switch_region(payload):
            redirects += 1
            if redirects > PLUS_MAX_REGION_REDIRECTS:
                raise FitdaysAPIError(
                    f"Fitdays+ redirected more than {PLUS_MAX_REGION_REDIRECTS} times "
                    f"(last to {self.session.api_base}); the app gives up at that "
                    "point too",
                    code=code,
                )
            payload = await self._post(path, body, authenticated=authenticated)
            code = self._code(payload)

        if code == SUCCESS_CODE:
            data = payload.get("data")
            return data if isinstance(data, dict) else {}

        message = payload.get("msg") or f"Fitdays+ API error (code {code})"

        if code == PLUS_TOKEN_INVALID_CODE and authenticated:
            if not allow_relogin:
                raise FitdaysAuthError(
                    "Fitdays+ rejected the token again right after a fresh login"
                )
            if not self.session.can_relogin:
                raise FitdaysAuthError(
                    "Fitdays+ token is no longer valid and no credentials are stored "
                    "to renew it"
                )
            _LOGGER.debug("Fitdays+ token rejected (code %s), logging in again", code)
            await self._relogin(token_in_use)
            return await self._request(path, body, allow_relogin=False)

        if code == PLUS_ACCOUNT_FROZEN_CODE:
            raise FitdaysAuthError(
                f"Fitdays+ account is frozen (code {code}). A migration between "
                "Fitdays and Fitdays+ freezes the account until it finishes; open "
                "the Fitdays+ app to complete it"
            )
        if code in PLUS_LOGIN_ERROR_CODES:
            raise FitdaysAuthError(f"Fitdays+ login failed: {message} (code {code})")

        raise FitdaysAPIError(message, code=code,
                              status_code=payload.get("_http_status"))

    async def _relogin(self, stale_token: Optional[str]) -> None:
        """
        Log in again unless a concurrent request already did.

        ``stale_token`` is the token that was rejected. When the session holds a
        different one after the lock, another request renewed it first.
        """
        async with self._relogin_lock:
            if self.session.token != stale_token:
                return
            await self.async_login()

    # ------------------------------------------------------------------
    # auth
    # ------------------------------------------------------------------

    async def async_login(self) -> PlusSession:
        """
        Log in with the stored email and password digests.

        Updates :attr:`session` in place and fires ``token_updated``.
        """
        if not self.session.can_relogin:
            raise FitdaysValidationError(
                "email, access_code and access_sign are required to log in"
            )

        data = await self._request(
            LOGIN_PATH,
            {
                "type": 1,
                "account": self.session.email,
                "vcode": "",
                "access_code": self.session.access_code,
                "access_sign": self.session.access_sign,
            },
            authenticated=False,
        )

        account = data.get("account") or {}
        token = account.get("token")
        if not token:
            raise FitdaysAuthError("Fitdays+ login succeeded but returned no token")

        self.session.token = token
        self.session.account_id = str(account.get("account_id") or "") or None

        await self._fire_token_updated()
        return self.session

    async def _fire_token_updated(self) -> None:
        """Notify the caller that the session changed, sync or async."""
        if self._token_updated is None:
            return
        result = self._token_updated(self.session)
        if asyncio.iscoroutine(result):
            await result

    async def async_verify(self) -> bool:
        """Cheap round-trip that proves the current token still works."""
        await self._increments(int(time.time()))
        return True

    # ------------------------------------------------------------------
    # data
    # ------------------------------------------------------------------

    async def _increments(self, since: int) -> dict[str, Any]:
        return await self._request(
            INCREMENTS_PATH, {"sync_time": int(since), "from_data_migration": False}
        )

    async def get_sync(self, days: int = DEFAULT_HISTORY_DAYS) -> SyncResult:
        """
        Pull measurement history plus the account's profiles and devices.

        This costs two requests. ``sync_from_server`` returns weigh-ins and
        devices but no member profiles, and ``sync_increments`` returns the
        profiles on every call.
        """
        if days <= 0:
            raise FitdaysValidationError("days must be positive")
        now = int(time.time())
        data = await self._request(
            SYNC_PATH, {"start_time": now - days * 86400, "end_time": now, "count": 0}
        )
        users = (await self._increments(now)).get("users") or []
        return SyncResult.from_plus_api(data, users=users)

    async def get_increments(self, since: int) -> SyncResult:
        """Fetch only what changed since a unix timestamp, profiles included."""
        return SyncResult.from_plus_api(await self._increments(since))

    async def get_measurements(
        self,
        suid: Optional[int] = None,
        days: int = DEFAULT_HISTORY_DAYS,
    ) -> list[Measurement]:
        """Measurement history, newest first, optionally for one profile."""
        result = await self.get_sync(days=days)
        return result.for_profile(suid)

    async def get_latest(self, suid: Optional[int] = None,
                         days: int = DEFAULT_HISTORY_DAYS) -> Optional[Measurement]:
        """The most recent measurement, optionally for one profile."""
        result = await self.get_sync(days=days)
        return result.latest(suid)

    async def get_profiles(self) -> list[UserProfile]:
        """The member profiles on this account."""
        return (await self.get_increments(int(time.time()))).profiles

    async def get_devices(self) -> list[ScaleDevice]:
        """The scales bound to this account."""
        result = await self.get_sync(days=1)
        return result.devices
