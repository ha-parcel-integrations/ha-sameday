"""Sameday public track-by-AWB API client.

The contract the coordinator relies on:

* ``async_get_parcel`` returns the raw ``{"awbHistory": [...]}`` dict on success,
* returns ``None`` when the AWB is unknown or not yet scanned (HTTP 404 — a
  normal, expected state, never an error),
* raises :class:`SamedayApiError` for anything else,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import COUNTRY_LOCALES, DEFAULT_COUNTRY, TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)


class SamedayApiError(Exception):
    """Raised when a Sameday API call returns an unexpected response."""

    def __init__(self, detail: str) -> None:
        """Store the status code that triggered the error."""
        super().__init__(f"Sameday API request failed: {detail}")
        self.detail = detail


class SamedayApiClient:
    """Client for the public Sameday track-by-AWB endpoint.

    No authentication: the endpoint is keyed on the AWB (tracking code) alone.
    The ``country`` selects the national host (ro/hu/bg) and its ``_locale``;
    it is fixed for the entry's lifetime and passed in at construction.

    A known AWB answers HTTP 200 with ``{"awbHistory": [ {...}, ... ]}`` — the
    full envelope is returned untouched and mapped in :mod:`.parcels`. An
    unknown or not-yet-scanned AWB answers HTTP 404 (JSON body
    ``{"error":{"code":404,...}}``) and yields ``None``.
    """

    def __init__(
        self, session: aiohttp.ClientSession, country: str = DEFAULT_COUNTRY
    ) -> None:
        """Initialise the client with an aiohttp session and a country host."""
        self._session = session
        self._country = country
        self._locale = COUNTRY_LOCALES.get(country, country)

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's AWB history.

        Returns the raw ``{"awbHistory": [...]}`` envelope for a known AWB, or
        ``None`` when the endpoint reports it as unknown (HTTP 404) — which is
        also what a not-yet-scanned AWB gets. Any other non-2xx status raises
        :class:`SamedayApiError`; network errors propagate as
        ``aiohttp.ClientError``.
        """
        url = TRACKING_API_URL.format(
            country=self._country,
            tracking_code=tracking_code,
            locale=self._locale,
        )
        async with self._session.get(url) as response:
            if response.status == 404:
                # Unknown or not-yet-scanned AWB — a normal, expected state.
                return None
            if response.status != 200:
                raise SamedayApiError(f"HTTP {response.status}")
            try:
                # content_type=None: be forgiving if the host ever mislabels
                # the JSON body's content type.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise SamedayApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise SamedayApiError("unexpected body (not a JSON object)")

        history = payload.get("awbHistory")
        if not isinstance(history, list):
            # A 200 without an awbHistory list is not something we can map;
            # treat it as unknown rather than crashing the whole poll.
            _LOGGER.warning(
                "Sameday returned 200 without an awbHistory list for %s",
                tracking_code,
            )
            return None
        return payload
