"""
fitdays — unofficial async Python client for the Fitdays (ICOMON) cloud API.

Fitdays is the app behind a family of white-label body-composition scales
(Robi S6 and friends). The scale computes fat/muscle/water/bone from impedance
and uploads the result; the app is the only place that data appears. There is
no public API — this package speaks the app's own protocol.

Read-only: it logs in, then pulls measurement history for each member profile
on the account.

    from fitdays import FitdaysClient

    client = await FitdaysClient.login("you@example.com", "password")
    latest = await client.get_latest()
    print(latest.weight_kg, latest.body_fat_pct)
"""

from .client import FitdaysClient, TokenUpdatedCallback
from .plus import FitdaysPlusClient, PlusTokenUpdatedCallback
from .constants import (
    API_BASES,
    APP_VERSION,
    DEFAULT_API_BASE,
    DEFAULT_COUNTRY,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_LANGUAGE,
    PLUS_API_BASES,
    PLUS_APP_VERSION,
    PLUS_DEFAULT_API_BASE,
)
from .exceptions import (
    FitdaysAPIError,
    FitdaysAuthError,
    FitdaysError,
    FitdaysNetworkError,
    FitdaysValidationError,
)
from .models import (
    Measurement,
    PlusSession,
    ScaleDevice,
    Session,
    SyncResult,
    UserProfile,
)
from .plus_signing import plus_access_code, plus_access_sign, plus_headers
from .signing import hash_password, signed_query

__version__ = "1.1.0"

__all__ = [
    # Main client
    "FitdaysClient",
    "TokenUpdatedCallback",
    "FitdaysPlusClient",
    "PlusTokenUpdatedCallback",
    # Session / auth
    "Session",
    "hash_password",
    "signed_query",
    "PlusSession",
    "plus_access_code",
    "plus_access_sign",
    "plus_headers",
    # Exceptions
    "FitdaysError",
    "FitdaysAPIError",
    "FitdaysAuthError",
    "FitdaysNetworkError",
    "FitdaysValidationError",
    # Models
    "Measurement",
    "UserProfile",
    "ScaleDevice",
    "SyncResult",
    # Constants
    "API_BASES",
    "APP_VERSION",
    "DEFAULT_API_BASE",
    "DEFAULT_COUNTRY",
    "DEFAULT_LANGUAGE",
    "DEFAULT_HISTORY_DAYS",
    "PLUS_API_BASES",
    "PLUS_APP_VERSION",
    "PLUS_DEFAULT_API_BASE",
]
