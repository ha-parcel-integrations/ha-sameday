"""Tests for the Sameday API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.sameday.api import (
    SamedayApiClient,
    SamedayApiError,
)

CODE = "2SDAY0009999"


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_payload_on_success():
    session = _session_returning(200, {"awbHistory": [{"statusStateId": "5"}]})
    client = SamedayApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["awbHistory"][0]["statusStateId"] == "5"
    # the AWB ends up in the URL
    assert CODE in session.get.call_args[0][0]


async def test_country_selects_host_and_locale():
    """The country picks the national host TLD and the _locale query value."""
    session = _session_returning(200, {"awbHistory": []})
    client = SamedayApiClient(session, "hu")

    await client.async_get_parcel(CODE)

    url = session.get.call_args[0][0]
    assert "api.sameday.hu" in url
    assert "_locale=hu" in url


async def test_get_parcel_returns_none_when_not_found():
    """An unknown or not-yet-scanned AWB answers HTTP 404 — a normal state."""
    client = SamedayApiClient(
        _session_returning(404, {"error": {"code": 404, "message": "nu a fost gasit"}})
    )
    assert await client.async_get_parcel("2SDAY0000000") is None


async def test_get_parcel_returns_none_on_missing_history():
    """A 200 without an awbHistory list is treated as unknown, not a crash."""
    client = SamedayApiClient(_session_returning(200, {"foo": "bar"}))
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_raises_on_error_status():
    client = SamedayApiClient(_session_returning(500, {}))
    with pytest.raises(SamedayApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_unparseable_body():
    client = SamedayApiClient(_session_returning(200, "not json"))
    with pytest.raises(SamedayApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = SamedayApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(SamedayApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = SamedayApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
