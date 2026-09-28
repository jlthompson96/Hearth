"""The API answers only requests addressed to this machine by name.

uvicorn listens on 127.0.0.1, which keeps other machines out, but not other
web pages. A page on any site can re-point its own domain at 127.0.0.1 (DNS
rebinding) and then read this API as though it were its own origin — the
Model log, which holds every prompt with its balances, included. What gives
it away is the Host header, which still carries that site's name. So a request
is answered only when the header names this machine.
"""

import pytest
from fastapi.testclient import TestClient

from api.hosts import hostname
from api.main import app


@pytest.mark.parametrize(
    "host", ["localhost", "localhost:8000", "LOCALHOST:5173", "127.0.0.1:8000", "[::1]:8000"]
)
def test_this_machine_by_name_is_answered(host: str) -> None:
    """[::1] included: localhost resolves to IPv6 first on the host, and
    Starlette's own TrustedHostMiddleware reads "[::1]:8000" as "[" and
    refuses it."""
    response = TestClient(app).get("/health", headers={"host": host})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "rebind.example",
        "rebind.example:8000",
        # Its own name as a prefix, a suffix, or a subdomain is still another name.
        "localhost.rebind.example",
        "127.0.0.1.rebind.example",
        "rebind.example.localhost.",
        "",
    ],
)
def test_any_other_name_is_refused(host: str) -> None:
    response = TestClient(app).get("/health", headers={"host": host})

    assert response.status_code == 400


def test_a_refusal_carries_nothing_but_the_refusal() -> None:
    """The route never runs, so nothing it would have returned can leak."""
    response = TestClient(app).get("/api/model-log", headers={"host": "rebind.example"})

    assert response.status_code == 400
    assert response.text == "Invalid host header"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("localhost:8000", "localhost"),
        ("[::1]:8000", "::1"),
        ("127.0.0.1", "127.0.0.1"),
        ("", None),
        ("[::1", None),
    ],
)
def test_the_name_is_read_from_the_header_as_a_url_would_read_it(
    header: str, expected: str | None
) -> None:
    assert hostname(header) == expected
