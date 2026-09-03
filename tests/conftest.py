"""Shared fixtures for the Fitdays client tests.

The client sends every request to whatever ``session.api_base`` holds, so the
tests point that at a real aiohttp server on a loopback port rather than
mocking aiohttp. Mocking libraries for aiohttp lag its releases and break the
suite on an unrelated bump; a real server does not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from aiohttp import ClientSession, web

from fitdays import FitdaysClient, Session
from fitdays.signing import hash_password

EMAIL = "member@example.test"
PASSWORD = "hunter2"


class FakeFitdays:
    """A loopback server standing in for the Fitdays cloud.

    Every endpoint lives under ``/api/``. ``requests`` records the requests and
    ``bodies`` their decoded JSON, so a test can assert on the signed query
    string and the posted body.
    """

    def __init__(self) -> None:
        self.app = web.Application()
        self.requests: list[web.Request] = []
        self.bodies: list[dict] = []
        self._routes: dict[str, Callable[[web.Request], web.StreamResponse]] = {}
        self.app.router.add_route("*", "/{tail:.*}", self._dispatch)
        self.url = ""
        # A second port serving the same handlers. A region redirect has to
        # move to a genuinely different base before the client follows it, and
        # pointing that at a real Fitdays host would send a login to
        # production.
        self.other_url = ""

    @property
    def api_base(self) -> str:
        """What belongs in ``session.api_base``."""
        return f"{self.url}/api"

    @property
    def other_api_base(self) -> str:
        """The same fake, reached on its second port."""
        return f"{self.other_url}/api"

    def handle(self, path: str, handler: Callable[[web.Request], web.StreamResponse]) -> None:
        """Answer the API path ``path`` with ``handler``."""
        self._routes[f"/api/{path}"] = handler

    def envelope(self, path: str, data: object, code: int = 0, msg: str = "ok") -> None:
        """Answer ``path`` with a success envelope carrying ``data``."""
        self.handle(
            path, lambda _r: web.json_response({"code": code, "msg": msg, "data": data})
        )

    def error(self, path: str, code: int, msg: str, status: int = 200) -> None:
        """Answer ``path`` with a non-zero envelope code."""
        self.handle(
            path, lambda _r: web.json_response({"code": code, "msg": msg}, status=status)
        )

    def raw(self, path: str, body: str, status: int = 200) -> None:
        """Answer ``path`` with a body that is not necessarily JSON."""
        self.handle(path, lambda _r: web.Response(text=body, status=status))

    def sequence(self, path: str, *payloads: dict) -> None:
        """Answer ``path`` with each envelope in turn, repeating the last."""
        remaining = list(payloads)

        def handler(_request: web.Request) -> web.StreamResponse:
            item = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            return web.json_response(item)

        self.handle(path, handler)

    async def _dispatch(self, request: web.Request) -> web.StreamResponse:
        self.requests.append(request)
        try:
            self.bodies.append(await request.json())
        except Exception:  # noqa: BLE001  a non-JSON body is a valid case
            self.bodies.append({})
        handler = self._routes.get(request.path)
        if handler is None:
            return web.json_response({"code": 404, "msg": "no route"}, status=404)
        result = handler(request)
        return await result if hasattr(result, "__await__") else result


@pytest_asyncio.fixture
async def api() -> AsyncIterator[FakeFitdays]:
    """A running fake Fitdays cloud, with its address filled in."""
    fake = FakeFitdays()
    runner = web.AppRunner(fake.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    other = web.TCPSite(runner, "127.0.0.1", 0)
    await other.start()
    fake.url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    fake.other_url = f"http://127.0.0.1:{runner.addresses[1][1]}"
    try:
        yield fake
    finally:
        await runner.cleanup()


@pytest_asyncio.fixture
async def http() -> AsyncIterator[ClientSession]:
    """A session the test owns, so the client must leave it open."""
    async with ClientSession() as open_session:
        yield open_session


@pytest.fixture(name="stored_session")
def fixture_stored_session(api: FakeFitdays) -> Session:
    """A session with a token and the credentials needed to renew it."""
    return Session(
        token="token-1",
        uid=42,
        email=EMAIL,
        password_hash=hash_password(PASSWORD),
        api_base=api.api_base,
    )


@pytest_asyncio.fixture
async def client(
    api: FakeFitdays, http: ClientSession, stored_session: Session
) -> AsyncIterator[FitdaysClient]:
    """A logged-in client pointed at the fake."""
    instance = FitdaysClient.from_session(stored_session, http_session=http)
    yield instance
    await instance.aclose_if_owned()


def sync_payload(**overrides: object) -> dict:
    """A syncFromServer data block with one profile, one device, one weigh-in."""
    payload = {
        "users": [{"suid": 1, "uid": 42, "nickname": "Colin", "height": 183}],
        "devices": [{"device_id": "d1", "name": "Robi S6", "mac": "AA:BB"}],
        "bind_device": [{"device_id": "d1", "remark_name": "Badkamer"}],
        "weight_list": [
            {
                "suid": 1,
                "measured_time": 1756000000,
                "weight_g": 72500,
                "bfr": 18.4,
                "device_id": "d1",
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to resolve a name outside loopback.

    This is a tripwire, not a limit: every test here talks to a fake on
    127.0.0.1, so correct tests never notice it exists. It is here because an
    earlier version of the region-redirect tests pointed the client at the real
    regional host, and the suite quietly posted a login to the production
    Fitdays cloud on every run.

    The guard sits on getaddrinfo rather than on connect, because that is where
    every outbound connection starts and it is the last point at which the
    hostname is still readable. Guarding the socket instead reports an empty
    address list, which says nothing about what went wrong.
    """
    import socket

    allowed = {"127.0.0.1", "::1", "localhost", ""}
    real_getaddrinfo = socket.getaddrinfo

    def guarded(host, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if host is not None and str(host) not in allowed:
            raise AssertionError(
                f"a test tried to reach {host!r}. Tests must only talk to the "
                "local fake: point the client at the api fixture, not a real "
                "host."
            )
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
