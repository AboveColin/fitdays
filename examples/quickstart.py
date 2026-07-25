"""
Minimal end-to-end example.

    FITDAYS_EMAIL=you@example.com FITDAYS_PASSWORD=... python examples/quickstart.py

Logs in, prints the account's profiles and scales, then the latest weigh-in for
each profile. Nothing is written back — the client is read-only.
"""

import asyncio
import os

from fitdays import FitdaysClient


async def main() -> None:
    """Log in and dump a short summary of the account."""
    email = os.environ["FITDAYS_EMAIL"]
    password = os.environ["FITDAYS_PASSWORD"]

    async with await FitdaysClient.login(email, password) as client:
        result = await client.get_sync(days=400)

        print(f"{len(result.measurements)} measurements on this account\n")

        for device in result.devices:
            print(f"scale: {device.name} ({device.model}) mac={device.mac} "
                  f"heart-rate={device.measures_heart_rate}")

        for profile in result.profiles:
            latest = result.latest(profile.suid)
            print(f"\n--- {profile.display_name} (suid {profile.suid}) ---")
            print(f"height {profile.height_cm} cm, target {profile.target_weight_kg} kg")
            if latest is None:
                print("no measurements yet")
                continue
            print(f"measured at : {latest.measured_at}")
            print(f"weight      : {latest.weight_kg} kg")
            print(f"bmi         : {latest.bmi}")
            print(f"body fat    : {latest.body_fat_pct} % ({latest.body_fat_kg} kg)")
            print(f"muscle      : {latest.muscle_pct} % ({latest.muscle_mass_kg} kg)")
            print(f"water       : {latest.body_water_pct} % ({latest.body_water_kg} kg)")
            print(f"bone mass   : {latest.bone_mass_kg} kg")
            print(f"bmr         : {latest.bmr} kcal")
            print(f"body age    : {latest.body_age}")
            print(f"visceral    : {latest.visceral_fat}")


if __name__ == "__main__":
    asyncio.run(main())
