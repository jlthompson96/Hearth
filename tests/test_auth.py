"""The password in front of the API (api/auth.py).

Served from the NAS, Hearth answers every device at home, and this is what
stands between them and the Model log. So the tests are about the gate's
edges: every /api route behind it, a forged or stale session refused, guessing
slowed, and no way to open Hearth to the network without setting a password.
"""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import config
from api import auth
from api.main import app
from config import Settings

PASSWORD = "correct horse battery"
STORED = auth.hash_password(PASSWORD)


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A Hearth that asks for the password. Plain HTTP, because the test client
    speaks it and would not send a Secure cookie back."""
    monkeypatch.setenv("HEARTH_PASSWORD_HASH", STORED)
    monkeypatch.setenv("HEARTH_COOKIE_SECURE", "false")
    config.get_settings.cache_clear()
    auth.throttle.reset()
    yield
    auth.throttle.reset()
    config.get_settings.cache_clear()


@pytest.fixture
def no_gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("HEARTH_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("HEARTH_ALLOWED_HOSTS", raising=False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def client() -> TestClient:
    """Addressed to localhost: the test client's own "testserver" is a name the
    host guard refuses, as it should."""
    return TestClient(app, base_url="http://localhost")


def sign_in(http: TestClient, password: str = PASSWORD) -> int:
    return http.post("/api/login", json={"password": password}).status_code


# --- the password and the token ------------------------------------------------


def test_a_password_checks_against_its_own_hash_and_nothing_else() -> None:
    assert auth.verify_password(PASSWORD, STORED)
    assert not auth.verify_password(PASSWORD + " ", STORED)
    assert not auth.verify_password("", STORED)


def test_two_hashes_of_one_password_differ() -> None:
    """Salted: the hash in .env says nothing about whether another one matches."""
    assert auth.hash_password(PASSWORD) != STORED


@pytest.mark.parametrize("stored", ["", "plain", "md5:x", "scrypt:1:2:3", "scrypt:a:b:c:d:e"])
def test_a_hash_that_will_not_read_matches_nothing(stored: str) -> None:
    assert not auth.verify_password(PASSWORD, stored)


def test_the_hash_has_no_dollar_sign() -> None:
    """The Makefile includes .env, and make would expand `$3` inside the value;
    compose interpolates `$` too. Either would hand the app a different hash."""
    assert "$" not in STORED
    assert re.fullmatch(r"scrypt:\d+:\d+:\d+:[\w-]+:[\w-]+", STORED)


def test_a_token_is_good_until_it_expires() -> None:
    token = auth.issue(STORED, now=1_000)

    assert auth.valid(token, STORED, now=1_000)
    assert auth.valid(token, STORED, now=1_000 + auth.SESSION_SECONDS - 1)
    assert not auth.valid(token, STORED, now=1_000 + auth.SESSION_SECONDS)


def test_a_token_with_a_later_expiry_written_in_is_refused() -> None:
    token = auth.issue(STORED, now=1_000)
    _, expiry, signature = token.split(".")

    assert not auth.valid(f"v1.{int(expiry) + 10**9}.{signature}", STORED, now=1_000)


def test_changing_the_password_signs_every_device_out() -> None:
    token = auth.issue(STORED, now=1_000)

    assert not auth.valid(token, auth.hash_password("a different password"), now=1_000)


@pytest.mark.parametrize("token", [None, "", "v1", "v1.x.y", "v2.99999999999.abc", "garbage"])
def test_a_token_that_will_not_read_is_refused(token: str | None) -> None:
    assert not auth.valid(token, STORED, now=1_000)


# --- the gate ------------------------------------------------------------------


def _guarded_requests() -> list[tuple[str, str]]:
    """Every /api route the app has, with its path parameters filled in. Read
    from the app's own OpenAPI schema rather than listed, so a route added later
    is covered here without anyone remembering to add it — and rather than from
    `app.routes`, which wraps included routers and lists none of them."""
    requests: list[tuple[str, str]] = []
    for template, operations in app.openapi()["paths"].items():
        if not template.startswith("/api/") or template in auth.OPEN:
            continue
        path = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", template)
        requests.extend((method.upper(), path) for method in operations)
    return sorted(requests)


def test_the_app_has_routes_to_guard() -> None:
    """If this ever finds none, the test below proves nothing."""
    assert len(_guarded_requests()) > 15


@pytest.mark.usefixtures("gate")
@pytest.mark.parametrize(("method", "path"), _guarded_requests())
def test_every_api_route_refuses_a_request_without_a_session(method: str, path: str) -> None:
    """Refused before the route runs, so nothing it would have returned leaks —
    and no database is needed to prove it."""
    response = client().request(method, path, json={})

    assert response.status_code == 401
    assert response.json() == {"kind": "signed_out", "message": "Sign in to continue."}


@pytest.mark.usefixtures("gate")
@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_the_api_description_is_behind_the_gate_too(path: str) -> None:
    assert client().get(path).status_code == 401


@pytest.mark.usefixtures("gate")
def test_health_and_the_session_probe_answer_without_one() -> None:
    http = client()

    assert http.get("/health").status_code == 200
    assert http.get("/api/session").json() == {"required": True, "signed_in": False}


@pytest.mark.usefixtures("gate")
def test_signing_in_opens_the_api_and_signing_out_closes_it() -> None:
    http = client()

    assert sign_in(http) == 204
    assert http.get("/api/session").json() == {"required": True, "signed_in": True}
    assert http.get("/openapi.json").status_code == 200

    assert http.post("/api/logout").status_code == 204
    assert http.get("/openapi.json").status_code == 401


@pytest.mark.usefixtures("gate")
def test_a_wrong_password_is_refused_and_opens_nothing() -> None:
    http = client()

    response = http.post("/api/login", json={"password": "not it"})

    assert response.status_code == 401
    assert response.json()["kind"] == "wrong_password"
    assert auth.COOKIE not in response.cookies
    assert http.get("/openapi.json").status_code == 401


@pytest.mark.usefixtures("gate")
def test_the_session_cookie_is_out_of_reach_of_scripts_and_other_sites() -> None:
    response = client().post("/api/login", json={"password": PASSWORD})
    cookie = response.headers["set-cookie"].lower()

    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie


def test_the_session_cookie_is_https_only_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEARTH_PASSWORD_HASH", STORED)
    monkeypatch.delenv("HEARTH_COOKIE_SECURE", raising=False)
    config.get_settings.cache_clear()
    auth.throttle.reset()
    try:
        response = client().post("/api/login", json={"password": PASSWORD})
        assert "secure" in response.headers["set-cookie"].lower()
    finally:
        config.get_settings.cache_clear()


@pytest.mark.usefixtures("gate")
def test_guessing_is_throttled_and_the_right_password_does_not_get_through() -> None:
    """Once locked, the right password is refused like any other attempt, or
    the throttle would tell a guesser which guess was right."""
    http = client()
    for _ in range(auth.MAX_FAILURES):
        assert sign_in(http, "a guess") == 401

    response = http.post("/api/login", json={"password": PASSWORD})

    assert response.status_code == 429
    assert response.json()["kind"] == "throttled"
    assert http.get("/openapi.json").status_code == 401


def test_the_throttle_forgets_failures_older_than_its_window() -> None:
    throttle = auth.Throttle()
    for second in range(auth.MAX_FAILURES):
        throttle.failed(now=second)

    assert throttle.locked_for(now=auth.MAX_FAILURES) > 0
    assert throttle.locked_for(now=auth.WINDOW + auth.MAX_FAILURES) == 0


@pytest.mark.usefixtures("no_gate")
def test_without_a_password_hearth_behaves_as_before() -> None:
    """On the desktop, answering localhost alone, nothing changes."""
    http = client()

    assert http.get("/api/session").json() == {"required": False, "signed_in": True}
    assert http.get("/openapi.json").status_code == 200


# --- the network ---------------------------------------------------------------


@pytest.mark.usefixtures("gate")
def test_a_name_added_to_the_allowed_hosts_is_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEARTH_ALLOWED_HOSTS", "Hearth.local, 192.168.1.10")
    config.get_settings.cache_clear()
    http = client()

    assert http.get("/health", headers={"host": "hearth.local"}).status_code == 200
    assert http.get("/health", headers={"host": "192.168.1.10:8000"}).status_code == 200
    assert http.get("/health", headers={"host": "rebind.example"}).status_code == 400


@pytest.mark.usefixtures("gate")
def test_the_host_is_checked_before_the_session() -> None:
    """A rebinding page learns nothing — not even that there is a sign-in."""
    response = client().get("/api/threads", headers={"host": "rebind.example"})

    assert response.status_code == 400
    assert response.text == "Invalid host header"


def test_hearth_will_not_answer_other_machines_without_a_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 7: this is a startup failure, not a line in the README."""
    monkeypatch.setenv("HEARTH_ALLOWED_HOSTS", "hearth.local")
    monkeypatch.delenv("HEARTH_PASSWORD_HASH", raising=False)

    with pytest.raises(ValidationError, match="HEARTH_PASSWORD_HASH"):
        Settings(_env_file=None)

    monkeypatch.setenv("HEARTH_PASSWORD_HASH", "   ")
    with pytest.raises(ValidationError, match="HEARTH_PASSWORD_HASH"):
        Settings(_env_file=None)

    monkeypatch.setenv("HEARTH_PASSWORD_HASH", STORED)
    assert Settings(_env_file=None).hearth_allowed_hosts == ["hearth.local"]
