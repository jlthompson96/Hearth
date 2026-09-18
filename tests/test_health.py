"""Phase 0 exit criterion, as a test."""

from fastapi.testclient import TestClient

from api import __version__
from api.main import app


def test_health_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_health_needs_no_configuration() -> None:
    """/health must answer on a machine with no .env, no Postgres and no model
    server — otherwise it is useless as the thing you check when those break."""
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_openapi_schema_is_generated() -> None:
    """The TypeScript types are generated from this document; if it stops
    building, the UI's types silently go stale."""
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "Hearth"
    assert "Health" in schema["components"]["schemas"]
