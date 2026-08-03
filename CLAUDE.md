# Working in this repository

Home Assistant custom integration for **Sameday** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific notes

Sameday is a **Romanian courier + easybox locker network — 100% parcels**, also
operating in HU and BG. Keyless **track-by-AWB**; no account. **Status:
unverified against a real parcel** — the success payload's field names and the
numeric `statusStateId` map are reconstructed from Sameday's official Android APK
(see **`carrier-research/api/sameday/`**); the map is best-effort by design
(unknown ids → `unknown` + one-shot warning).

- **Country is a hub-level setting** stored in `entry.data[CONF_COUNTRY]`
  (ro/hu/bg), chosen once in the config flow and passed to `SamedayApiClient` at
  construction — it fixes the host TLD (`api.sameday.{country}`) and the
  `_locale`. It is **not** per-parcel and cannot change without re-adding the
  entry. `device.py`'s `configuration_url` follows it.
- **Current status = newest `awbHistory` entry.** `_latest_event` sorts the
  timeline by `statusDate` descending; element[0] is the current status. An
  empty/absent timeline (the not-yet-scanned placeholder) stays `unknown`.
- **404 is not-found, not an error** — `api.py` maps HTTP 404 → `None` (unknown
  or not-yet-scanned AWB); a 200 without an `awbHistory` list is also treated as
  unknown (warn), anything else raises `SamedayApiError`. `aiohttp.ClientError`
  is caught per parcel in the gather loop, never around the whole update.
- **No ETA** — `planned_from`/`planned_to` always `None`, so the next-delivery
  sensor and calendar stay inert and `sameday_parcel_delivery_time_changed` never
  fires (machinery kept for parity, exercised white-box in `test_coordinator.py`).
- **`None` on purpose:** `sender`, `receiver`, `weight`, `dimensions` (absent from
  the public payload). `url` **is** a per-country deep link
  (`COUNTRY_TRACKING_URLS`, threaded via `normalize_parcel(country=…)` from the
  coordinator's `_country`) — RO/BG confirmed, HU (`#awb` fragment) best-effort.
  `pickup_point` **is** populated, from the current event's `transitLocation`,
  but only when `status is AT_PICKUP_POINT`.
- **History status is mapped** — unlike some siblings, `awbHistory` events carry a
  `statusStateId`, so history entries reuse `_STATUS_MAP` (don't extend the map
  per event).
- **Pre-1.0 field watch:** `_note_payload_shape` logs, once, any top-level or
  `awbHistory` event key beyond the confirmed sets (`_KNOWN_TOP_LEVEL_KEYS` /
  `_KNOWN_EVENT_KEYS`) — keys only, never values (a `transitLocation` can be
  personal). Diagnostics redact `transitLocation`/`county`/`pickup_point`/AWB;
  transit `country` stays.

**API mechanics live in `carrier-research/api/sameday/` (private research repo)**
— the endpoint, params, content type, 404 signalling, the per-country tracking
pages and the full 28-value `statusStateId` → `ParcelStatus` vocabulary. Do not
duplicate them here. The research narrative (how the endpoint was found, and
why it is keyless) is one level up in `carrier-research/sameday.md`.

## Options and reloads

The options flow is one sectioned form (`data_entry_flow.section`); changes apply
without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default) apply changes live: an update listener
  retunes `coordinator.update_interval` and calls `async_request_refresh()`, so
  added/removed parcel sensors appear immediately.
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

The user-tunable poll interval is a deliberate HACS divergence (see
CONVENTIONS.md); a carrier that throttles is generated with a fixed cadence and no
polling option at all.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Tests on Windows

`tests/conftest.py` carries two Windows-only shims (no-ops elsewhere):
`disable_socket` is neutralised (Windows event loops need AF_INET socketpairs;
the 127.0.0.1 allowlist stays) and HA's `AsyncResolver` is swapped for
`ThreadedResolver` (aiodns refuses the Proactor loop). Do not remove them
"because CI passes" — CI is Linux, development is Windows.

## Running tests

```
python -m pytest tests/ --cov=custom_components.sameday
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file in the same commit;
the API reference lives in the private `carrier-research/api/sameday/`,
not in this repo.
