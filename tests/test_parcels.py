"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sameday import parcels as parcels_module
from custom_components.sameday.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    ParcelStatus,
)
from custom_components.sameday.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import active_sample, delivered_sample, event, pickup_sample

# ---------------------------------------------------------------------------
# map_parcel_status / map_event_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("1", ParcelStatus.REGISTERED),
        ("4", ParcelStatus.OUT_FOR_DELIVERY),
        ("5", ParcelStatus.DELIVERED),
        ("7", ParcelStatus.IN_TRANSIT),
        ("8", ParcelStatus.RETURNING),
        ("10", ParcelStatus.PROBLEM),
        ("18", ParcelStatus.AT_PICKUP_POINT),
    ],
)
def test_map_parcel_status_known(code, expected):
    assert map_parcel_status(code) == expected


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status("9999") == ParcelStatus.UNKNOWN


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("9999") is None
    assert map_event_status("5") == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("4242") == ParcelStatus.UNKNOWN
    assert map_parcel_status("4242") == ParcelStatus.UNKNOWN
    assert caplog.text.count("4242") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# _note_payload_shape — one-shot unconfirmed-field warning (pre-1.0.0)
# ---------------------------------------------------------------------------


def test_payload_shape_warns_once_on_unknown_fields(caplog):
    parcels_module._payload_shape_logged = False
    raw = delivered_sample()
    raw["surpriseTopLevel"] = "x"
    raw["awbHistory"][0]["surpriseEventKey"] = "y"

    normalize_parcel(raw)
    normalize_parcel(raw)

    assert caplog.text.count("have not confirmed") == 1
    assert "(top) surpriseTopLevel" in caplog.text
    assert "(event) surpriseEventKey" in caplog.text
    assert "issues/new" in caplog.text


def test_payload_shape_silent_on_known_fields(caplog):
    parcels_module._payload_shape_logged = False
    normalize_parcel(delivered_sample())
    assert "have not confirmed" not in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(delivered_sample()["awbHistory"])
    assert len(history) == 4
    assert history[0]["raw_status"] == "Comanda preluata"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    # Sameday events carry a numeric status id, so history entries are mapped.
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    events = [
        event("7", f"2026-04-{day:02d}T10:00:00Z", "In tranzit")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"statusStateId": "7"}]) == []  # no statusDate
    assert build_history(["not-a-dict"]) == []


def test_build_history_keeps_unparseable_timestamp_last():
    history = build_history(
        [
            event("1", "2026-04-24T10:00:00Z", "fine"),
            event("7", "not-a-date", "odd"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["fine", "odd"]


def test_build_history_falls_back_to_status_id_without_text():
    history = build_history([event("7", "2026-04-24T10:00:00Z", "")])
    assert history[0]["raw_status"] == "7"


# ---------------------------------------------------------------------------
# _latest_event
# ---------------------------------------------------------------------------


def test_latest_event_picks_newest_regardless_of_order():
    events = [
        event("1", "2026-04-27T23:03:58Z", "old"),
        event("5", "2026-04-29T13:12:42Z", "new"),
        event("7", "2026-04-28T15:52:17Z", "mid"),
    ]
    assert parcels_module._latest_event(events)["statusState"] == "new"


def test_latest_event_empty_is_none():
    assert parcels_module._latest_event([]) is None
    assert parcels_module._latest_event(["not-a-dict"]) is None


def test_latest_event_all_unparseable_falls_back_to_first():
    events = [event("1", "nope", "first"), event("7", "also-nope", "second")]
    assert parcels_module._latest_event(events)["statusState"] == "first"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Sameday"
    assert parcel["barcode"] == "2SDAY0009999"
    # Sameday's public payload names neither party.
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Livrat"
    assert parcel["delivered"] is True
    # delivered_at is the newest event's statusDate.
    assert parcel["delivered_at"] == "2026-04-29T13:12:42Z"
    # Sameday's public timeline carries no ETA window at all.
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    # Deep link defaults to the RO tracking page (default country).
    assert parcel["url"] == "https://sameday.ro/status-colet/?awb=2SDAY0009999"
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 4
    assert parcel["history"][0]["raw_status"] == "Comanda preluata"


def test_normalize_active_parcel_has_no_window():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["delivered_at"] is None


def test_normalize_pickup_parcel_names_the_locker():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    # The locker/easybox location comes from the current event's transitLocation.
    assert parcel["pickup_point"] == "Easybox Kaufland Baneasa"


def test_normalize_falls_back_to_status_id_without_text():
    raw = active_sample()
    raw["awbHistory"][-1]["statusState"] = ""
    assert normalize_parcel(raw)["raw_status"] == "4"


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned AWB still yields a full parcel dict."""
    parcel = normalize_parcel({"trackingNumber": "2SDAY0000001"})
    assert parcel["barcode"] == "2SDAY0000001"
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["pickup_point"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


@pytest.mark.parametrize(
    "country,expected",
    [
        ("ro", "https://sameday.ro/status-colet/?awb=2SDAY0009999"),
        ("hu", "https://sameday.hu/#awb=2SDAY0009999"),
        ("bg", "https://sameday.bg/status-na-pratkata/?awb=2SDAY0009999"),
    ],
)
def test_normalize_deep_link_is_per_country(country, expected):
    assert normalize_parcel(delivered_sample(), country=country)["url"] == expected


def test_normalize_deep_link_none_for_unknown_country():
    assert normalize_parcel(delivered_sample(), country="xx")["url"] is None


def test_normalize_unmapped_status_is_unknown():
    raw = active_sample()
    raw["awbHistory"][-1]["statusStateId"] = "9999"
    assert normalize_parcel(raw)["status"] == ParcelStatus.UNKNOWN


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
