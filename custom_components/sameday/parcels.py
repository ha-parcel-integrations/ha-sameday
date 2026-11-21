"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

The carrier-specific parts are :data:`_STATUS_MAP` and :func:`normalize_parcel`
(plus the ``awbHistory`` field lookups in :func:`build_history` /
:func:`_latest_event`). Everything else — the timestamp parsing, the sort
contract, the delivered filter, the one-shot warnings — is suite-wide machinery
and should be left alone.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    COUNTRY_TRACKING_URLS,
    DEFAULT_COUNTRY,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-sameday/issues/new"
    "?template=unrecognised_status.yml"
)

# Sameday's numeric ``statusStateId`` → canonical ParcelStatus.
#
# Keys are the numeric ids as **strings** (the API sends them as ints; we
# str()-them before lookup). The vocabulary and ids are reconstructed from the
# official Android APK's ``@SerializedName`` enum (28 values) and are
# **unverified against a real RO AWB** — an unmapped id surfaces as ``unknown``
# plus a one-shot warning that asks the user to report it, which is how the map
# is confirmed and grows.
#
# The canonical enum in this template has no CANCELED / RETURNED / FAILED /
# DELAYED member, so those Sameday states map to the nearest available value:
# cancellations and delivery failures → PROBLEM, every return flavour →
# RETURNING, postponements/redirects → IN_TRANSIT. The comment on each line is
# Sameday's own name for the id.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "1": ParcelStatus.REGISTERED,  # ORDER_OF_PARCELS_PLACED
    "2": ParcelStatus.IN_TRANSIT,  # AT_COURIER
    "3": ParcelStatus.IN_TRANSIT,  # IN_COURIER_WAREHOUSE
    "4": ParcelStatus.OUT_FOR_DELIVERY,  # DELIVERY_IN_PROGRESS
    "5": ParcelStatus.DELIVERED,  # PARCELS_DELIVERED
    "6": ParcelStatus.PROBLEM,  # EXPIRED_CANCELLED
    "7": ParcelStatus.IN_TRANSIT,  # TRANSIT
    "8": ParcelStatus.RETURNING,  # SUCCESSFULLY_RETURNED
    "9": ParcelStatus.IN_TRANSIT,  # ORDER_REDIRECTED
    "10": ParcelStatus.PROBLEM,  # DELIVERY_FAILED
    "11": ParcelStatus.PROBLEM,  # SENDER_CANCELLED
    "12": ParcelStatus.IN_TRANSIT,  # DELIVERY_POSTPONED
    "13": ParcelStatus.RETURNING,  # PICKED_UP_RETURN
    "14": ParcelStatus.RETURNING,  # ONGOING_RETURN
    "15": ParcelStatus.RETURNING,  # RETURN_IN_OOH
    "16": ParcelStatus.PROBLEM,  # PICKUP_FAILED
    "17": ParcelStatus.IN_TRANSIT,  # ORDER_IN_CENTRAL_WAREHOUSE
    "18": ParcelStatus.AT_PICKUP_POINT,  # LOADED_IN_OOH (in the locker/easybox)
    "19": ParcelStatus.IN_TRANSIT,  # IN_INTERNATIONAL_TRANSIT
    "20": ParcelStatus.AT_PICKUP_POINT,  # SAMEDAY_POINT_CROWDED
    "21": ParcelStatus.IN_TRANSIT,  # PARCEL_KEPT_IN_CENTRAL_WAREHOUSE
    "22": ParcelStatus.PROBLEM,  # SAMEDAY_POINT_TEMPORARY_UNAVAILABLE
    "23": ParcelStatus.PROBLEM,  # INCOMPLETE_DELIVERY
    "24": ParcelStatus.IN_TRANSIT,  # PARCEL_REDIRECTED_TO_OOH
    "25": ParcelStatus.IN_TRANSIT,  # PARCEL_REDIRECTED_TO_ADDRESS
    "26": ParcelStatus.IN_TRANSIT,  # PARCEL_REDIRECTED_TO_ANOTHER_OOH
    "27": ParcelStatus.IN_TRANSIT,  # PARCEL_REDIRECTED_TO_INITIAL_EASYBOX
    "29": ParcelStatus.PROBLEM,  # PARCEL_UNAVAILABLE_FOR_DELIVERY
    "30": ParcelStatus.PROBLEM,  # PARCEL_CANNOT_BE_DELIVERED
}

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()

# The field names and status ids in this integration are reconstructed from the
# official Android APK and have **never been run against a real RO/HU/BG AWB**
# (pre-1.0.0). The first real payload that carries a top-level field or an
# ``awbHistory`` event key beyond the known sets logs them once — keys only,
# never values (a location can be personal) — so a tester can confirm what we
# should wire up. See NEW_ISSUE_URL.
_KNOWN_TOP_LEVEL_KEYS = {"awbHistory", "trackingNumber"}
_KNOWN_EVENT_KEYS = {
    "county",
    "country",
    "statusState",
    "statusStateId",
    "statusId",
    "statusDate",
    "transitLocation",
}
_payload_shape_logged = False


def _note_payload_shape(raw: dict) -> None:
    """One-shot: report unconfirmed fields so a tester can map them."""
    global _payload_shape_logged  # noqa: PLW0603  # one-shot flag per process, by design
    if _payload_shape_logged:
        return
    extra = {f"(top) {k}" for k in set(raw) - _KNOWN_TOP_LEVEL_KEYS}
    for event in raw.get("awbHistory") or []:
        if isinstance(event, dict):
            extra |= {f"(event) {k}" for k in set(event) - _KNOWN_EVENT_KEYS}
    if not extra:
        return
    _payload_shape_logged = True
    _LOGGER.warning(
        "Sameday payload carries fields we have not confirmed against a real "
        "parcel yet: %s. Please help us map them — a diagnostics file is ideal: %s",
        sorted(extra),
        NEW_ISSUE_URL,
    )


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Sameday status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if not code:
        return None
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
    }


def _event_status_id(event: dict) -> str | None:
    """Return an ``awbHistory`` entry's numeric status id as a string, or None."""
    value = event.get("statusStateId")
    return str(value) if value is not None else None


def _latest_event(events: list) -> dict | None:
    """Return the newest ``awbHistory`` entry by ``statusDate``, or ``None``.

    Sorts descending so the current status is element[0]. Entries with an
    unparseable ``statusDate`` sort last, so a good timestamp always wins; if
    *nothing* parses, the list's original first entry is returned as a
    best-effort current status.
    """
    dicts = [event for event in events if isinstance(event, dict)]
    if not dicts:
        return None
    with_ts = [
        (parsed, event)
        for event in dicts
        if (parsed := parse_iso(to_iso_timestamp(event.get("statusDate")))) is not None
    ]
    if not with_ts:
        return dicts[0]
    with_ts.sort(key=lambda item: item[0], reverse=True)
    return with_ts[0][1]


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from Sameday's ``awbHistory``.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is Sameday's own
    ``statusState`` text, falling back to the numeric id. Sorted oldest →
    newest and capped to the most recent ``max_events``.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("statusDate"))
        if not timestamp:
            continue
        status_id = _event_status_id(event)
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(status_id),
            "raw_status": event.get("statusState") or status_id,
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def tracking_url(
    tracking_code: str | None, country: str = DEFAULT_COUNTRY
) -> str | None:
    """Construct the consumer tracking deep-link for a parcel.

    Each country has its own tracking-page URL (see ``COUNTRY_TRACKING_URLS``);
    an unknown country or a missing code yields ``None``.
    """
    template = COUNTRY_TRACKING_URLS.get(country)
    if not tracking_code or template is None:
        return None
    return template.format(tracking_code=tracking_code)


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


def normalize_parcel(
    raw: dict, *, include_history: bool = False, country: str = DEFAULT_COUNTRY
) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. A key is ``None`` when Sameday does
    not expose it — never omitted.

    Rules worth keeping when you rewrite the body:

    * ``status`` is canonical, ``raw_status`` is the carrier's own text.
    * ``delivered`` is ``status is DELIVERED``; ``delivered_at`` is set only
      then, from the delivery event.
    * ``pickup`` stays ``True`` once the parcel has reached a pickup point, so
      it survives collection; ``pickup_point`` is that point's name.
    * ``weight`` is kilograms, ``dimensions`` centimetres (see
      :func:`format_dimensions`).
    * ``history`` is ``None`` when the option is off — the key still exists.
    """
    tracking_code = raw.get("trackingNumber")

    # Sameday keys everything off the AWB timeline: sort ``awbHistory`` by
    # ``statusDate`` descending and the first element is the current status.
    # An empty timeline (a not-yet-scanned AWB placeholder) leaves the parcel
    # at UNKNOWN with no dates.
    history_events = raw.get("awbHistory") or []
    if history_events:
        # Only on a real payload — the not-yet-scanned placeholder has none.
        _note_payload_shape(raw)
    current = _latest_event(history_events)
    status_id = _event_status_id(current) if current else None
    status = map_parcel_status(status_id)
    delivered = status is ParcelStatus.DELIVERED
    current_ts = to_iso_timestamp(current.get("statusDate")) if current else None

    mapped_events = [
        (event, map_event_status(_event_status_id(event)))
        for event in history_events
        if isinstance(event, dict)
    ]
    pickup = is_pickup(None, status, (mapped for _, mapped in mapped_events))
    pickup_event = _latest_event(
        [
            event
            for event, mapped in mapped_events
            if mapped is ParcelStatus.AT_PICKUP_POINT
        ]
    )
    planned_from, planned_to = planned_for_status(
        status,
        *planned_window(None, None, zone=dt_util.get_default_time_zone()),
    )

    return {
        "carrier": "Sameday",
        # Public payload carries no party names, weight or dimensions — the keys
        # exist for cross-carrier parity but stay None.
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": (current.get("statusState") if current else None) or status_id,
        "delivered": delivered,
        "delivered_at": current_ts if delivered else None,
        # No ETA in the public timeline: Sameday reports history, not a window.
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
        "pickup_point": (pickup_event or {}).get("transitLocation") or None
        if pickup
        else None,
        "url": tracking_url(tracking_code, country),
        "weight": None,
        "dimensions": None,
        "history": build_history(history_events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
