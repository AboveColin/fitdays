"""
Async client for the Fitdays (ICOMON) cloud API.

The API is undocumented and read-only as far as this library is concerned: it
logs in, then pulls measurement history. Two behaviours are worth knowing
about because they are the reason this class exists rather than a bare
``post()`` helper:

* **Region redirects.** An account registered outside Europe answers with
  ``code 302`` and the host it actually lives on. The client follows that once
  and remembers the new base for the rest of its life.
* **Self-healing login.** Tokens are long-lived but not eternal. When one is
  rejected and the session carries an email + password digest, the client logs
  in again transparently and retries the call. Callers get a ``token_updated``
  callback so they can persist the new token.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from typing import Any, Awaitable, Callable, Optional, Union

import aiohttp

from .constants import (
    AUTH_ERROR_CODES,
    DEFAULT_API_BASE,
    DEFAULT_COUNTRY,
    DEFAULT_HEADERS,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_LANGUAGE,
    DEFAULT_TIMEOUT,
    DEVICE_MODEL,
    REGION_REDIRECT_CODE,
    SUCCESS_CODE,
)
from .exceptions import (
    FitdaysAPIError,
    FitdaysAuthError,
    FitdaysNetworkError,
    FitdaysValidationError,
)
from .models import Measurement, ScaleDevice, Session, SyncResult, UserProfile
from .signing import hash_password, new_client_id, signed_query

_LOGGER = logging.getLogger(__name__)

TokenUpdatedCallback = Callable[[Session], Union[None, Awaitable[None]]]


class FitdaysClient:
    """Talks to the Fitdays cloud on behalf of one account."""

    def __init__(
        self,
        session: Session,
        *,
        http_session: Optional[aiohttp.ClientSession] = None,
        token_updated: Optional[TokenUpdatedCallback] = None,
        timeout: int = DEFAULT_TIMEOUT,
        device_model: str = DEVICE_MODEL,
    ):
        self.session = session
        self._http = http_session
        self._owns_http = http_session is None
        self._token_updated = token_updated
        self._timeout = timeout
        self._device_model = device_model
        # One client id per client instance, so our traffic looks like one
        # installation rather than a new device on every request.
        self._client_id = new_client_id()
        self._relogin_lock = asyncio.Lock()

        if not session.api_base:
            session.api_base = DEFAULT_API_BASE
        if not session.country:
            session.country = DEFAULT_COUNTRY
        if not session.language:
            session.language = DEFAULT_LANGUAGE

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
        token_updated: Optional[TokenUpdatedCallback] = None,
        country: str = DEFAULT_COUNTRY,
        language: str = DEFAULT_LANGUAGE,
        api_base: str = DEFAULT_API_BASE,
        timeout: int = DEFAULT_TIMEOUT,
        device_model: str = DEVICE_MODEL,
    ) -> "FitdaysClient":
        """
        Authenticate with email + password and return a ready client.

        The plaintext password is hashed immediately and then dropped; only the
        digest is kept on the session.
        """
        if not email or not password:
            raise FitdaysValidationError("email and password are required")

        client = cls(
            Session(
                email=email,
                password_hash=hash_password(password),
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
        session: Union[Session, dict[str, Any]],
        *,
        http_session: Optional[aiohttp.ClientSession] = None,
        token_updated: Optional[TokenUpdatedCallback] = None,
        timeout: int = DEFAULT_TIMEOUT,
        device_model: str = DEVICE_MODEL,
    ) -> "FitdaysClient":
        """Rebuild a client from a stored :class:`Session` (or its dict form)."""
        if isinstance(session, dict):
            session = Session.from_dict(session)
        if not session.token and not session.can_relogin:
            raise FitdaysValidationError(
                "session needs either a token or an email + password_hash"
            )
        return cls(
            session,
            http_session=http_session,
            token_updated=token_updated,
            timeout=timeout,
            device_model=device_model,
        )

    async def __aenter__(self) -> "FitdaysClient":
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
                    authenticated: bool = True) -> dict[str, Any]:
        """POST a signed request and return the decoded envelope."""
        params = signed_query(
            self.session.token if authenticated else "",
            self.session.uid if authenticated else 0,
            client_id=self._client_id,
            country=self.session.country,
            language=self.session.language,
            device_model=self._device_model,
        )
        url = f"{self.session.api_base}/{path}?{urllib.parse.urlencode(params)}"

        try:
            async with self._http_session().post(
                url,
                json=body,
                headers=DEFAULT_HEADERS,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as response:
                if response.status >= 500:
                    raise FitdaysAPIError(
                        "Fitdays cloud returned a server error",
                        status_code=response.status,
                    )
                try:
                    payload = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise FitdaysAPIError(
                        "Fitdays returned a non-JSON response",
                        status_code=response.status,
                    ) from err
                if not isinstance(payload, dict):
                    raise FitdaysAPIError("Unexpected response shape from Fitdays")
                payload.setdefault("_http_status", response.status)
                return payload
        except asyncio.TimeoutError as err:
            raise FitdaysNetworkError(f"Timeout talking to Fitdays: {err}") from err
        except aiohttp.ClientError as err:
            raise FitdaysNetworkError(f"Network error talking to Fitdays: {err}") from err

    @staticmethod
    def _code(payload: dict[str, Any]) -> Optional[int]:
        """Pull the numeric ``code`` out of an envelope."""
        code = payload.get("code")
        try:
            return int(code)
        except (TypeError, ValueError):
            return None

    def _switch_region(self, payload: dict[str, Any]) -> bool:
        """Follow a ``code 302`` region redirect. True when the base changed."""
        data = payload.get("data")
        domain = None
        if isinstance(data, dict):
            domain = data.get("domain")
        domain = domain or payload.get("domain")
        if not domain:
            return False
        if not str(domain).startswith("http"):
            domain = f"https://{domain}"
        new_base = f"{str(domain).rstrip('/')}/api" if not str(domain).endswith("/api") \
            else str(domain)
        if new_base == self.session.api_base:
            return False
        _LOGGER.debug("Fitdays region redirect -> %s", new_base)
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

        if code == REGION_REDIRECT_CODE and self._switch_region(payload):
            payload = await self._post(path, body, authenticated=authenticated)
            code = self._code(payload)

        if code == SUCCESS_CODE:
            data = payload.get("data")
            return data if isinstance(data, dict) else {}

        message = payload.get("msg") or f"Fitdays API error (code {code})"

        if authenticated and allow_relogin and self._looks_like_auth_error(code, payload):
            if self.session.can_relogin:
                _LOGGER.debug("Fitdays token rejected (code %s) — logging in again", code)
                await self._relogin(token_in_use)
                return await self._request(
                    path, body, authenticated=authenticated, allow_relogin=False
                )
            raise FitdaysAuthError(
                "Fitdays token is no longer valid and no credentials are stored "
                "to renew it"
            )

        if self._looks_like_auth_error(code, payload):
            raise FitdaysAuthError(message)

        raise FitdaysAPIError(message, code=code,
                              status_code=payload.get("_http_status"))

    @staticmethod
    def _looks_like_auth_error(code: Optional[int], payload: dict[str, Any]) -> bool:
        """
        Decide whether a non-zero code means "re-authenticate".

        The error catalogue is not published, so alongside the codes we have
        actually observed we fall back to sniffing the message.
        """
        if code in AUTH_ERROR_CODES:
            return True
        message = str(payload.get("msg") or "").lower()
        return any(word in message for word in ("token", "login", "登录", "未登录"))

    async def _relogin(self, stale_token: Optional[str]) -> None:
        """
        Re-authenticate using the stored digest.

        ``stale_token`` is whatever was in use when the call was rejected. If a
        concurrent request already replaced it while we waited for the lock,
        there is nothing left to do.
        """
        async with self._relogin_lock:
            if self.session.token != stale_token:
                return
            await self.async_login()

    # ------------------------------------------------------------------
    # auth
    # ------------------------------------------------------------------

    async def async_login(self) -> Session:
        """
        Log in with the stored email + password digest.

        Updates :attr:`session` in place and fires ``token_updated``.
        """
        if not self.session.can_relogin:
            raise FitdaysValidationError(
                "email and password_hash are required to log in"
            )

        data = await self._request(
            "users/login",
            {"email": self.session.email, "password": self.session.password_hash},
            authenticated=False,
            allow_relogin=False,
        )

        account = data.get("account") or {}
        token = data.get("token") or account.get("token")
        if not token:
            raise FitdaysAuthError("Fitdays login succeeded but returned no token")

        self.session.token = token
        self.session.refresh_token = data.get("refresh_token") or account.get("refresh_token")
        self.session.uid = account.get("uid") or data.get("uid")
        self.session.active_suid = account.get("active_suid") or data.get("active_suid")

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
        await self.get_sync(days=1)
        return True

    # ------------------------------------------------------------------
    # data
    # ------------------------------------------------------------------

    async def get_sync(self, days: int = DEFAULT_HISTORY_DAYS) -> SyncResult:
        """
        Pull measurement history plus the account's profiles and devices.

        ``days`` is how far back to reach. The endpoint takes ``start_time`` as
        *now* and ``end_time`` as the older bound — that inversion is the API's,
        not a bug here.
        """
        if days <= 0:
            raise FitdaysValidationError("days must be positive")
        now = int(time.time())
        data = await self._request(
            "sync/syncFromServer",
            {"start_time": now, "end_time": now - days * 86400},
        )
        result = SyncResult.from_api(data)

        # The account's own active profile is worth remembering for callers
        # that never passed an explicit suid.
        if self.session.active_suid is None and result.profiles:
            self.session.active_suid = result.profiles[0].suid

        return result

    async def get_increments(self, since: int) -> SyncResult:
        """Fetch only what changed since a unix timestamp."""
        data = await self._request("sync/syncIncrements", {"sync_time": int(since)})
        return SyncResult.from_api(data)

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
        result = await self.get_sync(days=1)
        return result.profiles

    async def get_devices(self) -> list[ScaleDevice]:
        """The scales bound to this account."""
        result = await self.get_sync(days=1)
        return result.devices
