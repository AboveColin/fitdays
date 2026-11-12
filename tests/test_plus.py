"""Tests for the Fitdays+ backend.

The client talks to the same loopback fake as the Fitdays tests. Fitdays+ paths
already start with ``/api/``, so its base is the fake's bare URL.
"""

from __future__ import annotations

import asyncio
import time
import urllib.parse

import pytest
from aiohttp import web

from fitdays import (
    FitdaysAPIError,
    FitdaysAuthError,
    FitdaysNetworkError,
    FitdaysPlusClient,
    FitdaysValidationError,
    Measurement,
    PlusSession,
    ScaleDevice,
    SyncResult,
    UserProfile,
)
from fitdays.constants import PLUS_DEFAULT_API_BASE, PLUS_MAX_REGION_REDIRECTS
from fitdays.plus_signing import (
    java_urlencode,
    plus_access_code,
    plus_access_sign,
    plus_headers,
    plus_query,
)
from fitdays.signing import md5_lower

from .conftest import EMAIL, PASSWORD, FakeFitdays

LOGIN = "account/login"
SYNC = "sync/sync_from_server"
INCREMENTS = "sync/sync_increments"


def login_data(token: str = "a" * 32, account_id: int = 7) -> dict:
    """A login ``data`` block shaped like the live response."""
    return {
        "account": {"account_id": account_id, "token": token, "account": EMAIL},
        "users": [{"uid": 11, "account_id": str(account_id), "nickname": "Ada"}],
        "is_del": False,
    }


def weight_row(**overrides: object) -> dict:
    """A ``weights[]`` row with the fields ICAFWeight declares."""
    row: dict[str, object] = {
        "account_id": 7,
        "uid": 11,
        "data_id": "w1",
        "device_id": "d1",
        "measure_time": 1760000000,
        "weight_g": 72500,
        "weight_kg": 72.499998,
        "weight_lb": 159.8,
        "bmi": "22.4",
        "pbf": "18.4",
        "imps": "512.3",
        "electrode": 4,
        "height": 180,
        "ext": (
            '{"body_composition": {"subcutaneousFatPercent": 16.1, "visceralFat": 6,'
            ' "musclePercent": 52.3, "smPercent": 46.0, "boneMass": 3.12,'
            ' "moisturePercent": 58.7, "proteinPercent": 17.9, "bmr": 1688,'
            ' "physicalAge": 31, "hr": 0}}'
        ),
    }
    row.update(overrides)
    return row


@pytest.fixture(name="plus_session")
def fixture_plus_session(api: FakeFitdays) -> PlusSession:
    """A session with a token and the digests needed to renew it."""
    return PlusSession(
        token="token-1",
        account_id="7",
        email=EMAIL,
        access_code=plus_access_code(PASSWORD),
        access_sign=plus_access_sign(PASSWORD),
        client_id="c" * 32,
        api_base=api.url,
    )


@pytest.fixture(name="plus")
def fixture_plus(api: FakeFitdays, http, plus_session: PlusSession) -> FitdaysPlusClient:
    """A client with a stored token, pointed at the fake."""
    return FitdaysPlusClient.from_session(plus_session, http_session=http)


class TestSigning:
    """Header signing and password digests, as the 1.14.4 APK computes them."""

    def test_the_signature_matches_a_known_vector(self) -> None:
        # Computed with the request code that logged in and synced against
        # plus-eu.fitdays.cn on 2026-11-12.
        headers = plus_headers(
            token="t" * 32,
            account_id="7",
            client_id="c" * 32,
            country="NL",
            device_model="HomeAssistant",
            timestamp=1760000000,
            request_id="r" * 32,
            timezone_offset_ms=3600000,
        )
        assert headers["sign"] == "8d6d8e97b77300b06881ea8b9a4b0ef8"

    def test_timezone_and_sign_are_sent_but_not_signed(self) -> None:
        headers = plus_headers(client_id="c", timestamp=1, request_id="r",
                               timezone_offset_ms=0)
        other = plus_headers(client_id="c", timestamp=1, request_id="r",
                             timezone_offset_ms=7200000)
        assert headers["timezone"] == "0"
        assert other["timezone"] == "7200000"
        assert headers["sign"] == other["sign"]

    def test_country_is_signed_but_not_sent_as_a_header(self) -> None:
        nl = plus_headers(client_id="c", timestamp=1, request_id="r", country="NL")
        us = plus_headers(client_id="c", timestamp=1, request_id="r", country="US")
        assert "country" not in nl
        assert nl["sign"] != us["sign"]

    def test_the_url_encoding_follows_java_not_python(self) -> None:
        assert java_urlencode("a b~*/") == "a+b%7E*%2F"

    def test_the_password_digests(self) -> None:
        assert plus_access_code(PASSWORD) == "2f068727a505809a6424f022f702c28c"
        assert plus_access_sign(PASSWORD) == "2f068727a505809a6424f022f702c28c"

    def test_the_access_sign_encodes_the_password_first(self) -> None:
        password = "p w~"
        assert plus_access_sign(password) == md5_lower(
            md5_lower("p+w%7Efitdayspro")
        )
        assert plus_access_sign(password) != plus_access_code(password)

    def test_the_query_parameters(self) -> None:
        assert plus_query("DE", "nl") == {
            "os_type": "2", "bapp_ver": "1.0.0", "country": "DE",
            "language": "nl", "source": "0",
        }


class TestModels:
    """Fitdays+ payloads land in the same models as Fitdays ones."""

    def test_a_weight_row_is_parsed(self) -> None:
        m = Measurement.from_plus_api(weight_row())
        assert m.suid == 11
        assert m.measured_at is not None and int(m.measured_at.timestamp()) == 1760000000
        assert m.weight_kg == 72.5
        assert m.weight_lb == 159.8
        assert m.bmi == 22.4
        assert m.body_fat_pct == 18.4
        assert m.subcutaneous_fat_pct == 16.1
        assert m.visceral_fat == 6
        assert m.muscle_pct == 52.3
        assert m.skeletal_muscle_pct == 46.0
        assert m.bone_mass_kg == 3.12
        assert m.body_water_pct == 58.7
        assert m.protein_pct == 17.9
        assert m.bmr == 1688
        assert m.body_age == 31
        assert m.heart_rate is None
        assert m.impedance == 512.3
        assert m.electrodes == 4
        assert m.height_cm == 180
        assert m.device_id == "d1"
        assert m.data_id == "w1"
        assert m.body_fat_kg == 13.34

    def test_weight_kg_is_the_fallback_when_grams_are_missing(self) -> None:
        assert Measurement.from_plus_api(weight_row(weight_g=0)).weight_kg == 72.5

    def test_a_weight_only_row_has_no_composition(self) -> None:
        m = Measurement.from_plus_api(weight_row(pbf="0", imps="", ext=""))
        assert m.is_weight_only
        assert m.muscle_pct is None

    def test_an_eight_electrode_impedance_list_yields_its_first_value(self) -> None:
        m = Measurement.from_plus_api(weight_row(imps="[501.2,480.0,470.5]", electrode=8))
        assert m.impedance == 501.2
        assert m.raw["imps"] == "[501.2,480.0,470.5]"

    def test_unparsable_ext_is_ignored(self) -> None:
        m = Measurement.from_plus_api(weight_row(ext="{not json"))
        assert m.weight_kg == 72.5
        assert m.muscle_pct is None

    def test_a_profile_is_parsed(self) -> None:
        p = UserProfile.from_plus_api({
            "uid": 11, "account_id": "7", "nickname": "Ada", "sex": 2,
            "birthday": "1990-01-01", "height": 170, "target_weight": 0,
            "people_type": 0, "avatar": "https://example.test/a.png",
        })
        assert (p.suid, p.uid, p.nickname, p.height_cm) == (11, 7, "Ada", 170)
        assert p.target_weight_kg is None
        assert p.photo == "https://example.test/a.png"

    def test_a_device_takes_its_name_from_the_binding_remark(self) -> None:
        d = ScaleDevice.from_plus_api(
            {"device_id": "d1", "name": "Scale", "mac": "AA:BB", "sn": "S1"},
            {"d1": {"device_id": "d1", "remark": "Bathroom"}},
        )
        assert (d.name, d.mac, d.serial, d.model) == ("Bathroom", "AA:BB", "S1", "Scale")

    def test_a_sync_drops_deleted_rows_and_sorts_newest_first(self) -> None:
        result = SyncResult.from_plus_api(
            {
                "weights": [
                    weight_row(data_id="old", measure_time=1700000000),
                    weight_row(data_id="new", measure_time=1760000000),
                    weight_row(data_id="gone", measure_time=1765000000),
                ],
                "weight_delete_list": [{"data_id": "gone"}],
                "devices": [{"device_id": "d1"}],
                "bind_device": [{"device_id": "d1", "remark": "Old", "is_deleted": 1}],
            },
            users=[{"uid": 11}, {"uid": 12, "is_deleted": 1}],
        )
        assert [m.data_id for m in result.measurements] == ["new", "old"]
        assert [p.suid for p in result.profiles] == [11]
        assert result.devices[0].name is None

    def test_users_in_the_payload_win_over_the_argument(self) -> None:
        result = SyncResult.from_plus_api({"users": [{"uid": 1}]}, users=[{"uid": 2}])
        assert [p.suid for p in result.profiles] == [1]

    def test_a_session_survives_a_round_trip(self, plus_session: PlusSession) -> None:
        assert PlusSession.from_dict(plus_session.to_dict()) == plus_session
        assert PlusSession.from_dict({}).can_relogin is False


class TestLogin:
    """Login sends digests, stores the token and fires the callback."""

    async def test_login_sends_digests_and_stores_the_token(
        self, api: FakeFitdays, http
    ) -> None:
        api.envelope(LOGIN, login_data())
        fired: list[PlusSession] = []
        client = await FitdaysPlusClient.login(
            EMAIL, PASSWORD, http_session=http, api_base=api.url,
            token_updated=fired.append,
        )
        assert client.session.token == "a" * 32
        assert client.session.account_id == "7"
        assert fired == [client.session]
        body = api.bodies[-1]
        assert body == {
            "type": 1, "account": EMAIL, "vcode": "",
            "access_code": plus_access_code(PASSWORD),
            "access_sign": plus_access_sign(PASSWORD),
        }
        assert PASSWORD not in str(body)
        assert client.session.to_dict()["access_code"] == plus_access_code(PASSWORD)

    async def test_the_login_request_is_anonymous_and_signed(
        self, api: FakeFitdays, http
    ) -> None:
        api.envelope(LOGIN, login_data())
        await FitdaysPlusClient.login(EMAIL, PASSWORD, http_session=http, api_base=api.url)
        request = api.requests[-1]
        assert request.headers["token"] == ""
        assert request.headers["account-id"] == ""
        assert request.headers["package-name"] == "cn.icomon.fitdayspro"
        assert request.query["os_type"] == "2"
        signed = {k: request.headers[k] for k in (
            "user-agent", "request-id", "client-id", "account-id", "app-ver",
            "device-model", "package-name", "timestamp", "token")}
        signed["country"] = request.query["country"]
        joined = "&".join(f"{k}={signed[k]}" for k in sorted(signed)) + "fitdayspro"
        assert request.headers["sign"] == md5_lower(urllib.parse.quote_plus(joined))

    async def test_login_requires_both_an_email_and_a_password(self, http) -> None:
        with pytest.raises(FitdaysValidationError):
            await FitdaysPlusClient.login(EMAIL, "", http_session=http)

    async def test_a_wrong_password_is_an_auth_error(self, api: FakeFitdays, http) -> None:
        api.error(LOGIN, 11022, "wrong password")
        with pytest.raises(FitdaysAuthError, match="code 11022"):
            await FitdaysPlusClient.login(EMAIL, PASSWORD, http_session=http,
                                          api_base=api.url)

    async def test_a_frozen_account_says_why(self, api: FakeFitdays, http) -> None:
        api.error(LOGIN, 11008, "frozen")
        with pytest.raises(FitdaysAuthError, match="frozen.*migration"):
            await FitdaysPlusClient.login(EMAIL, PASSWORD, http_session=http,
                                          api_base=api.url)

    async def test_a_success_without_a_token_is_an_auth_error(
        self, api: FakeFitdays, http
    ) -> None:
        api.envelope(LOGIN, {"account": {}})
        with pytest.raises(FitdaysAuthError, match="no token"):
            await FitdaysPlusClient.login(EMAIL, PASSWORD, http_session=http,
                                          api_base=api.url)

    async def test_a_failed_login_closes_an_owned_session(self, api: FakeFitdays) -> None:
        api.error(LOGIN, 11022, "wrong password")
        with pytest.raises(FitdaysAuthError):
            await FitdaysPlusClient.login(EMAIL, PASSWORD, api_base=api.url)

    async def test_an_async_callback_is_awaited(self, api: FakeFitdays, http) -> None:
        api.envelope(LOGIN, login_data())
        fired: list[str] = []

        async def callback(session: PlusSession) -> None:
            fired.append(session.token or "")

        await FitdaysPlusClient.login(EMAIL, PASSWORD, http_session=http,
                                      api_base=api.url, token_updated=callback)
        assert fired == ["a" * 32]


class TestConstruction:
    """Building a client from stored state."""

    def test_a_session_needs_a_token_or_credentials(self) -> None:
        with pytest.raises(FitdaysValidationError, match="token or an email"):
            FitdaysPlusClient.from_session(PlusSession())

    async def test_the_defaults_are_filled_in(self, http) -> None:
        client = FitdaysPlusClient.from_session({"token": "t"}, http_session=http)
        assert client.session.api_base == PLUS_DEFAULT_API_BASE
        assert client.session.country == "NL"
        assert len(client.session.client_id or "") == 32

    async def test_a_stored_client_id_is_reused(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.envelope(INCREMENTS, {"users": []})
        await plus.async_verify()
        assert api.requests[-1].headers["client-id"] == "c" * 32

    async def test_logging_in_without_credentials_is_refused(self, http) -> None:
        client = FitdaysPlusClient.from_session({"token": "t"}, http_session=http)
        with pytest.raises(FitdaysValidationError):
            await client.async_login()


class TestRecovery:
    """Expired tokens and region redirects."""

    async def test_code_10000_logs_in_again_and_retries_once(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.envelope(LOGIN, login_data(token="b" * 32))
        api.sequence(INCREMENTS, {"code": 10000, "msg": "token invalid"},
                     {"code": 0, "data": {"users": [{"uid": 11}]}})
        profiles = await plus.get_profiles()
        assert [p.suid for p in profiles] == [11]
        assert plus.session.token == "b" * 32
        assert api.requests[-1].headers["token"] == "b" * 32

    async def test_a_second_rejection_is_not_retried_again(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.envelope(LOGIN, login_data())
        api.error(INCREMENTS, 10000, "token invalid")
        with pytest.raises(FitdaysAuthError, match="right after a fresh login"):
            await plus.get_profiles()
        assert sum(r.path.endswith(LOGIN) for r in api.requests) == 1

    async def test_without_credentials_the_token_error_is_final(
        self, api: FakeFitdays, http
    ) -> None:
        api.error(INCREMENTS, 10000, "token invalid")
        client = FitdaysPlusClient.from_session(
            {"token": "t", "api_base": api.url}, http_session=http
        )
        with pytest.raises(FitdaysAuthError, match="no credentials"):
            await client.async_verify()

    async def test_a_302_moves_the_base_and_retries(self, api: FakeFitdays, http) -> None:
        def login(request: web.Request) -> web.Response:
            if request.url.port == int(api.url.rsplit(":", 1)[1]):
                return web.json_response(
                    {"code": "302", "data": {"domain": api.other_url + "/"}}
                )
            return web.json_response({"code": "0", "data": login_data()})

        api.handle(LOGIN, login)
        client = await FitdaysPlusClient.login(EMAIL, PASSWORD, http_session=http,
                                               api_base=api.url)
        assert client.session.api_base == api.other_url
        assert client.session.token == "a" * 32

    async def test_redirects_stop_after_the_app_limit(self, api: FakeFitdays, http) -> None:
        bases = [api.url, api.other_url]

        def login(request: web.Request) -> web.Response:
            here = f"http://127.0.0.1:{request.url.port}"
            target = bases[1] if here == bases[0] else bases[0]
            return web.json_response({"code": 302, "data": {"domain": target}})

        api.handle(LOGIN, login)
        with pytest.raises(FitdaysAPIError, match=f"more than {PLUS_MAX_REGION_REDIRECTS}"):
            await FitdaysPlusClient.login(EMAIL, PASSWORD, http_session=http,
                                          api_base=api.url)
        assert len(api.requests) == PLUS_MAX_REGION_REDIRECTS + 1

    async def test_a_302_without_a_url_is_reported(self, api: FakeFitdays, http) -> None:
        api.envelope(LOGIN, {"domain": "plus-us.fitdays.cn"}, code=302, msg="moved")
        with pytest.raises(FitdaysAPIError, match="moved"):
            await FitdaysPlusClient.login(EMAIL, PASSWORD, http_session=http,
                                          api_base=api.url)

    async def test_an_unknown_code_is_an_api_error(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.error(INCREMENTS, 500123, "boom")
        with pytest.raises(FitdaysAPIError) as err:
            await plus.async_verify()
        assert err.value.code == 500123

    async def test_a_500_is_an_api_error(self, api: FakeFitdays, plus: FitdaysPlusClient) -> None:
        api.raw(INCREMENTS, "oops", status=502)
        with pytest.raises(FitdaysAPIError) as err:
            await plus.async_verify()
        assert err.value.status_code == 502

    async def test_a_non_json_body_is_an_api_error(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.raw(INCREMENTS, "<html>")
        with pytest.raises(FitdaysAPIError, match="non-JSON"):
            await plus.async_verify()

    async def test_an_unreachable_host_is_a_network_error(self, http) -> None:
        client = FitdaysPlusClient.from_session(
            {"token": "t", "api_base": "http://127.0.0.1:9"}, http_session=http
        )
        with pytest.raises(FitdaysNetworkError):
            await client.async_verify()


class TestData:
    """Sync, increments and the convenience readers."""

    async def test_get_sync_merges_weights_with_profiles_from_increments(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.envelope(SYNC, {"weights": [weight_row()], "devices": [{"device_id": "d1"}]})
        api.envelope(INCREMENTS, {"users": [{"uid": 11, "nickname": "Ada"}]})
        before = int(time.time())
        result = await plus.get_sync(days=10)
        assert [m.data_id for m in result.measurements] == ["w1"]
        assert [p.display_name for p in result.profiles] == ["Ada"]
        assert [d.device_id for d in result.devices] == ["d1"]
        sync_body = api.bodies[0]
        assert sync_body["end_time"] - sync_body["start_time"] == 10 * 86400
        assert sync_body["end_time"] >= before
        assert sync_body["count"] == 0
        assert api.requests[0].headers["token"] == "token-1"
        assert api.requests[0].headers["account-id"] == "7"

    async def test_a_non_positive_window_is_refused(self, plus: FitdaysPlusClient) -> None:
        with pytest.raises(FitdaysValidationError):
            await plus.get_sync(days=0)

    async def test_get_increments_sends_the_sync_time(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.envelope(INCREMENTS, {"weights": [weight_row()], "users": [{"uid": 11}]})
        result = await plus.get_increments(1700000000)
        assert api.bodies[-1] == {"sync_time": 1700000000, "from_data_migration": False}
        assert len(result.measurements) == 1 and len(result.profiles) == 1

    async def test_measurements_latest_and_devices(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.envelope(SYNC, {
            "weights": [weight_row(data_id="a", uid=11, measure_time=1),
                        weight_row(data_id="b", uid=12, measure_time=2)],
            "devices": [{"device_id": "d1", "name": "Scale"}],
        })
        api.envelope(INCREMENTS, {"users": []})
        assert [m.data_id for m in await plus.get_measurements(suid=11)] == ["a"]
        latest = await plus.get_latest()
        assert latest is not None and latest.data_id == "b"
        assert [d.name for d in await plus.get_devices()] == ["Scale"]

    async def test_an_owned_session_is_closed(self, api: FakeFitdays) -> None:
        api.envelope(LOGIN, login_data())
        async with await FitdaysPlusClient.login(EMAIL, PASSWORD, api_base=api.url) as client:
            assert client.session.token
        assert client._http is None  # pylint: disable=protected-access


class TestEdgeCases:
    """Envelope shapes and timing the happy path does not reach."""

    async def test_a_json_list_where_an_envelope_belongs_is_an_api_error(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.raw(INCREMENTS, "[]")
        with pytest.raises(FitdaysAPIError, match="Unexpected response shape"):
            await plus.async_verify()

    async def test_a_missing_code_is_an_api_error(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.raw(INCREMENTS, '{"msg": "no code"}')
        with pytest.raises(FitdaysAPIError, match="no code"):
            await plus.async_verify()

    async def test_a_redirect_to_the_current_base_is_not_followed(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.envelope(INCREMENTS, {"domain": api.url}, code=302, msg="moved")
        with pytest.raises(FitdaysAPIError, match="moved"):
            await plus.async_verify()
        assert len(api.requests) == 1

    async def test_a_timeout_is_a_network_error(self, api: FakeFitdays, http) -> None:
        async def slow(_request: web.Request) -> web.Response:
            await asyncio.sleep(2)
            return web.json_response({"code": 0})

        api.handle(INCREMENTS, slow)
        client = FitdaysPlusClient.from_session(
            {"token": "t", "api_base": api.url}, http_session=http, timeout=0.1
        )
        with pytest.raises(FitdaysNetworkError, match="Timeout"):
            await client.async_verify()

    async def test_concurrent_rejections_share_one_login(
        self, api: FakeFitdays, plus: FitdaysPlusClient
    ) -> None:
        api.envelope(LOGIN, login_data(token="b" * 32))

        def increments(request: web.Request) -> web.Response:
            if request.headers["token"] == "b" * 32:
                return web.json_response({"code": 0, "data": {"users": []}})
            return web.json_response({"code": 10000, "msg": "token invalid"})

        api.handle(INCREMENTS, increments)
        await asyncio.gather(plus.async_verify(), plus.async_verify())
        assert sum(r.path.endswith(LOGIN) for r in api.requests) == 1
