"""The parcel contract's checks, run over every normalised sample."""

from zoneinfo import ZoneInfo

import pytest

from custom_components.sameday.const import ParcelStatus
from custom_components.sameday.parcels import (
    normalize_parcel,
    parse_iso,
    planned_window,
)

from .payloads import active_sample, delivered_sample, event, pickup_sample

ZONE = ZoneInfo("Europe/Bucharest")

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


def _with_status(status_id: str) -> dict:
    sample = pickup_sample()
    sample["awbHistory"].append(event(status_id, "2026-04-30T08:00:00Z", "Status"))
    return sample


def _collected() -> dict:
    sample = pickup_sample()
    sample["awbHistory"].append(
        event("5", "2026-04-29T16:00:00Z", "Livrat", location="Bucuresti")
    )
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "returning": lambda: _with_status("14"),
    "problem": lambda: _with_status("10"),
    "bare": lambda: {"trackingNumber": "2SDAY0000001"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T16:00:00Z"
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Easybox Kaufland Baneasa"


def test_a_returning_parcel_keeps_pickup_and_is_not_delivered():
    parcel = normalize_parcel(_with_status("14"))
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["pickup"] is True


def test_a_home_delivery_is_not_pickup():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_pickup_point_is_the_newest_pickup_event_not_the_scan_location():
    sample = pickup_sample()
    sample["awbHistory"].append(
        event("18", "2026-04-29T10:30:00Z", "Easybox", location="Easybox Mega Image")
    )
    sample["awbHistory"].append(
        event("5", "2026-04-29T18:00:00Z", "Livrat", location="Depozit Bucuresti")
    )
    assert normalize_parcel(sample)["pickup_point"] == "Easybox Mega Image"


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=ZONE)
    assert start == "2099-04-29T13:00:00Z"
    assert end == "2099-04-29T23:59:59+03:00"


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=ZONE)
    assert start == "2099-04-29T00:00:00+03:00"
    assert end == "2099-04-29T23:59:59+03:00"
