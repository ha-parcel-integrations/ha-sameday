"""Sample Sameday API payloads shared by the test modules.

These reproduce the envelope the keyless track-by-AWB endpoint returns on
success::

    {"awbHistory": [ {statusStateId, statusState, statusDate,
                      transitLocation, county, country}, ... ]}

The newest entry by ``statusDate`` is the current status. The real API does not
echo the AWB back, so ``trackingNumber`` is injected by the coordinator; the
fixtures include it anyway so the pure ``normalize_parcel`` tests have a
barcode.

NOTE: the endpoint and its 404 "not found" shape are confirmed, but the field
names and the numeric ``statusStateId`` vocabulary are **reconstructed from the
official Android APK** and unverified against a real RO/HU/BG AWB. When a real
AWB arrives, this is the one module to correct — see TODO.md.
"""
from __future__ import annotations

# Realistic-shaped Sameday AWB numbers, distinct per sample.
ACTIVE_CODE = "2SDAY0001111"
DELIVERED_CODE = "2SDAY0009999"


def event(
    status_state_id: str,
    timestamp: str,
    status_state: str,
    *,
    location: str = "Bucuresti",
) -> dict:
    """One Sameday ``awbHistory`` entry."""
    return {
        "statusStateId": status_state_id,
        "statusState": status_state,
        "statusDate": timestamp,
        "transitLocation": location,
        "county": "Ilfov",
        "country": "RO",
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A representative response for a delivered parcel (statusStateId 5)."""
    return {
        "trackingNumber": code,
        "awbHistory": [
            event("1", "2026-04-27T23:03:58Z", "Comanda preluata"),
            event("7", "2026-04-28T15:52:17Z", "In tranzit"),
            event("4", "2026-04-29T08:46:00Z", "Livrare in curs"),
            event("5", "2026-04-29T13:12:42Z", "Livrat"),
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery parcel (statusStateId 4)."""
    sample = delivered_sample(code)
    sample["awbHistory"] = sample["awbHistory"][:3]
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel waiting in an easybox/locker (statusStateId 18 → at pickup)."""
    sample = delivered_sample(code)
    sample["awbHistory"] = sample["awbHistory"][:2] + [
        event(
            "18",
            "2026-04-29T09:30:00Z",
            "Comanda a fost incarcata in easybox",
            location="Easybox Kaufland Baneasa",
        )
    ]
    return sample
