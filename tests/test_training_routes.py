"""The training half of Manual entry, end to end through FastAPI.

Weights arrive as typed text and are read by the same strict parser rules as a
dollar amount: exactly, or refused by the shape of what was typed. They travel
back as exact decimal strings, in pounds.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from api.main import app
from api.routes import training as training_route


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, conn: sa.Connection) -> TestClient:
    @contextmanager
    def _same() -> Iterator[sa.Connection]:
        yield conn

    monkeypatch.setattr(training_route, "readonly_connection", _same)
    monkeypatch.setattr(training_route, "writer_connection", _same)
    return TestClient(app)


SESSION = {
    "performed_on": "2026-09-21",
    "kind": "strength",
    "sets": [
        {"exercise": "Back Squat", "reps": 5, "weight": "225"},
        {"exercise": "back squat", "reps": 5, "weight": "235.5"},
        {"exercise": "pull-up", "reps": 8, "weight": None},
    ],
}


def test_the_listing_says_the_unit_and_offers_the_logged_names(client: TestClient) -> None:
    assert client.post("/api/workouts", json=SESSION).status_code == 201

    listing = client.get("/api/training").json()

    assert listing["unit"] == "lb"
    assert listing["fixture_loaded"] is False
    assert "strength" in listing["kinds"]
    assert listing["exercises"] == ["back squat", "pull-up"]


def test_a_workout_comes_back_with_exact_weights_in_pounds(client: TestClient) -> None:
    response = client.post("/api/workouts", json=SESSION)

    assert response.status_code == 201, response.text
    sets = response.json()["sets"]
    assert [(s["exercise"], s["set_number"], s["weight"]) for s in sets] == [
        ("back squat", 1, "225.000"),
        ("back squat", 2, "235.500"),
        ("pull-up", 1, None),
    ]


def test_a_weight_that_cannot_be_stored_as_typed_is_refused_by_its_shape(
    client: TestClient,
) -> None:
    bad = {**SESSION, "sets": [{"exercise": "back squat", "reps": 5, "weight": "225.1234"}]}

    response = client.post("/api/workouts", json=bad)

    assert response.status_code == 422
    body = response.json()
    assert body["kind"] == "refused"
    assert "Set 1" in body["message"] and "###.####" in body["message"]
    assert client.get("/api/training").json()["workouts"] == []


def test_the_date_is_required_at_the_door(client: TestClient) -> None:
    undated = {k: v for k, v in SESSION.items() if k != "performed_on"}

    assert client.post("/api/workouts", json=undated).status_code == 422


def test_a_workout_can_be_removed(client: TestClient) -> None:
    workout = client.post("/api/workouts", json=SESSION).json()

    assert client.delete(f"/api/workouts/{workout['id']}").status_code == 204
    assert client.get("/api/training").json()["workouts"] == []


def test_body_weight_is_recorded_exactly_and_a_second_for_the_day_is_a_409(
    client: TestClient,
) -> None:
    first = client.post("/api/body-weights", json={"as_of": "2026-09-21", "weight": "181.4"})
    assert first.status_code == 201, first.text
    assert first.json()["weight"] == "181.400"

    again = client.post("/api/body-weights", json={"as_of": "2026-09-21", "weight": "180"})
    assert again.status_code == 409
    assert again.json()["kind"] == "conflict"


def test_a_body_weight_can_be_removed(client: TestClient) -> None:
    entry = client.post("/api/body-weights", json={"as_of": "2026-09-21", "weight": "181.4"})

    assert client.delete(f"/api/body-weights/{entry.json()['id']}").status_code == 204
    assert client.get("/api/training").json()["body_weights"] == []
