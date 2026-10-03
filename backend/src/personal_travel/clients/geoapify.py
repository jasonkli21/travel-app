from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from personal_travel.api.schemas import GeoapifyRouteMode
from personal_travel.config import get_settings


class GeoapifyClientError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class GeoapifyPlace:
    provider_place_id: str
    name: str
    address: str | None
    category: str | None
    latitude: float
    longitude: float
    provider_source_name: str
    provider_source_attribution: str
    provider_source_license: str | None
    provider_source_url: str | None


@dataclass(frozen=True)
class GeoapifyRouteLeg:
    duration_seconds: int
    distance_meters: float
    geometry: list[list[float]]


class GeoapifyClient:
    """Typed HTTP boundary for Geoapify's geocoding and routing APIs."""

    base_url = "https://api.geoapify.com/v1"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        configured_key = settings.geoapify_api_key
        self._api_key = api_key or (
            configured_key.get_secret_value() if configured_key is not None else None
        )
        self._timeout = (
            timeout_seconds if timeout_seconds is not None else settings.geoapify_timeout_seconds
        )
        self._transport = transport

    async def search_places(
        self,
        *,
        query: str,
        limit: int,
        bias: tuple[float, float] | None = None,
    ) -> list[GeoapifyPlace]:
        params: dict[str, str | int] = {
            "text": query,
            "format": "json",
            "limit": limit,
        }
        if bias is not None:
            latitude, longitude = bias
            # Geoapify geocoding proximity uses longitude,latitude order.
            params["bias"] = f"proximity:{longitude},{latitude}"
        payload = await self._get("/geocode/search", params)
        results = payload.get("results")
        if not isinstance(results, list):
            raise self._invalid_response()

        places: list[GeoapifyPlace] = []
        for result in results[:limit]:
            place = self._parse_place(result)
            if place is not None:
                places.append(place)
        return places

    async def route(
        self,
        *,
        waypoints: list[tuple[float, float]],
        mode: GeoapifyRouteMode,
    ) -> list[GeoapifyRouteLeg]:
        if len(waypoints) < 2:
            return []
        encoded_waypoints = "|".join(f"{latitude},{longitude}" for latitude, longitude in waypoints)
        payload = await self._get(
            "/routing",
            {
                "waypoints": encoded_waypoints,
                "mode": mode,
                "intermediate_waypoint_mode": "stopover",
                "format": "geojson",
            },
        )
        features = payload.get("features")
        if not isinstance(features, list) or not features or not isinstance(features[0], dict):
            raise self._invalid_response()
        feature = features[0]
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            raise self._invalid_response()

        raw_legs = properties.get("legs")
        if not isinstance(raw_legs, list) or len(raw_legs) != len(waypoints) - 1:
            raise self._invalid_response()
        raw_geometry = geometry.get("coordinates")
        route_lines = self._parse_route_geometry(geometry.get("type"), raw_geometry, len(raw_legs))

        legs: list[GeoapifyRouteLeg] = []
        for raw_leg, coordinates in zip(raw_legs, route_lines, strict=True):
            if not isinstance(raw_leg, dict):
                raise self._invalid_response()
            duration = self._finite_number(raw_leg.get("time"))
            distance = self._finite_number(raw_leg.get("distance"))
            if duration is None or duration < 0 or distance is None or distance < 0:
                raise self._invalid_response()
            legs.append(
                GeoapifyRouteLeg(
                    duration_seconds=math.ceil(duration),
                    distance_meters=distance,
                    geometry=coordinates,
                )
            )
        return legs

    async def _get(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        if not self._api_key:
            raise GeoapifyClientError(
                "location_provider_not_configured",
                "Location services are not configured. Add a Geoapify API key to continue.",
                status_code=503,
            )
        request_params = {**params, "apiKey": self._api_key}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(f"{self.base_url}{path}", params=request_params)
                response.raise_for_status()
        except httpx.TimeoutException:
            raise GeoapifyClientError(
                "location_provider_timeout",
                "The location provider did not respond in time. Try again.",
                status_code=503,
            ) from None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise GeoapifyClientError(
                    "location_provider_rate_limited",
                    "The location provider is busy. Wait a moment and try again.",
                    status_code=503,
                ) from None
            raise GeoapifyClientError(
                "location_provider_unavailable",
                "The location provider could not complete the request. Try again later.",
                status_code=502,
            ) from None
        except httpx.HTTPError:
            raise GeoapifyClientError(
                "location_provider_unavailable",
                "The location provider could not be reached. Try again later.",
                status_code=503,
            ) from None

        try:
            payload = response.json()
        except ValueError:
            raise self._invalid_response() from None
        if not isinstance(payload, dict):
            raise self._invalid_response()
        return payload

    @classmethod
    def _parse_place(cls, result: object) -> GeoapifyPlace | None:
        if not isinstance(result, dict):
            return None
        place_id = result.get("place_id")
        name = result.get("name") or result.get("address_line1") or result.get("formatted")
        datasource = result.get("datasource")
        latitude = cls._finite_number(result.get("lat"))
        longitude = cls._finite_number(result.get("lon"))
        if (
            not isinstance(place_id, str)
            or not place_id
            or not isinstance(name, str)
            or not name.strip()
            or latitude is None
            or longitude is None
            or not isinstance(datasource, dict)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            return None

        source_name = datasource.get("sourcename")
        source_attribution = datasource.get("attribution")
        if (
            not isinstance(source_name, str)
            or not source_name.strip()
            or not isinstance(source_attribution, str)
            or not source_attribution.strip()
        ):
            return None
        source_license = datasource.get("license")
        if not isinstance(source_license, str) or not source_license.strip():
            source_license = None
        source_url = datasource.get("url")
        if isinstance(source_url, str) and source_url.strip():
            try:
                parsed_source_url = urlsplit(source_url.strip())
            except ValueError:
                source_url = None
            else:
                if (
                    parsed_source_url.scheme not in {"http", "https"}
                    or not parsed_source_url.netloc
                ):
                    source_url = None
        else:
            source_url = None

        formatted = result.get("formatted")
        address = formatted.strip() if isinstance(formatted, str) and formatted.strip() else None
        categories = result.get("categories")
        category: str | None = None
        if isinstance(categories, list):
            category = next(
                (value.strip() for value in categories if isinstance(value, str) and value.strip()),
                None,
            )
        raw_category = result.get("category")
        if category is None and isinstance(raw_category, str):
            category = raw_category.strip() or None

        normalized_source_name = source_name.strip()
        normalized_attribution = source_attribution.strip()
        normalized_license = source_license.strip() if source_license else None
        normalized_source_url = source_url.strip() if isinstance(source_url, str) else None
        if (
            len(place_id) > 256
            or len(normalized_source_name) > 128
            or len(normalized_attribution) > 500
            or (normalized_license is not None and len(normalized_license) > 255)
            or (normalized_source_url is not None and len(normalized_source_url) > 500)
        ):
            return None
        return GeoapifyPlace(
            provider_place_id=place_id,
            name=name.strip()[:240],
            address=address[:500] if address else None,
            category=category[:120] if category else None,
            latitude=latitude,
            longitude=longitude,
            provider_source_name=normalized_source_name,
            provider_source_attribution=normalized_attribution,
            provider_source_license=normalized_license,
            provider_source_url=normalized_source_url,
        )

    @classmethod
    def _parse_route_geometry(
        cls,
        geometry_type: object,
        raw_geometry: object,
        expected_legs: int,
    ) -> list[list[list[float]]]:
        if not isinstance(raw_geometry, list):
            raise cls._invalid_response()
        if geometry_type == "LineString" and expected_legs == 1:
            raw_lines: list[object] = [raw_geometry]
        elif geometry_type == "MultiLineString":
            raw_lines = raw_geometry
        else:
            raise cls._invalid_response()
        if len(raw_lines) != expected_legs:
            raise cls._invalid_response()

        lines: list[list[list[float]]] = []
        for raw_line in raw_lines:
            if not isinstance(raw_line, list) or len(raw_line) < 2:
                raise cls._invalid_response()
            points: list[list[float]] = []
            for raw_point in raw_line:
                if not isinstance(raw_point, list) or len(raw_point) < 2:
                    raise cls._invalid_response()
                longitude = cls._finite_number(raw_point[0])
                latitude = cls._finite_number(raw_point[1])
                if (
                    longitude is None
                    or latitude is None
                    or not -180 <= longitude <= 180
                    or not -90 <= latitude <= 90
                ):
                    raise cls._invalid_response()
                points.append([longitude, latitude])
            lines.append(points)
        return lines

    @staticmethod
    def _finite_number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @staticmethod
    def _invalid_response() -> GeoapifyClientError:
        return GeoapifyClientError(
            "location_provider_invalid_response",
            "The location provider returned data the travel app could not use.",
            status_code=502,
        )
