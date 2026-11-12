"""
Typed models for the data the Fitdays API returns.

Every model keeps the original payload on ``.raw`` so callers can reach fields
this library does not surface yet, and every ``from_api`` is defensive: the
cloud happily returns nulls, empty strings and zero-as-missing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _num(value: Any) -> Optional[float]:
    """Best-effort float conversion; ``None`` for anything unusable."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    """Round, tolerating ``None``. The cloud sends floats like 62.499998092651."""
    return None if value is None else round(value, digits)


def _int(value: Any) -> Optional[int]:
    """Best-effort int conversion."""
    number = _num(value)
    return None if number is None else int(number)


def _positive(value: Optional[float]) -> Optional[float]:
    """Treat 0 as "not measured" — the API uses it for absent optional metrics."""
    return value if value else None


def _dt(value: Any) -> Optional[datetime]:
    """Unix seconds -> timezone-aware UTC datetime."""
    seconds = _int(value)
    if not seconds:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_ext(value: Any) -> dict[str, Any]:
    """``ext_data`` arrives as a JSON *string* (or occasionally already a dict)."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_number(value: Any) -> Optional[float]:
    """
    The first value of an impedance list such as ``"512.3"`` or ``"[512.3,498.1]"``.

    Fitdays+ stores impedance as a comma-separated string (``cn.icomon.base.i``
    ``p()`` writes it, ``i()`` reads it back, tolerating brackets). A
    four-electrode scale sends one value. An eight-electrode scale sends ten,
    and the APK does not say which of them is the whole-body reading, so the
    full list stays on ``Measurement.raw["imps"]``.
    """
    if isinstance(value, str):
        value = value.replace("[", "").replace("]", "").split(",", 1)[0].strip()
    return _num(value)


@dataclass
class Session:
    """
    Everything needed to talk to the API again without a fresh password prompt.

    ``password_hash`` is the double-MD5 digest the login endpoint expects — the
    plaintext password is never stored here, and re-login works from the digest
    alone, which is what makes unattended token recovery possible.
    """

    token: Optional[str] = None
    refresh_token: Optional[str] = None
    uid: Optional[int] = None
    active_suid: Optional[int] = None
    email: Optional[str] = None
    password_hash: Optional[str] = None
    api_base: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for persistent storage."""
        return {
            "token": self.token,
            "refresh_token": self.refresh_token,
            "uid": self.uid,
            "active_suid": self.active_suid,
            "email": self.email,
            "password_hash": self.password_hash,
            "api_base": self.api_base,
            "country": self.country,
            "language": self.language,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        """Rebuild from a previously stored dict."""
        data = data or {}
        return cls(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            uid=data.get("uid"),
            active_suid=data.get("active_suid"),
            email=data.get("email"),
            password_hash=data.get("password_hash"),
            api_base=data.get("api_base"),
            country=data.get("country"),
            language=data.get("language"),
        )

    @property
    def can_relogin(self) -> bool:
        """Whether this session carries enough to re-authenticate itself."""
        return bool(self.email and self.password_hash)


@dataclass
class PlusSession:  # pylint: disable=too-many-instance-attributes
    """
    Stored state for a Fitdays+ account.

    The login sends two digests of the password (``access_code`` and
    ``access_sign``). Both are kept so the client can log in again without the
    plaintext. ``client_id`` is kept too, so a restarted client presents itself
    as the same installation.
    """

    token: Optional[str] = None
    account_id: Optional[str] = None
    email: Optional[str] = None
    access_code: Optional[str] = None
    access_sign: Optional[str] = None
    client_id: Optional[str] = None
    api_base: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for persistent storage."""
        return {
            "token": self.token,
            "account_id": self.account_id,
            "email": self.email,
            "access_code": self.access_code,
            "access_sign": self.access_sign,
            "client_id": self.client_id,
            "api_base": self.api_base,
            "country": self.country,
            "language": self.language,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlusSession":
        """Rebuild from a previously stored dict."""
        data = data or {}
        return cls(**{name: data.get(name) for name in cls().to_dict()})

    @property
    def can_relogin(self) -> bool:
        """Whether this session carries enough to re-authenticate itself."""
        return bool(self.email and self.access_code and self.access_sign)


@dataclass
class UserProfile:
    """
    One member profile on the account.

    A single Fitdays account can hold several people (``suid``); the scale
    attributes each measurement to one of them.
    """

    suid: Optional[int]
    uid: Optional[int] = None
    nickname: Optional[str] = None
    sex: Optional[int] = None
    birthday: Optional[str] = None
    height_cm: Optional[float] = None
    target_weight_kg: Optional[float] = None
    people_type: Optional[int] = None
    photo: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "UserProfile":
        """Build a :class:`UserProfile` from a ``users[]`` entry."""
        payload = payload or {}
        return cls(
            suid=_int(payload.get("suid")),
            uid=_int(payload.get("uid")),
            nickname=payload.get("nickname") or None,
            sex=_int(payload.get("sex")),
            birthday=(payload.get("birthday") or None),
            height_cm=_num(payload.get("height")),
            target_weight_kg=_positive(_num(payload.get("target_weight"))),
            people_type=_int(payload.get("people_type")),
            photo=payload.get("photo") or None,
            raw=payload,
        )

    @classmethod
    def from_plus_api(cls, payload: dict[str, Any]) -> "UserProfile":
        """
        Build a :class:`UserProfile` from a Fitdays+ ``users[]`` entry.

        Fitdays+ calls the member id ``uid`` and the account ``account_id``.
        They land in ``suid`` and ``uid`` so code written for Fitdays reads
        both backends the same way.
        """
        payload = payload or {}
        return cls(
            suid=_int(payload.get("uid")),
            uid=_int(payload.get("account_id")),
            nickname=payload.get("nickname") or None,
            sex=_int(payload.get("sex")),
            birthday=(payload.get("birthday") or None),
            height_cm=_positive(_num(payload.get("height"))),
            target_weight_kg=_positive(_num(payload.get("target_weight"))),
            people_type=_int(payload.get("people_type")),
            photo=payload.get("avatar") or None,
            raw=payload,
        )

    @property
    def display_name(self) -> str:
        """A human label, falling back to the profile id."""
        return self.nickname or f"Profile {self.suid}"


@dataclass
class ScaleDevice:  # pylint: disable=too-many-instance-attributes
    """A scale bound to the account."""

    device_id: Optional[str]
    name: Optional[str] = None
    mac: Optional[str] = None
    serial: Optional[str] = None
    model: Optional[str] = None
    device_type: Optional[int] = None
    firmware_version: Optional[str] = None
    hardware_version: Optional[str] = None
    measures_heart_rate: bool = False
    offline_measure: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: dict[str, Any],
                 bindings: Optional[dict[str, dict[str, Any]]] = None) -> "ScaleDevice":
        """
        Build a :class:`ScaleDevice` from a ``devices[]`` entry.

        ``bindings`` maps device_id -> ``bind_device[]`` entry, which is where
        the user's own rename (``remark_name``) lives.
        """
        payload = payload or {}
        ext = _parse_ext(payload.get("ext_data"))
        device_id = payload.get("device_id")
        binding = (bindings or {}).get(device_id) or {}
        return cls(
            device_id=device_id,
            name=binding.get("remark_name") or payload.get("name") or None,
            mac=payload.get("mac") or None,
            serial=payload.get("sn") or None,
            model=payload.get("model") or payload.get("name") or None,
            device_type=_int(payload.get("device_type")),
            firmware_version=payload.get("firmware_ver") or None,
            hardware_version=payload.get("hardware_ver") or None,
            measures_heart_rate=bool(ext.get("measureHeart")),
            offline_measure=bool(ext.get("offlineMeasure")),
            raw=payload,
        )


    @classmethod
    def from_plus_api(cls, payload: dict[str, Any],
                      bindings: Optional[dict[str, dict[str, Any]]] = None) -> "ScaleDevice":
        """
        Build a :class:`ScaleDevice` from a Fitdays+ ``devices[]`` entry.

        ``bindings`` maps device_id -> ``bind_device[]`` entry. Fitdays+ keeps
        the user's rename in ``remark``.
        """
        payload = payload or {}
        device_id = payload.get("device_id")
        binding = (bindings or {}).get(device_id) or {}
        return cls(
            device_id=device_id,
            name=binding.get("remark") or payload.get("name") or None,
            mac=payload.get("mac") or None,
            serial=payload.get("sn") or None,
            model=payload.get("model") or payload.get("name") or None,
            device_type=_int(payload.get("device_type")),
            firmware_version=payload.get("firmware_ver") or None,
            hardware_version=payload.get("hardware_ver") or None,
            raw=payload,
        )


@dataclass
class Measurement:  # pylint: disable=too-many-instance-attributes
    """
    A single weigh-in.

    Percentages are as the scale reports them; the ``*_mass_kg`` values are
    derived here (percentage x weight) because the API only sends ratios.
    """

    suid: Optional[int]
    measured_at: Optional[datetime]
    weight_kg: Optional[float]
    weight_lb: Optional[float] = None
    bmi: Optional[float] = None
    body_fat_pct: Optional[float] = None
    subcutaneous_fat_pct: Optional[float] = None
    visceral_fat: Optional[float] = None
    muscle_pct: Optional[float] = None
    skeletal_muscle_pct: Optional[float] = None
    bone_mass_kg: Optional[float] = None
    body_water_pct: Optional[float] = None
    protein_pct: Optional[float] = None
    bmr: Optional[int] = None
    body_age: Optional[int] = None
    heart_rate: Optional[int] = None
    impedance: Optional[float] = None
    electrodes: Optional[int] = None
    height_cm: Optional[float] = None
    device_id: Optional[str] = None
    data_id: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "Measurement":
        """Build a :class:`Measurement` from a ``weight_list[]`` row."""
        payload = payload or {}
        ext = _parse_ext(payload.get("ext_data"))

        # weight_g is the integer the scale actually sent; weight_kg is the same
        # value after a float round-trip, e.g. 62.499998092651 for 62.5. Prefer
        # the former when present.
        grams = _num(payload.get("weight_g"))
        weight = grams / 1000 if grams else _num(payload.get("weight_kg"))

        return cls(
            suid=_int(payload.get("suid")),
            measured_at=_dt(payload.get("measured_time")),
            weight_kg=_round(weight),
            weight_lb=_round(_num(payload.get("weight_lb"))),
            bmi=_round(_num(payload.get("bmi")), 1),
            body_fat_pct=_round(_positive(_num(payload.get("bfr"))), 1),
            subcutaneous_fat_pct=_round(_positive(_num(payload.get("sfr"))), 1),
            visceral_fat=_round(_positive(_num(payload.get("uvi"))), 1),
            muscle_pct=_round(_positive(_num(payload.get("rom"))), 1),
            skeletal_muscle_pct=_round(_positive(_num(payload.get("rosm"))), 1),
            bone_mass_kg=_round(_positive(_num(payload.get("bm")))),
            body_water_pct=_round(_positive(_num(payload.get("vwc"))), 1),
            protein_pct=_round(_positive(_num(payload.get("pp"))), 1),
            bmr=_int(_positive(_num(payload.get("bmr")))),
            body_age=_int(_positive(_num(payload.get("bodyage")))),
            heart_rate=_int(_positive(_num(payload.get("hr")))),
            impedance=_round(_positive(_num(payload.get("adc"))), 1),
            electrodes=_int(payload.get("electrode")),
            height_cm=_num(ext.get("height")),
            device_id=payload.get("device_id") or None,
            data_id=payload.get("data_id") or None,
            raw=payload,
        )

    @classmethod
    def from_plus_api(cls, payload: dict[str, Any]) -> "Measurement":
        """
        Build a :class:`Measurement` from a Fitdays+ ``weights[]`` row.

        The row itself carries weight, BMI, body fat and impedance. The rest of
        the body composition sits in ``ext``, a JSON string whose
        ``body_composition`` object the app fills in after it computes the
        values on the phone.
        """
        payload = payload or {}
        composition = _parse_ext(payload.get("ext")).get("body_composition")
        composition = composition if isinstance(composition, dict) else {}

        grams = _num(payload.get("weight_g"))
        weight = grams / 1000 if grams else _num(payload.get("weight_kg"))

        def metric(key: str, digits: int = 1) -> Optional[float]:
            return _round(_positive(_num(composition.get(key))), digits)

        return cls(
            suid=_int(payload.get("uid")),
            measured_at=_dt(payload.get("measure_time")),
            weight_kg=_round(weight),
            weight_lb=_round(_positive(_num(payload.get("weight_lb")))),
            bmi=_round(_positive(_num(payload.get("bmi"))), 1),
            body_fat_pct=_round(_positive(_num(payload.get("pbf"))), 1),
            subcutaneous_fat_pct=metric("subcutaneousFatPercent"),
            visceral_fat=metric("visceralFat"),
            muscle_pct=metric("musclePercent"),
            skeletal_muscle_pct=metric("smPercent"),
            bone_mass_kg=metric("boneMass", 2),
            body_water_pct=metric("moisturePercent"),
            protein_pct=metric("proteinPercent"),
            bmr=_int(metric("bmr")),
            body_age=_int(metric("physicalAge")),
            heart_rate=_int(metric("hr")),
            impedance=_round(_positive(_first_number(payload.get("imps"))), 1),
            electrodes=_int(payload.get("electrode")),
            height_cm=_positive(_num(payload.get("height"))),
            device_id=payload.get("device_id") or None,
            data_id=payload.get("data_id") or None,
            raw=payload,
        )

    def _mass_from_pct(self, percentage: Optional[float]) -> Optional[float]:
        """Convert one of the ratio metrics into kilograms."""
        if percentage is None or not self.weight_kg:
            return None
        return round(percentage * self.weight_kg / 100, 2)

    @property
    def body_fat_kg(self) -> Optional[float]:
        """Fat mass in kilograms."""
        return self._mass_from_pct(self.body_fat_pct)

    @property
    def muscle_mass_kg(self) -> Optional[float]:
        """Muscle mass in kilograms."""
        return self._mass_from_pct(self.muscle_pct)

    @property
    def skeletal_muscle_kg(self) -> Optional[float]:
        """Skeletal muscle mass in kilograms."""
        return self._mass_from_pct(self.skeletal_muscle_pct)

    @property
    def body_water_kg(self) -> Optional[float]:
        """Total body water in kilograms."""
        return self._mass_from_pct(self.body_water_pct)

    @property
    def protein_kg(self) -> Optional[float]:
        """Protein mass in kilograms."""
        return self._mass_from_pct(self.protein_pct)

    @property
    def is_weight_only(self) -> bool:
        """True when the scale recorded no impedance (so no body composition)."""
        return self.body_fat_pct is None and self.impedance is None


@dataclass
class SyncResult:
    """Everything one ``syncFromServer`` call returned."""

    measurements: list[Measurement] = field(default_factory=list)
    profiles: list[UserProfile] = field(default_factory=list)
    devices: list[ScaleDevice] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "SyncResult":
        """Build a :class:`SyncResult` from a sync response ``data`` block."""
        payload = payload or {}
        bindings = {
            entry.get("device_id"): entry
            for entry in (payload.get("bind_device") or [])
            if isinstance(entry, dict)
        }
        rows = [
            row for row in (payload.get("weight_list") or [])
            if isinstance(row, dict) and not row.get("is_deleted")
        ]
        measurements = [Measurement.from_api(row) for row in rows]
        measurements.sort(
            key=lambda m: m.measured_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return cls(
            measurements=measurements,
            profiles=[
                UserProfile.from_api(user)
                for user in (payload.get("users") or [])
                if isinstance(user, dict) and not user.get("is_deleted")
            ],
            devices=[
                ScaleDevice.from_api(device, bindings)
                for device in (payload.get("devices") or [])
                if isinstance(device, dict)
            ],
            raw=payload,
        )

    @classmethod
    def from_plus_api(cls, payload: dict[str, Any],
                      users: Optional[list[dict[str, Any]]] = None) -> "SyncResult":
        """
        Build a :class:`SyncResult` from a Fitdays+ sync ``data`` block.

        ``sync_from_server`` returns no ``users``. Pass them in ``users`` when
        they came from another call. A ``users`` list in ``payload`` wins.
        """
        payload = payload or {}
        bindings = {
            entry.get("device_id"): entry
            for entry in (payload.get("bind_device") or [])
            if isinstance(entry, dict) and not entry.get("is_deleted")
        }
        deleted = {
            entry.get("data_id")
            for entry in (payload.get("weight_delete_list") or [])
            if isinstance(entry, dict)
        }
        measurements = [
            Measurement.from_plus_api(row)
            for row in (payload.get("weights") or [])
            if isinstance(row, dict) and row.get("data_id") not in deleted
        ]
        measurements.sort(
            key=lambda m: m.measured_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        user_rows = payload.get("users") if payload.get("users") is not None else users
        return cls(
            measurements=measurements,
            profiles=[
                UserProfile.from_plus_api(user)
                for user in (user_rows or [])
                if isinstance(user, dict) and not user.get("is_deleted")
            ],
            devices=[
                ScaleDevice.from_plus_api(device, bindings)
                for device in (payload.get("devices") or [])
                if isinstance(device, dict)
            ],
            raw=payload,
        )

    def for_profile(self, suid: Optional[int]) -> list[Measurement]:
        """Measurements belonging to one member profile, newest first."""
        if suid is None:
            return list(self.measurements)
        return [m for m in self.measurements if str(m.suid) == str(suid)]

    def latest(self, suid: Optional[int] = None) -> Optional[Measurement]:
        """The most recent measurement, optionally for one profile."""
        rows = self.for_profile(suid)
        return rows[0] if rows else None
