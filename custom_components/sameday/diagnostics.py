"""Diagnostics support for the Sameday parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SamedayConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# Sameday's public AWB payload carries little PII: the AWB itself and the
# transit locations. A ``transitLocation`` is often the easybox/locker the
# recipient collects from, so it is redacted; the transit ``country`` (ro/hu/bg
# network) is not personal and stays. ``sender``/``receiver`` are always None
# here but kept in the set for cross-carrier parity.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "pickup_point",
    "url",
    # Sameday payload fields
    "trackingNumber",
    "transitLocation",
    "county",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SamedayConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Sameday config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "polling": {
            "current_tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
