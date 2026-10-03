from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from personal_travel.api.dependencies import session_dependency
from personal_travel.api.middleware import MAX_REQUEST_BYTES
from personal_travel.api.schemas import ItemCreate, PlaceCreate, PlaceUpdate, SavedPlaceCreate
from personal_travel.main import app
from personal_travel.services.errors import DomainError
from personal_travel.services.time_utils import get_zoneinfo


def test_invalid_timezone_paths_fail_as_domain_errors() -> None:
    for zone in ("/etc/passwd", "../UTC", "", "not/a/timezone"):
        with pytest.raises(DomainError) as error:
            get_zoneinfo(zone)
        assert error.value.code == "invalid_timezone"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://u:p@example.test",
        "https://example.test:bad",
        "https://example.test/a\nb",
    ],
)
def test_website_urls_are_safe(url: str) -> None:
    with pytest.raises(ValidationError):
        PlaceCreate(name="Unsafe", website_url=url)


def test_coordinate_patch_and_notes_are_bounded() -> None:
    with pytest.raises(ValidationError):
        PlaceUpdate(latitude=1, longitude=None)
    assert PlaceUpdate(latitude=None, longitude=None).latitude is None
    with pytest.raises(ValidationError):
        ItemCreate(title="Too much", notes="x" * 10001)
    with pytest.raises(ValidationError):
        SavedPlaceCreate(place_id=uuid4(), note="x" * 1001)


def test_local_boundary_blocks_host_origin_and_oversized_body_before_db() -> None:
    with TestClient(app, base_url="http://localhost") as client:
        assert (
            client.post("/v1/trips", headers={"Origin": "https://evil.test"}, json={}).status_code
            == 403
        )
        for host in ("evil.test", "localhost/path", "localhost?x", "localhost#x", "localhost:bad"):
            assert client.get("/health", headers={"Host": host}).status_code == 400
        assert client.get("/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.post("/v1/trips", content=b"x" * (MAX_REQUEST_BYTES + 1)).status_code == 413
        healthy = client.get("/health")
        assert healthy.status_code == 200
        assert healthy.headers["Cache-Control"] == "no-store"
        assert healthy.headers["X-Request-ID"]
        assert (
            client.options(
                "/v1/trips",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                },
            ).status_code
            == 200
        )


def test_validation_does_not_echo_private_input_or_exception_objects() -> None:
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/v1/places", json={"name": "PRIVATE BOOKING", "latitude": 91})
        assert response.status_code == 422
        assert "PRIVATE BOOKING" not in response.text
        assert "input" not in response.json()["error"]["details"]["fields"][0]


def test_database_unavailability_has_safe_envelope() -> None:
    def unavailable():
        raise OperationalError("SECRET SQL", {}, RuntimeError("SECRET CREDENTIAL"))

    app.dependency_overrides[session_dependency] = unavailable
    try:
        with TestClient(app, base_url="http://localhost") as client:
            response = client.get("/ready")
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "database_unavailable"
            assert "SECRET" not in response.text
    finally:
        app.dependency_overrides.pop(session_dependency, None)


def test_unexpected_errors_do_not_escape_to_server_tracebacks() -> None:
    def unavailable():
        raise RuntimeError("PRIVATE QUERY AND CREDENTIAL")

    app.dependency_overrides[session_dependency] = unavailable
    try:
        with TestClient(app, base_url="http://localhost") as client:
            response = client.get("/ready")
            assert response.status_code == 500
            assert response.json()["error"]["code"] == "internal_error"
            assert response.headers["X-Request-ID"]
            assert "PRIVATE" not in response.text
    finally:
        app.dependency_overrides.pop(session_dependency, None)
