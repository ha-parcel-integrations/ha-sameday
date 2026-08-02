# Sameday Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-sameday.svg)](https://github.com/ha-parcel-integrations/ha-sameday/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Sameday](https://www.sameday.ro) parcels — the Romanian courier and *easybox* locker network, also operating in Hungary and Bulgaria (RO/HU/BG). No account is needed: you pick your country once and enter the AWB (tracking number) yourself, just like on the Sameday tracking page.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

> ### ⚠️ Early release — the success payload is not yet confirmed
>
> The endpoint is live and keyless, and unknown or not-yet-scanned AWBs are
> handled correctly. What has **not yet been seen from a real parcel** is a
> success response: its field names and the numeric `statusStateId` status map
> are reconstructed from Sameday's official Android app. Anything unmapped
> reports **`unknown`** (never a wrong status) and logs a one-shot warning with a
> ready-made issue link — as does any payload field we have not confirmed yet.
> Please [report it](https://github.com/ha-parcel-integrations/ha-sameday/issues/new?template=unrecognised_status.yml)
> so the mapping can be completed.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Sameday parcels by AWB — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `at_pickup_point` / `delivered` / …), the carrier's own status text and the current location
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar
- `sameday.track_parcel` / `sameday.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history — Sameday returns the full timeline in the same call
- Manual refresh button and a diagnostic last-update sensor

> **No ETA:** Sameday's public tracking exposes a status timeline, not a
> predicted delivery window. The next-delivery sensor and the calendar therefore
> stay empty, and no `delivery_time_changed` event fires.

## Requirements

- Home Assistant 2024.7 or newer
- A Sameday parcel and its AWB / tracking number (from the shipping
  confirmation email or the locker/pickup notification) — no account needed
- The country the parcel ships in: Romania, Hungary or Bulgaria

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-sameday` as an **Integration**.
3. Install **Sameday** and restart Home Assistant.

### Manual

Copy `custom_components/sameday` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Sameday**. The only question is your country (Romania, Hungary or Bulgaria) — the same tracking runs on all three national networks, and the choice picks the right host. It is fixed for the life of the entry; to switch countries, remove and re-add the integration.

Then add parcels via the integration's **Configure** dialog, the [`sameday.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The AWB is on your shipping confirmation email or the locker/pickup notification.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |
| Polling | Refresh every | 30 min | How often Sameday is checked. Slower is gentler on their API. |

## Removal

Standard HA removal applies: **Settings → Devices & Services → Sameday → ⋮ → Delete**. Nothing is stored on Sameday's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.sameday_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.sameday_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.sameday_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.sameday_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.sameday_last_successful_update` | Diagnostic: when Sameday was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Order placed / picked up by Sameday |
| `in_transit` | In the sorting network or being redirected |
| `out_for_delivery` | With the courier today |
| `at_pickup_point` | Loaded in an easybox / waiting at a pickup point |
| `delivered` | Delivered |
| `returning` | Going back to the sender |
| `problem` | Sameday reports a failed delivery, cancellation or exception |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Sameday device):

| Event | When |
|---|---|
| `sameday_parcel_registered` | A new parcel appears in the active list |
| `sameday_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `sameday_parcel_delivered` | A parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up. (Sameday exposes no ETA, so no `delivery_time_changed` event fires.)

## Services

| Service | Fields | Description |
|---|---|---|
| `sameday.track_parcel` | `tracking_code` | Start tracking a parcel |
| `sameday.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.sameday: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Sameday has not scanned it yet (their API answers HTTP 404 until the first scan), or the AWB is wrong, or its country does not match the one you set up. It will pick up automatically once scanned.
- **A status logs "Unrecognised Sameday status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-sameday/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of Dutch
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public tracking endpoint as the Sameday consumer website. It is not affiliated with, endorsed by, or supported by Sameday. Be gentle with the polling interval.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
