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
from .constants import (
    API_BASES,
    APP_VERSION,
    DEFAULT_API_BASE,
    DEFAULT_COUNTRY,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_LANGUAGE,
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
    ScaleDevice,
    Session,
    SyncResult,
    UserProfile,
)
from .signing import hash_password, signed_query

__version__ = "1.0.0"

__all__ = [
    # Main client
    "FitdaysClient",
    "TokenUpdatedCallback",
    # Session / auth
    "Session",
    "hash_password",
    "signed_query",
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
]
