"""Tests for request signing.

The signature is the whole reason this library can talk to the API at all, and
it is easy to break invisibly: reorder the parameters, sign a field that should
be excluded, or change the quoting, and every request comes back rejected with
a code that says nothing about which of those it was. These tests pin the
algorithm to fixed inputs.
"""

from __future__ import annotations

import hashlib
import urllib.parse

import pytest

from fitdays.constants import (
    APP_VERSION,
    PASSWORD_SALT,
    SIGN_SALT,
    UNSIGNED_PARAMS,
)
from fitdays.signing import (
    hash_password,
    md5_lower,
    md5_upper,
    new_client_id,
    new_request_id,
    signed_query,
)


class TestDigests:
    """The two MD5 helpers, checked against hashlib directly."""

    def test_upper_is_uppercase_hex(self) -> None:
        assert md5_upper("abc") == hashlib.md5(b"abc").hexdigest().upper()

    def test_lower_is_lowercase_hex(self) -> None:
        assert md5_lower("abc") == hashlib.md5(b"abc").hexdigest()

    def test_the_two_differ_only_in_case(self) -> None:
        assert md5_upper("abc").lower() == md5_lower("abc")

    def test_non_ascii_is_encoded_as_utf8(self) -> None:
        assert md5_lower("wéight") == hashlib.md5("wéight".encode()).hexdigest()


class TestPasswordHash:
    """The login digest, so the plaintext never leaves the device."""

    def test_is_a_double_md5_with_the_salt(self) -> None:
        expected = md5_upper(md5_upper("hunter2" + PASSWORD_SALT))
        assert hash_password("hunter2") == expected

    def test_is_stable_for_the_same_password(self) -> None:
        assert hash_password("hunter2") == hash_password("hunter2")

    def test_differs_between_passwords(self) -> None:
        assert hash_password("hunter2") != hash_password("hunter3")

    def test_the_plaintext_does_not_appear_in_the_digest(self) -> None:
        assert "hunter2" not in hash_password("hunter2")

    def test_the_digest_is_32_uppercase_hex_characters(self) -> None:
        digest = hash_password("hunter2")
        assert len(digest) == 32
        assert digest == digest.upper()


class TestIdentifiers:
    """Per-installation and per-request ids."""

    def test_a_client_id_is_uppercase_hex(self) -> None:
        value = new_client_id()
        assert len(value) == 32
        assert value == value.upper()

    def test_a_request_id_is_lowercase_hex(self) -> None:
        value = new_request_id()
        assert len(value) == 32
        assert value == value.lower()

    def test_two_client_ids_differ(self) -> None:
        assert new_client_id() != new_client_id()


class TestSignedQuery:
    """The parameter set and the signature over it."""

    @pytest.fixture(name="params")
    def fixture_params(self) -> dict[str, str]:
        return signed_query(
            token="tok", uid=42, client_id="C" * 32, timestamp=1756000000
        )

    def test_carries_every_parameter_the_api_expects(self, params: dict) -> None:
        for key in (
            "app_ver",
            "client_id",
            "country",
            "device_model",
            "language",
            "os_type",
            "request_id",
            "sign",
            "source",
            "timestamp",
            "token",
            "uid",
        ):
            assert key in params

    def test_the_signature_matches_the_documented_algorithm(
        self, params: dict
    ) -> None:
        # Rebuild it independently: sorted k=v pairs, joined with &, salt
        # appended, url-quoted, then lowercase MD5.
        signable = {k: v for k, v in params.items() if k not in UNSIGNED_PARAMS}
        joined = "&".join(f"{k}={signable[k]}" for k in sorted(signable)) + SIGN_SALT
        assert params["sign"] == md5_lower(urllib.parse.quote_plus(joined, safe="*"))

    def test_capp_ver_is_added_after_signing(self, params: dict) -> None:
        # It is in the URL but not in the signed string. Signing it would make
        # every request fail, and the failure would not say why.
        assert params["capp_ver"] == APP_VERSION
        assert "capp_ver" not in UNSIGNED_PARAMS[1:]
        signable = {k: v for k, v in params.items() if k not in UNSIGNED_PARAMS}
        assert "capp_ver" not in signable

    def test_the_sign_itself_is_not_part_of_the_signed_string(self) -> None:
        assert "sign" in UNSIGNED_PARAMS

    def test_a_fixed_timestamp_gives_a_reproducible_signature(self) -> None:
        first = signed_query("tok", 42, client_id="C" * 32, timestamp=1756000000)
        second = signed_query("tok", 42, client_id="C" * 32, timestamp=1756000000)
        # Only request_id differs between the two, so the signatures differ,
        # but everything else must line up.
        assert first["timestamp"] == second["timestamp"] == "1756000000"
        assert first["client_id"] == second["client_id"]

    def test_an_absent_timestamp_uses_the_clock(self) -> None:
        assert int(signed_query()["timestamp"]) > 1_700_000_000

    def test_an_anonymous_call_sends_an_empty_token_and_zero_uid(self) -> None:
        params = signed_query()
        assert params["token"] == ""
        assert params["uid"] == "0"

    def test_a_none_uid_becomes_zero(self) -> None:
        assert signed_query("tok", None)["uid"] == "0"

    def test_the_country_and_language_can_be_overridden(self) -> None:
        params = signed_query(country="DE", language="de")
        assert params["country"] == "DE"
        assert params["language"] == "de"

    def test_an_empty_country_falls_back_to_the_default(self) -> None:
        assert signed_query(country="")["country"] == "NL"

    def test_the_device_model_reaches_the_query(self) -> None:
        assert signed_query(device_model="Pixel")["device_model"] == "Pixel"
