"""Constants for the Sameday parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "sameday"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Sameday's public track-by-AWB endpoint. No auth: the AWB is the only key.
#
#   GET https://api.sameday.{tld}/api/public/awb/{awb}/awb-history?_locale={locale}
#
# * Country switch = the **host TLD** (ro/hu/bg). Chosen once at setup and
#   stored in the entry data (``CONF_COUNTRY``); the API client is built with it.
# * content-type is a proper ``application/json`` (unlike DHL's text/plain), but
#   we still parse with ``content_type=None`` to be forgiving.
# * An unknown/not-yet-scanned AWB is signalled by **HTTP 404** with a JSON
#   body ``{"error":{"code":404,"message":"Awb-ul nu a fost gasit!"}}`` — the
#   client turns that into ``None``, never an error.
# * Alt host with the same ``{"awbHistory":[...]}`` shape (from the APK), kept
#   here for the record but not used:
#   ``GET https://recipients.sameday.ro/api/awbs/public/{awb}?culture=ro-RO``.
#
# No confirmed public per-AWB *web* page: Sameday's consumer tracking page path
# has not been captured, so each parcel's ``url`` field stays ``None`` rather
# than ship a fabricated deep link. Revisit once a real browser capture lands
# (see the pre-release "unconfirmed fields" warning).
TRACKING_API_URL = (
    "https://api.sameday.{country}/api/public/awb/{tracking_code}"
    "/awb-history?_locale={locale}"
)
TRACKING_URL: str | None = None

# Country selection. Sameday operates the same API on three national hosts; the
# TLD is the only thing that differs, and ``_locale`` follows it. Stored in the
# entry data and passed to the API client at construction.
CONF_COUNTRY = "country"
COUNTRY_OPTIONS = ("ro", "hu", "bg")
DEFAULT_COUNTRY = "ro"
# ``_locale`` query value per country (English is accepted everywhere, but the
# national locale returns the localised status text users expect).
COUNTRY_LOCALES = {"ro": "ro", "hu": "hu", "bg": "bg"}

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Refresh interval (minutes) controls how often the coordinator polls the
# carrier. Default 30 min keeps the load on a consumer endpoint gentle; the
# minimum is 15 min for the same reason.
#
# Deliberate divergence from the HA Core rule that polling intervals are not
# user-configurable: that rule targets core integrations, and in a HACS parcel
# tracker a tunable cadence is a wanted feature. Generate with
# ``--interval fixed`` instead when the carrier throttles or soft-bans unusual
# traffic — that drops the option entirely and hard-codes the cadence, so users
# cannot dial it down to something that gets them blocked.
CONF_REFRESH_INTERVAL = "refresh_interval"
REFRESH_INTERVAL_OPTIONS = (15, 30, 60, 120, 240)
DEFAULT_REFRESH_INTERVAL = 30

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
