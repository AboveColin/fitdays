"""Tests for the client's two awkward behaviours.

Region redirects: an account registered outside Europe answers code 302 with
the host it really lives on, and the client has to follow that once.

Self-healing login: tokens expire, and when one is refused the client logs in
again from the stored digest and retries. Getting the retry cap wrong turns a
dead password into an infinite login loop.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from fitdays import (
    FitdaysAPIError,
    FitdaysAuthError,
    FitdaysClient,
    FitdaysNetworkError,
    FitdaysValidationError,
    Session,
)
from fitdays.constants import DEFAULT_API_BASE, SUCCESS_CODE
from fitdays.signing import hash_password

from .conftest import EMAIL, PASSWORD, FakeFitdays, sync_payload

LOGIN = "users/login"
SYNC = "sync/syncFromServer"
INCREMENTS = "sync/syncIncrements"


class TestConstruction:
    """Building a client, with or without stored state."""

    async def test_a_session_needs_a_token_or_credentials(self, api: FakeFitdays) -> None:
        with pytest.raises(FitdaysValidationError, match="token or an email"):
            FitdaysClient.from_session(Session())

    async def test_a_token_alone_is_enough(self, api: FakeFitdays, http) -> None:
        client = FitdaysClient.from_session(
            Session(token="t", api_base=api.api_base), http_session=http
        )
        assert client.session.token == "t"

    async def test_credentials_alone_are_enough(self, api: FakeFitdays, http) -> None:
        client = FitdaysClient.from_session(
            Session(email=EMAIL, password_hash="D", api_base=api.api_base), http_session=http
        )
        assert client.session.can_relogin is True

    async def test_a_stored_dict_is_accepted(self, api: FakeFitdays, http) -> None:
        client = FitdaysClient.from_session(
            {"token": "t", "api_base": api.api_base}, http_session=http
        )
        assert client.session.token == "t"

    async def test_the_defaults_are_filled_in(self, api: FakeFitdays, http) -> None:
        client = FitdaysClient.from_session(Session(token="t"), http_session=http)
        assert client.session.api_base == DEFAULT_API_BASE
        assert client.session.country == "NL"
        assert client.session.language == "en"

    async def test_login_requires_both_an_email_and_a_password(
        self, api: FakeFitdays, http
    ) -> None:
        with pytest.raises(FitdaysValidationError, match="email and password"):
            await FitdaysClient.login("", "x", http_session=http)


class TestLogin:
    """The login round trip, and what it stores."""

    async def test_the_password_is_sent_only_as_a_digest(
        self, api: FakeFitdays, http
    ) -> None:
        api.envelope(LOGIN, {"token": "t1", "account": {"uid": 42, "active_suid": 1}})
        await FitdaysClient.login(EMAIL, PASSWORD, http_session=http, api_base=api.api_base)
        body = api.bodies[-1]
        assert body["password"] == hash_password(PASSWORD)
        assert PASSWORD not in str(body)

    async def test_the_token_and_ids_are_stored(self, api: FakeFitdays, http) -> None:
        api.envelope(LOGIN, {"token": "t1", "account": {"uid": 42, "active_suid": 7}})
        client = await FitdaysClient.login(
            EMAIL, PASSWORD, http_session=http, api_base=api.api_base
        )
        assert client.session.token == "t1"
        assert client.session.uid == 42
        assert client.session.active_suid == 7

    async def test_a_token_nested_in_the_account_is_found(
        self, api: FakeFitdays, http
    ) -> None:
        api.envelope(LOGIN, {"account": {"token": "t1", "uid": 42}})
        client = await FitdaysClient.login(
            EMAIL, PASSWORD, http_session=http, api_base=api.api_base
        )
        assert client.session.token == "t1"

    async def test_a_success_without_a_token_is_an_auth_error(
        self, api: FakeFitdays, http
    ) -> None:
        api.envelope(LOGIN, {"account": {"uid": 42}})
        with pytest.raises(FitdaysAuthError, match="no token"):
            await FitdaysClient.login(EMAIL, PASSWORD, http_session=http, api_base=api.api_base)

    async def test_the_login_request_is_anonymous(self, api: FakeFitdays, http) -> None:
        # Signing a login with the token it is trying to obtain makes no sense.
        api.envelope(LOGIN, {"token": "t1"})
        await FitdaysClient.login(EMAIL, PASSWORD, http_session=http, api_base=api.api_base)
        assert api.requests[-1].query["token"] == ""
        assert api.requests[-1].query["uid"] == "0"

    async def test_a_failed_login_closes_an_owned_session(self, api: FakeFitdays) -> None:
        api.error(LOGIN, 1001, "wrong password")
        with pytest.raises(FitdaysAPIError):
            await FitdaysClient.login(EMAIL, PASSWORD, api_base=api.api_base)

    async def test_logging_in_without_credentials_is_refused(
        self, api: FakeFitdays, http
    ) -> None:
        client = FitdaysClient.from_session(
            Session(token="t", api_base=api.api_base), http_session=http
        )
        with pytest.raises(FitdaysValidationError, match="required to log in"):
            await client.async_login()


class TestTokenCallback:
    """The caller has to be told when the session changes."""

    async def test_a_plain_callback_fires_on_login(self, api: FakeFitdays, http) -> None:
        seen: list[Session] = []
        api.envelope(LOGIN, {"token": "t1"})
        await FitdaysClient.login(
            EMAIL, PASSWORD, http_session=http, api_base=api.api_base, token_updated=seen.append
        )
        assert [s.token for s in seen] == ["t1"]

    async def test_an_async_callback_is_awaited(self, api: FakeFitdays, http) -> None:
        seen: list[Session] = []

        async def remember(session: Session) -> None:
            seen.append(session)

        api.envelope(LOGIN, {"token": "t1"})
        await FitdaysClient.login(
            EMAIL, PASSWORD, http_session=http, api_base=api.api_base, token_updated=remember
        )
        assert [s.token for s in seen] == ["t1"]

    async def test_no_callback_is_not_an_error(self, api: FakeFitdays, http) -> None:
        api.envelope(LOGIN, {"token": "t1"})
        await FitdaysClient.login(EMAIL, PASSWORD, http_session=http, api_base=api.api_base)


class TestRegionRedirect:
    """An account outside Europe lives on a different host.

    The string handling is tested directly on _switch_region, with no request
    at all. Driving it through a real call would need a real regional host,
    and that means posting a login to the production Fitdays cloud.
    """

    def test_a_bare_hostname_gets_a_scheme_and_the_api_suffix(
        self, client: FitdaysClient
    ) -> None:
        assert client._switch_region({"data": {"domain": "online-us.fitdays.cn"}}) is True
        assert client.session.api_base == "https://online-us.fitdays.cn/api"

    def test_a_full_url_keeps_its_scheme(self, client: FitdaysClient) -> None:
        assert client._switch_region({"data": {"domain": "http://online-us.fitdays.cn"}}) is True
        assert client.session.api_base == "http://online-us.fitdays.cn/api"

    def test_a_domain_that_already_ends_in_api_is_not_doubled(
        self, client: FitdaysClient
    ) -> None:
        payload = {"data": {"domain": "https://online-us.fitdays.cn/api"}}
        assert client._switch_region(payload) is True
        assert client.session.api_base == "https://online-us.fitdays.cn/api"

    def test_a_trailing_slash_is_stripped(self, client: FitdaysClient) -> None:
        assert client._switch_region({"data": {"domain": "https://online-us.fitdays.cn/"}}) is True
        assert client.session.api_base == "https://online-us.fitdays.cn/api"

    def test_a_domain_at_the_top_level_is_also_read(self, client: FitdaysClient) -> None:
        assert client._switch_region({"domain": "https://online-us.fitdays.cn"}) is True
        assert client.session.api_base == "https://online-us.fitdays.cn/api"

    def test_no_domain_means_no_switch(self, client: FitdaysClient) -> None:
        assert client._switch_region({"code": 302}) is False

    def test_a_redirect_to_the_current_base_is_not_followed(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        # Following it would be an infinite redirect.
        assert client._switch_region({"data": {"domain": api.url}}) is False

    async def test_a_302_moves_the_base_and_retries_the_call(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        # The redirect points at the fake's second port, so the retry is served
        # locally and nothing leaves the machine.
        api.sequence(
            SYNC,
            {"code": 302, "msg": "wrong region", "data": {"domain": api.other_url}},
            {"code": SUCCESS_CODE, "msg": "ok", "data": sync_payload()},
        )
        result = await client.get_sync()
        assert len(result.measurements) == 1
        assert client.session.api_base == api.other_api_base

    async def test_a_302_with_no_domain_is_reported_rather_than_looping(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.error(SYNC, 302, "wrong region")
        with pytest.raises(FitdaysAPIError, match="wrong region"):
            await client.get_sync()


class TestSelfHealingLogin:
    """A refused token is renewed once, not forever."""

    @pytest.mark.parametrize("code", [401, 1002, 1003, 10001])
    async def test_a_known_auth_code_triggers_one_relogin_and_retry(
        self, api: FakeFitdays, client: FitdaysClient, code: int
    ) -> None:
        api.sequence(
            SYNC,
            {"code": code, "msg": "token expired"},
            {"code": SUCCESS_CODE, "data": sync_payload()},
        )
        api.envelope(LOGIN, {"token": "token-2", "account": {"uid": 42}})
        result = await client.get_sync()
        assert len(result.measurements) == 1
        assert client.session.token == "token-2"

    async def test_an_unknown_code_whose_message_mentions_a_token_also_triggers_it(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        # The error catalogue is not published, so the message is the fallback.
        api.sequence(
            SYNC,
            {"code": 9999, "msg": "invalid token"},
            {"code": SUCCESS_CODE, "data": sync_payload()},
        )
        api.envelope(LOGIN, {"token": "token-2"})
        await client.get_sync()
        assert client.session.token == "token-2"

    async def test_a_second_rejection_is_not_retried_again(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        # Without the cap, a dead password becomes an infinite login loop.
        api.error(SYNC, 401, "token expired")
        api.envelope(LOGIN, {"token": "token-2"})
        with pytest.raises(FitdaysAuthError, match="token expired"):
            await client.get_sync()
        assert [r.path for r in api.requests].count(f"/api/{SYNC}") == 2

    async def test_without_credentials_the_user_is_told_to_sign_in(
        self, api: FakeFitdays, http
    ) -> None:
        client = FitdaysClient.from_session(
            Session(token="dead", api_base=api.api_base), http_session=http
        )
        api.error(SYNC, 401, "token expired")
        with pytest.raises(FitdaysAuthError, match="no credentials are stored"):
            await client.get_sync()

    async def test_a_non_auth_error_is_not_retried(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.error(SYNC, 5000, "server busy")
        with pytest.raises(FitdaysAPIError, match="server busy"):
            await client.get_sync()
        assert [r.path for r in api.requests].count(f"/api/{SYNC}") == 1


class TestTransportErrors:
    """Everything below the envelope."""

    async def test_a_500_is_an_api_error_with_its_status(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.raw(SYNC, "upstream down", status=503)
        with pytest.raises(FitdaysAPIError, match="server error") as caught:
            await client.get_sync()
        assert caught.value.status_code == 503

    async def test_a_non_json_body_is_an_api_error(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.raw(SYNC, "<html>portal</html>")
        with pytest.raises(FitdaysAPIError, match="non-JSON"):
            await client.get_sync()

    async def test_a_json_list_where_an_envelope_belongs_is_an_api_error(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.handle(SYNC, lambda _r: web.json_response([1, 2, 3]))
        with pytest.raises(FitdaysAPIError, match="Unexpected response shape"):
            await client.get_sync()

    async def test_an_unreachable_host_is_a_network_error(self, http) -> None:
        client = FitdaysClient.from_session(
            Session(token="t", api_base="http://127.0.0.1:1/api"), http_session=http
        )
        with pytest.raises(FitdaysNetworkError, match="Network error"):
            await client.get_sync()

    def test_the_error_string_carries_both_codes(self) -> None:
        assert "code 401" in str(FitdaysAPIError("nope", code=401, status_code=200))
        assert "HTTP 200" in str(FitdaysAPIError("nope", code=401, status_code=200))

    def test_a_bare_error_renders_the_message_alone(self) -> None:
        assert str(FitdaysAPIError("nope")) == "nope"


class TestDataEndpoints:
    """The reads built on top of one sync call."""

    async def test_get_sync_parses_everything(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.envelope(SYNC, sync_payload())
        result = await client.get_sync()
        assert len(result.measurements) == 1
        assert result.profiles[0].nickname == "Colin"
        assert result.devices[0].name == "Badkamer"

    async def test_the_time_window_is_inverted_as_the_api_expects(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        # start_time is now and end_time is the older bound. That inversion is
        # the API's, and swapping it back returns nothing.
        api.envelope(SYNC, sync_payload())
        await client.get_sync(days=10)
        body = api.bodies[-1]
        assert body["start_time"] > body["end_time"]
        assert body["start_time"] - body["end_time"] == 10 * 86400

    async def test_a_non_positive_window_is_refused(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        with pytest.raises(FitdaysValidationError, match="days must be positive"):
            await client.get_sync(days=0)

    async def test_the_active_profile_is_remembered_from_the_first_sync(
        self, api: FakeFitdays, http
    ) -> None:
        client = FitdaysClient.from_session(
            Session(token="t", api_base=api.api_base), http_session=http
        )
        api.envelope(SYNC, sync_payload())
        await client.get_sync()
        assert client.session.active_suid == 1

    async def test_an_existing_active_profile_is_not_overwritten(
        self, api: FakeFitdays, http
    ) -> None:
        client = FitdaysClient.from_session(
            Session(token="t", active_suid=9, api_base=api.api_base), http_session=http
        )
        api.envelope(SYNC, sync_payload())
        await client.get_sync()
        assert client.session.active_suid == 9

    async def test_get_measurements_filters_by_profile(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.envelope(
            SYNC,
            sync_payload(
                weight_list=[
                    {"suid": 1, "measured_time": 2000, "weight_g": 72500},
                    {"suid": 2, "measured_time": 1000, "weight_g": 90000},
                ]
            ),
        )
        assert len(await client.get_measurements(suid=2)) == 1

    async def test_get_latest_returns_the_newest(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.envelope(SYNC, sync_payload())
        latest = await client.get_latest()
        assert latest is not None
        assert latest.weight_kg == 72.5

    async def test_get_profiles_and_devices_read_one_day(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.envelope(SYNC, sync_payload())
        assert len(await client.get_profiles()) == 1
        assert len(await client.get_devices()) == 1
        body = api.bodies[-1]
        assert body["start_time"] - body["end_time"] == 86400

    async def test_get_increments_sends_the_sync_time(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.envelope(INCREMENTS, sync_payload())
        await client.get_increments(1756000000)
        assert api.bodies[-1] == {"sync_time": 1756000000}

    async def test_verify_proves_the_token_still_works(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.envelope(SYNC, sync_payload())
        assert await client.async_verify() is True

    async def test_a_success_envelope_with_a_non_object_data_yields_empty(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.envelope(SYNC, [1, 2, 3])
        result = await client.get_sync()
        assert result.measurements == []


class TestSessionOwnership:
    """The client closes only what it created."""

    async def test_a_borrowed_session_survives_close(
        self, api: FakeFitdays, http
    ) -> None:
        client = FitdaysClient.from_session(
            Session(token="t", api_base=api.api_base), http_session=http
        )
        await client.aclose_if_owned()
        assert http.closed is False

    async def test_an_owned_session_is_closed(self, api: FakeFitdays) -> None:
        client = FitdaysClient.from_session(Session(token="t", api_base=api.api_base))
        api.envelope(SYNC, sync_payload())
        await client.get_sync()
        owned = client._http  # noqa: SLF001  ownership is the thing under test
        assert owned is not None
        await client.aclose_if_owned()
        assert owned.closed is True

    async def test_the_context_manager_closes_an_owned_session(
        self, api: FakeFitdays
    ) -> None:
        api.envelope(SYNC, sync_payload())
        async with FitdaysClient.from_session(
            Session(token="t", api_base=api.api_base)
        ) as client:
            await client.get_sync()
            owned = client._http  # noqa: SLF001
        assert owned is not None
        assert owned.closed is True

    async def test_a_closed_session_is_replaced_on_the_next_call(
        self, api: FakeFitdays
    ) -> None:
        client = FitdaysClient.from_session(Session(token="t", api_base=api.api_base))
        api.envelope(SYNC, sync_payload())
        await client.get_sync()
        await client.aclose_if_owned()
        await client.get_sync()
        await client.aclose_if_owned()


class TestRemainingSurface:
    """The paths the cases above did not reach."""

    async def test_a_slow_server_is_a_network_error(
        self, api: FakeFitdays, http
    ) -> None:
        import asyncio

        async def slow(_request: web.Request) -> web.StreamResponse:
            await asyncio.sleep(5)
            return web.json_response({})

        api.handle(SYNC, slow)
        client = FitdaysClient.from_session(
            Session(token="t", api_base=api.api_base), http_session=http, timeout=1
        )
        with pytest.raises(FitdaysNetworkError, match="Timeout"):
            await client.get_sync()

    async def test_a_non_numeric_envelope_code_is_treated_as_unknown(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        api.handle(SYNC, lambda _r: web.json_response({"code": "oops", "msg": "broken"}))
        with pytest.raises(FitdaysAPIError, match="broken") as caught:
            await client.get_sync()
        assert caught.value.code is None

    async def test_a_relogin_that_another_call_already_did_is_skipped(
        self, api: FakeFitdays, client: FitdaysClient
    ) -> None:
        # Two concurrent calls both get a 401. The first logs in; the second
        # must notice the token already moved and not log in a second time.
        api.sequence(
            SYNC,
            {"code": 401, "msg": "token expired"},
            {"code": SUCCESS_CODE, "data": sync_payload()},
        )
        api.envelope(LOGIN, {"token": "token-2"})
        await client.get_sync()
        await client._relogin("token-1")
        assert [r.path for r in api.requests].count(f"/api/{LOGIN}") == 1
