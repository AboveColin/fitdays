# fitdays

An unofficial **async Python client** for the Fitdays (ICOMON) smart-scale cloud API.

Fitdays is the app behind a family of white-label body-composition scales — the
**Robi S6** and a long tail of rebadged siblings. The scale measures bio-impedance,
the cloud turns that into fat / muscle / water / bone / BMR figures, and the app is
the only place those numbers show up. There is no public API, so this package speaks
the app's own protocol.

Read-only: it logs in, then pulls measurement history. It never writes to your account.

```bash
pip install fitdays
```

## Quick start

```python
import asyncio
from fitdays import FitdaysClient

async def main():
    async with await FitdaysClient.login("you@example.com", "hunter2") as client:
        latest = await client.get_latest()
        print(latest.weight_kg, "kg")
        print(latest.body_fat_pct, "% fat →", latest.body_fat_kg, "kg")
        print(latest.muscle_mass_kg, "kg muscle")

asyncio.run(main())
```

See [`examples/quickstart.py`](examples/quickstart.py) for a fuller tour.

## What you get

`get_sync()` returns everything in one round-trip:

| | |
|---|---|
| `.measurements` | `Measurement` objects, newest first |
| `.profiles` | the member profiles on the account (`UserProfile`) |
| `.devices` | the scales bound to the account (`ScaleDevice`) |

A `Measurement` carries what the scale reported — `weight_kg`, `bmi`,
`body_fat_pct`, `subcutaneous_fat_pct`, `visceral_fat`, `muscle_pct`,
`skeletal_muscle_pct`, `bone_mass_kg`, `body_water_pct`, `protein_pct`, `bmr`,
`body_age`, `heart_rate`, `impedance` — plus derived masses the API only sends as
ratios: `body_fat_kg`, `muscle_mass_kg`, `skeletal_muscle_kg`, `body_water_kg`,
`protein_kg`.

`is_weight_only` tells you a weigh-in came without impedance (socks, or a quick step
on the scale), so you can skip the body-composition fields rather than charting
nulls.

## Multiple people

One Fitdays account can hold several member profiles, each with its own `suid`, and
the scale attributes each weigh-in to one of them:

```python
result = await client.get_sync()
for profile in result.profiles:
    latest = result.latest(profile.suid)
    print(profile.display_name, latest.weight_kg)
```

## Sessions and tokens

Logging in every time is unnecessary and rude to the server. Persist the session
instead:

```python
stored = client.session.to_dict()   # no plaintext password in here
...
client = FitdaysClient.from_session(stored, token_updated=save_it)
```

Two things happen automatically:

* **Self-healing login.** Tokens are long-lived but not eternal. When one is
  rejected, the client re-authenticates using the stored password *digest* and
  retries the call — then fires your `token_updated` callback so you can persist
  the new token.
* **Region redirects.** An account registered outside Europe answers with
  `code 302` and the host it actually lives on. The client follows that once and
  remembers it.

### About the password

The login endpoint expects `MD5(MD5(password + "hx"))`, so the plaintext never
leaves your process — `Session` stores only that digest, and re-login works from
the digest alone. It is still a credential (it is password-equivalent to this API),
so store it the way you would store a token.

## Home Assistant

There is a companion integration: **[HA-Fitdays](https://github.com/AboveColin/HA-Fitdays)**,
which turns each member profile into a device with body-composition sensors.

## Errors

| Exception | Means |
|---|---|
| `FitdaysAuthError` | login failed, or the token is dead and cannot be renewed |
| `FitdaysNetworkError` | timeout / connection problem |
| `FitdaysAPIError` | the cloud returned a non-success `code` |
| `FitdaysValidationError` | you passed something unusable |

All inherit from `FitdaysError`.

## Disclaimer

Not affiliated with, endorsed by, or supported by GUANGDONG ICOMON or Fitdays. The
protocol was determined by observing the app's own traffic against the author's own
account. Endpoints may change or disappear without notice. Use at your own risk.

## Licence

MIT — see [LICENSE](LICENSE).
