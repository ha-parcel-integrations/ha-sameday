"""Tests for Sameday diagnostics."""
from unittest.mock import MagicMock

from custom_components.sameday.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "2SDAY0009999"}]}
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "2SDAY0009999",
            "receiver": None,
            "pickup_point": "Easybox Kaufland Baneasa",
            "status": "at_pickup_point",
            "raw": {
                "trackingNumber": "2SDAY0009999",
                "awbHistory": [
                    {
                        "statusStateId": "18",
                        "transitLocation": "Easybox Kaufland Baneasa",
                        "county": "Ilfov",
                        "country": "RO",
                    }
                ],
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {"incoming_active": 1, "delivered": 0}
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["pickup_point"] == "**REDACTED**"
    event = result["incoming"][0]["raw"]["awbHistory"][0]
    assert event["transitLocation"] == "**REDACTED**"
    assert event["county"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "at_pickup_point"
    assert event["country"] == "RO"
