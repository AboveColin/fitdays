"""Tests for the typed models.

The scale reports absent optional metrics as 0, and weights as floats that have
been through a lossy round trip (62.499998092651 for 62.5). Both would produce
plausible-looking wrong numbers on a dashboard, so both get cases here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fitdays import Measurement, ScaleDevice, Session, SyncResult, UserProfile


class TestSession:
    """Stored credentials have to survive a round trip."""

    def test_a_round_trip_through_a_dict_preserves_everything(self) -> None:
        original = Session(
            token="t",
            refresh_token="r",
            uid=1,
            active_suid=2,
            email="a@b.test",
            password_hash="D" * 32,
            api_base="https://x.test/api",
            country="NL",
            language="en",
        )
        assert Session.from_dict(original.to_dict()) == original

    def test_an_empty_dict_yields_an_empty_session(self) -> None:
        assert Session.from_dict({}).token is None

    def test_a_none_dict_does_not_raise(self) -> None:
        assert Session.from_dict(None).token is None

    def test_relogin_needs_both_an_email_and_a_digest(self) -> None:
        assert Session(email="a@b.test", password_hash="D").can_relogin is True
        assert Session(email="a@b.test").can_relogin is False
        assert Session(password_hash="D").can_relogin is False


class TestUserProfile:
    """Member profiles on one account."""

    def test_reads_a_profile(self) -> None:
        profile = UserProfile.from_api(
            {"suid": 3, "uid": 42, "nickname": "Colin", "height": 183}
        )
        assert profile.suid == 3
        assert profile.nickname == "Colin"
        assert profile.height_cm == 183

    def test_a_zero_target_weight_means_none_was_set(self) -> None:
        assert UserProfile.from_api({"target_weight": 0}).target_weight_kg is None

    def test_the_display_name_falls_back_to_the_profile_id(self) -> None:
        assert UserProfile.from_api({"suid": 3}).display_name == "Profile 3"

    def test_the_display_name_prefers_the_nickname(self) -> None:
        assert UserProfile.from_api({"suid": 3, "nickname": "Colin"}).display_name == "Colin"

    def test_an_empty_payload_does_not_raise(self) -> None:
        assert UserProfile.from_api({}).suid is None


class TestScaleDevice:
    """Devices, whose user-chosen name lives in a separate binding list."""

    def test_the_binding_rename_wins_over_the_factory_name(self) -> None:
        device = ScaleDevice.from_api(
            {"device_id": "d1", "name": "Robi S6"},
            {"d1": {"remark_name": "Badkamer"}},
        )
        assert device.name == "Badkamer"

    def test_without_a_binding_the_factory_name_is_used(self) -> None:
        assert ScaleDevice.from_api({"device_id": "d1", "name": "Robi S6"}).name == "Robi S6"

    def test_ext_data_arrives_as_a_json_string(self) -> None:
        device = ScaleDevice.from_api(
            {"device_id": "d1", "ext_data": json.dumps({"measureHeart": 1, "offlineMeasure": 1})}
        )
        assert device.measures_heart_rate is True
        assert device.offline_measure is True

    def test_ext_data_is_also_accepted_as_a_dict(self) -> None:
        device = ScaleDevice.from_api({"device_id": "d1", "ext_data": {"measureHeart": 1}})
        assert device.measures_heart_rate is True

    def test_unparsable_ext_data_is_ignored(self) -> None:
        device = ScaleDevice.from_api({"device_id": "d1", "ext_data": "{not json"})
        assert device.measures_heart_rate is False

    def test_absent_ext_data_is_ignored(self) -> None:
        assert ScaleDevice.from_api({"device_id": "d1"}).measures_heart_rate is False


class TestMeasurementWeight:
    """Weight comes from two fields, and one of them is lossy."""

    def test_the_integer_gram_field_is_preferred(self) -> None:
        # weight_kg for the same reading is 62.499998092651.
        row = {"weight_g": 62500, "weight_kg": 62.499998092651}
        assert Measurement.from_api(row).weight_kg == 62.5

    def test_the_float_field_is_used_when_grams_are_absent(self) -> None:
        assert Measurement.from_api({"weight_kg": 62.499998092651}).weight_kg == 62.5

    def test_a_zero_gram_reading_falls_back_to_the_float(self) -> None:
        assert Measurement.from_api({"weight_g": 0, "weight_kg": 62.5}).weight_kg == 62.5

    def test_no_weight_at_all_is_none(self) -> None:
        assert Measurement.from_api({}).weight_kg is None


class TestMeasurementZeroes:
    """Zero means "not measured", not a reading of zero."""

    def test_a_zero_body_fat_is_an_absence(self) -> None:
        # 0% body fat is not a plausible reading; it is what the scale sends
        # when it could not measure impedance.
        assert Measurement.from_api({"weight_g": 72500, "bfr": 0}).body_fat_pct is None

    def test_a_zero_heart_rate_is_an_absence(self) -> None:
        assert Measurement.from_api({"hr": 0}).heart_rate is None

    def test_a_zero_bmr_is_an_absence(self) -> None:
        assert Measurement.from_api({"bmr": 0}).bmr is None

    def test_a_real_reading_survives(self) -> None:
        assert Measurement.from_api({"bfr": 18.44}).body_fat_pct == 18.4

    def test_a_weight_only_weigh_in_is_flagged(self) -> None:
        assert Measurement.from_api({"weight_g": 72500}).is_weight_only is True

    def test_a_full_weigh_in_is_not_weight_only(self) -> None:
        row = {"weight_g": 72500, "bfr": 18.4, "adc": 520}
        assert Measurement.from_api(row).is_weight_only is False


class TestMeasurementDerivedMasses:
    """The API sends ratios; kilograms are computed here."""

    def test_fat_mass_is_the_percentage_of_the_weight(self) -> None:
        row = {"weight_g": 80000, "bfr": 25}
        assert Measurement.from_api(row).body_fat_kg == 20.0

    def test_muscle_mass_is_computed_the_same_way(self) -> None:
        row = {"weight_g": 80000, "rom": 40}
        assert Measurement.from_api(row).muscle_mass_kg == 32.0

    def test_water_and_protein_are_computed_too(self) -> None:
        row = {"weight_g": 80000, "vwc": 55, "pp": 18}
        measurement = Measurement.from_api(row)
        assert measurement.body_water_kg == 44.0
        assert measurement.protein_kg == 14.4

    def test_no_percentage_means_no_mass(self) -> None:
        assert Measurement.from_api({"weight_g": 80000}).body_fat_kg is None

    def test_no_weight_means_no_mass_either(self) -> None:
        assert Measurement.from_api({"bfr": 25}).body_fat_kg is None


class TestMeasurementTimestamps:
    """Unix seconds become aware datetimes, and 0 is an absence."""

    def test_a_timestamp_becomes_an_aware_datetime(self) -> None:
        measured = Measurement.from_api({"measured_time": 1756000000}).measured_at
        assert measured == datetime.fromtimestamp(1756000000, tz=UTC)

    def test_a_zero_timestamp_is_an_absence(self) -> None:
        assert Measurement.from_api({"measured_time": 0}).measured_at is None

    def test_an_out_of_range_timestamp_is_an_absence(self) -> None:
        assert Measurement.from_api({"measured_time": 10**18}).measured_at is None

    def test_the_height_comes_out_of_ext_data(self) -> None:
        row = {"ext_data": json.dumps({"height": 183})}
        assert Measurement.from_api(row).height_cm == 183


class TestSyncResult:
    """Assembling one sync response."""

    def test_measurements_come_back_newest_first(self) -> None:
        result = SyncResult.from_api(
            {
                "weight_list": [
                    {"suid": 1, "measured_time": 1000, "weight_g": 70000},
                    {"suid": 1, "measured_time": 3000, "weight_g": 71000},
                    {"suid": 1, "measured_time": 2000, "weight_g": 72000},
                ]
            }
        )
        assert [m.measured_at.timestamp() for m in result.measurements] == [3000, 2000, 1000]

    def test_a_measurement_without_a_timestamp_sorts_last(self) -> None:
        result = SyncResult.from_api(
            {"weight_list": [{"measured_time": 0}, {"measured_time": 1000}]}
        )
        assert result.measurements[0].measured_at is not None
        assert result.measurements[-1].measured_at is None

    def test_deleted_rows_are_dropped(self) -> None:
        result = SyncResult.from_api(
            {
                "weight_list": [
                    {"measured_time": 1000, "is_deleted": 1},
                    {"measured_time": 2000},
                ]
            }
        )
        assert len(result.measurements) == 1

    def test_deleted_profiles_are_dropped(self) -> None:
        result = SyncResult.from_api(
            {"users": [{"suid": 1, "is_deleted": 1}, {"suid": 2}]}
        )
        assert [p.suid for p in result.profiles] == [2]

    def test_a_non_object_row_is_skipped(self) -> None:
        result = SyncResult.from_api({"weight_list": ["junk", None, {"measured_time": 1}]})
        assert len(result.measurements) == 1

    def test_the_binding_names_reach_the_devices(self) -> None:
        result = SyncResult.from_api(
            {
                "devices": [{"device_id": "d1", "name": "Robi S6"}],
                "bind_device": [{"device_id": "d1", "remark_name": "Badkamer"}],
            }
        )
        assert result.devices[0].name == "Badkamer"

    def test_an_empty_payload_yields_empty_lists(self) -> None:
        result = SyncResult.from_api({})
        assert result.measurements == []
        assert result.profiles == []
        assert result.devices == []


class TestFilteringByProfile:
    """A household account holds several people."""

    def test_no_profile_returns_everything(self) -> None:
        result = SyncResult.from_api(
            {"weight_list": [{"suid": 1, "measured_time": 2}, {"suid": 2, "measured_time": 1}]}
        )
        assert len(result.for_profile(None)) == 2

    def test_one_profile_filters_the_rest_out(self) -> None:
        result = SyncResult.from_api(
            {"weight_list": [{"suid": 1, "measured_time": 2}, {"suid": 2, "measured_time": 1}]}
        )
        assert [m.suid for m in result.for_profile(2)] == [2]

    def test_the_profile_id_is_compared_as_text(self) -> None:
        # The API is inconsistent about sending suid as a number or a string.
        result = SyncResult.from_api({"weight_list": [{"suid": "7", "measured_time": 1}]})
        assert len(result.for_profile(7)) == 1

    def test_the_latest_is_the_newest_of_that_profile(self) -> None:
        result = SyncResult.from_api(
            {
                "weight_list": [
                    {"suid": 1, "measured_time": 3000, "weight_g": 70000},
                    {"suid": 2, "measured_time": 4000, "weight_g": 90000},
                    {"suid": 1, "measured_time": 1000, "weight_g": 71000},
                ]
            }
        )
        latest = result.latest(1)
        assert latest is not None
        assert latest.measured_at.timestamp() == 3000

    def test_a_profile_with_no_measurements_has_no_latest(self) -> None:
        assert SyncResult.from_api({}).latest(1) is None


class TestNumberCoercion:
    """The float helper has to swallow whatever the cloud sends."""

    def test_text_that_is_not_a_number_is_an_absence(self) -> None:
        assert Measurement.from_api({"weight_kg": "n/a"}).weight_kg is None

    def test_a_list_where_a_number_belongs_is_an_absence(self) -> None:
        assert Measurement.from_api({"bfr": [1, 2]}).body_fat_pct is None

    def test_skeletal_muscle_mass_is_derived_like_the_others(self) -> None:
        row = {"weight_g": 80000, "rosm": 45}
        assert Measurement.from_api(row).skeletal_muscle_kg == 36.0
