"""The Data & imports and Manual entry routes, end to end through FastAPI.

The routes' connections are pointed at the test's own rolled-back connection,
and the data folder at a temporary directory holding a fake export. What is
checked is the layer's own job: that a refusal reaches the browser as a `kind`
and a readable message with the right status, that figures travel as exact
decimal strings, and that nothing but a bare filename reaches the importer.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

import config
from api.main import app
from api.routes import accounts as accounts_route
from api.routes import imports as imports_route
from tests.fake_exports import FILENAME, HEADER, export


def _bind(monkeypatch: pytest.MonkeyPatch, conn: sa.Connection) -> None:
    @contextmanager
    def _same() -> Iterator[sa.Connection]:
        yield conn

    for route in (imports_route, accounts_route):
        monkeypatch.setattr(route, "readonly_connection", _same)
        monkeypatch.setattr(route, "writer_connection", _same)


@pytest.fixture
def folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    (tmp_path / FILENAME).write_bytes(export())
    monkeypatch.setenv("HEARTH_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield tmp_path
    monkeypatch.undo()
    config.get_settings.cache_clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, conn: sa.Connection, folder: Path) -> TestClient:
    _bind(monkeypatch, conn)
    return TestClient(app)


def _accounts(client: TestClient) -> None:
    for label, kind in (("Joint Brokerage", "brokerage"), ("Roth IRA", "retirement")):
        assert client.post("/api/accounts", json={"label": label, "kind": kind}).status_code == 201


def _import(client: TestClient) -> dict[str, object]:
    response = client.post("/api/imports", json={"filename": FILENAME, "as_of": "2026-09-21"})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_the_folder_listing_offers_the_date_in_the_name(client: TestClient) -> None:
    listing = client.get("/api/imports/files").json()

    assert listing["problem"] is None
    assert listing["fixture_loaded"] is False
    [file] = listing["files"]
    assert file["name"] == FILENAME
    assert file["date_in_name"] == "2026-09-21"
    assert file["imported_batch"] is None


def test_an_import_answers_with_exact_decimal_strings(client: TestClient) -> None:
    """Figures travel as strings. A JSON number is a float by the time the
    browser has it."""
    _accounts(client)

    outcome = _import(client)

    assert outcome["already_imported"] is False
    assert outcome["accounts"] == [
        {"label": "Joint Brokerage", "balance": "25075.00", "holdings": 3},
        {"label": "Roth IRA", "balance": "21600.00", "holdings": 1},
    ]


def test_an_imported_file_is_marked_and_a_second_import_is_a_no_op(client: TestClient) -> None:
    _accounts(client)
    first = _import(client)

    second = _import(client)
    [file] = client.get("/api/imports/files").json()["files"]
    [batch] = client.get("/api/imports").json()

    assert second["already_imported"] is True
    assert file["imported_batch"] == first["batch_id"] == batch["id"]
    assert batch["status"] == "normalized"


def test_the_date_is_required_at_the_door(client: TestClient) -> None:
    response = client.post("/api/imports", json={"filename": FILENAME})

    assert response.status_code == 422


@pytest.mark.parametrize("name", ["../" + FILENAME, "sub/" + FILENAME])
def test_a_path_is_refused_as_a_data_folder_problem(client: TestClient, name: str) -> None:
    response = client.post("/api/imports", json={"filename": name, "as_of": "2026-09-21"})

    assert response.status_code == 409
    assert response.json()["kind"] == "data_dir"


def test_an_unknown_layout_is_a_422_with_a_readable_message(
    client: TestClient, folder: Path
) -> None:
    (folder / "other.csv").write_bytes(export(header=HEADER.replace("Symbol", "Ticker")))

    response = client.post("/api/imports", json={"filename": "other.csv", "as_of": "2026-09-21"})

    assert response.status_code == 422
    assert response.json()["kind"] == "unknown_layout"
    assert "Ticker" in response.json()["message"]


def test_unknown_accounts_come_back_named(client: TestClient) -> None:
    response = client.post("/api/imports", json={"filename": FILENAME, "as_of": "2026-09-21"})

    assert response.status_code == 422
    assert response.json()["kind"] == "unknown_accounts"
    assert "Joint Brokerage" in response.json()["message"]


def test_an_import_can_be_taken_back_out(client: TestClient, conn: sa.Connection) -> None:
    _accounts(client)
    batch_id = _import(client)["batch_id"]

    assert client.delete(f"/api/imports/{batch_id}").status_code == 204
    assert conn.execute(sa.text("select count(*) from balance_snapshot")).scalar_one() == 0
    assert client.delete(f"/api/imports/{batch_id}").status_code == 404


def test_a_fixture_database_is_a_409(
    monkeypatch: pytest.MonkeyPatch, seeded: sa.Connection, folder: Path
) -> None:
    _bind(monkeypatch, seeded)
    client = TestClient(app)

    assert client.get("/api/imports/files").json()["fixture_loaded"] is True
    response = client.post("/api/imports", json={"filename": FILENAME, "as_of": "2026-09-21"})
    assert response.status_code == 409
    assert response.json()["kind"] == "fixture_loaded"


def test_an_unset_data_folder_is_reported_not_thrown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEARTH_DATA_DIR", "")
    config.get_settings.cache_clear()

    listing = client.get("/api/imports/files").json()

    assert "HEARTH_DATA_DIR is not set" in listing["problem"]
    assert listing["files"] == []


# --- manual entry ----------------------------------------------------------------


def test_a_balance_typed_as_us_currency_is_recorded_exactly(client: TestClient) -> None:
    client.post("/api/accounts", json={"label": "Credit Card", "kind": "credit"})

    response = client.post(
        "/api/balances",
        json={"account": "Credit Card", "as_of": "2026-09-21", "balance": "-$1,234.56"},
    )
    listing = client.get("/api/accounts").json()

    assert response.status_code == 201
    assert response.json()["balance"] == "-1234.56"
    [account] = listing["accounts"]
    assert (account["latest_as_of"], account["latest_balance"]) == ("2026-09-21", "-1234.56")
    assert [e["label"] for e in listing["recent"]] == ["Credit Card"]


def test_a_balance_that_is_not_us_currency_is_refused_by_its_shape(client: TestClient) -> None:
    client.post("/api/accounts", json={"label": "Savings", "kind": "savings"})

    response = client.post(
        "/api/balances",
        json={"account": "Savings", "as_of": "2026-09-21", "balance": "1.234,56"},
    )

    assert response.status_code == 422
    assert "#.###,##" in response.json()["message"]


def test_a_label_carrying_digits_is_refused_through_the_api(client: TestClient) -> None:
    response = client.post("/api/accounts", json={"label": "Checking 1234", "kind": "checking"})

    assert response.status_code == 422
    assert response.json()["kind"] == "account_number"
    assert "1234" not in response.json()["message"]


def test_a_hand_entered_balance_can_be_removed_through_the_api(client: TestClient) -> None:
    client.post("/api/accounts", json={"label": "Savings", "kind": "savings"})
    entry = client.post(
        "/api/balances", json={"account": "Savings", "as_of": "2026-09-21", "balance": "100"}
    ).json()

    assert client.delete(f"/api/balances/{entry['id']}").status_code == 204
    assert client.get("/api/accounts").json()["recent"] == []
