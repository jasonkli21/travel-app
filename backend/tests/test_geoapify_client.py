from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import personal_travel.clients.geoapify as geoapify_module
from personal_travel.clients.geoapify import GeoapifyClient, GeoapifyClientError


@pytest.mark.asyncio
async def test_search_normalizes_provider_results_and_biases_to_trip_coordinates() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "place_id": "osm:node:123",
                        "name": "Museum",
                        "formatted": "Museum, Example City",
                        "categories": ["tourism.museum"],
                        "lat": 37.75,
                        "lon": -122.4,
                        "datasource": {
                            "sourcename": "openstreetmap",
                            "attribution": "© OpenStreetMap contributors",
                            "license": "Open Database License",
                            "url": "https://www.openstreetmap.org/copyright",
                        },
                    },
                    {"place_id": "bad-result", "name": "No coordinates"},
                ]
            },
        )

    client = GeoapifyClient(
        api_key="test-key",
        transport=httpx.MockTransport(respond),
    )
    results = await client.search_places(
        query="Museum",
        limit=10,
        bias=(37.7, -122.3),
    )

    assert len(results) == 1
    assert results[0].provider_place_id == "osm:node:123"
    assert results[0].category == "tourism.museum"
    assert results[0].provider_source_name == "openstreetmap"
    assert results[0].provider_source_attribution == "© OpenStreetMap contributors"
    assert results[0].provider_source_license == "Open Database License"
    assert results[0].provider_source_url == "https://www.openstreetmap.org/copyright"
    assert requests[0].url.params["text"] == "Museum"
    assert requests[0].url.params["limit"] == "10"
    assert requests[0].url.params["bias"] == "proximity:-122.3,37.7"
    assert requests[0].url.params["apiKey"] == "test-key"


@pytest.mark.asyncio
async def test_route_parses_each_leg_duration_distance_and_geometry() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "legs": [
                                {"time": 60.1, "distance": 400},
                                {"time": 120, "distance": 800.5},
                            ]
                        },
                        "geometry": {
                            "type": "MultiLineString",
                            "coordinates": [
                                [[-122.4, 37.7], [-122.3, 37.8]],
                                [[-122.3, 37.8], [-122.2, 37.9]],
                            ],
                        },
                    }
                ],
            },
        )

    client = GeoapifyClient(api_key="test-key", transport=httpx.MockTransport(respond))
    legs = await client.route(
        waypoints=[(37.7, -122.4), (37.8, -122.3), (37.9, -122.2)],
        mode="walk",
    )

    assert [leg.duration_seconds for leg in legs] == [61, 120]
    assert [leg.distance_meters for leg in legs] == [400, 800.5]
    assert legs[1].geometry == [[-122.3, 37.8], [-122.2, 37.9]]


@pytest.mark.asyncio
async def test_provider_rate_limit_maps_to_safe_unavailable_error() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "account details are private"})

    client = GeoapifyClient(api_key="test-key", transport=httpx.MockTransport(respond))

    with pytest.raises(GeoapifyClientError) as error:
        await client.search_places(query="Museum", limit=10)

    assert error.value.status_code == 503
    assert error.value.code == "location_provider_rate_limited"
    assert "private" not in error.value.message


@pytest.mark.asyncio
async def test_missing_provider_key_is_reported_without_an_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        geoapify_module,
        "get_settings",
        lambda: SimpleNamespace(geoapify_api_key=None, geoapify_timeout_seconds=8.0),
    )
    client = GeoapifyClient()

    with pytest.raises(GeoapifyClientError) as error:
        await client.search_places(query="Museum", limit=10)

    assert error.value.status_code == 503
    assert error.value.code == "location_provider_not_configured"
