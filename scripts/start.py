"""Start Hearth: Postgres if it is down, then the backend and the frontend.

`make dev` does this already, but `make` is not on this machine's PATH by
default and Postgres does not survive a reboot — which is how the app came to be
down twice in one day. This is the double-click path: `scripts/hearth.cmd` runs
it, everything starts in one window, and Ctrl-C stops both servers.

It starts nothing that is already running, so running it twice is harmless.
Postgres is left running on exit: other things use the cluster, and stopping a
database because a chat window closed would be rude. `make down` stops it.
"""

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / (".venv/Scripts" if os.name == "nt" else ".venv/bin")
EXE = ".exe" if os.name == "nt" else ""

#: Long enough for a cold Postgres and a first Vite build; short enough that a
#: real failure is reported rather than waited on.
READY_TIMEOUT = 60.0


def env_file() -> dict[str, str]:
    """`.env` as a mapping. Values are used, never printed."""
    values: dict[str, str] = {}
    path = REPO / ".env"
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def postgres(settings: dict[str, str]) -> None:
    """Start the native cluster if `PGDATA` names one and it is not up."""
    data, binaries = settings.get("PGDATA"), settings.get("PGBIN")
    if not data or not binaries:
        print("PGDATA/PGBIN not set — expecting Postgres to be running already.")
        return
    port = settings.get("POSTGRES_PORT", "5432")
    ready = [str(Path(binaries) / f"pg_isready{EXE}"), "-q", "-h", "127.0.0.1", "-p", port]
    if subprocess.run(ready).returncode == 0:
        print(f"postgres already up on 127.0.0.1:{port}")
        return

    print("starting postgres…")
    subprocess.run(
        [
            str(Path(binaries) / f"pg_ctl{EXE}"),
            "-D",
            data,
            "-l",
            str(Path(data) / "server.log"),
            "start",
        ],
        check=True,
    )
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if subprocess.run(ready).returncode == 0:
            print(f"postgres ready on 127.0.0.1:{port}")
            return
        time.sleep(1)
    raise SystemExit("postgres did not become ready — see the server log in PGDATA.")


def answering(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):  # noqa: S310 - localhost, built here
            return True
    except (urllib.error.URLError, OSError):
        return False


def wait_for(url: str, what: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if answering(url):
            print(f"{what} ready at {url}")
            return
        time.sleep(1)
    print(f"{what} has not answered at {url} yet; leaving it to keep trying.")


def main() -> int:
    settings = env_file()
    api_port = settings.get("API_PORT", "8000")
    web_port = settings.get("WEB_PORT", "5173")
    postgres(settings)

    running: list[subprocess.Popen[bytes]] = []
    if answering(f"http://localhost:{api_port}/health"):
        print(f"backend already up on {api_port}")
    else:
        running.append(
            subprocess.Popen(
                [str(BIN / f"uvicorn{EXE}"), "api.main:app", "--port", api_port], cwd=REPO
            )
        )
    if answering(f"http://localhost:{web_port}/"):
        print(f"frontend already up on {web_port}")
    else:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        running.append(subprocess.Popen([npm, "run", "dev"], cwd=REPO / "web"))

    wait_for(f"http://localhost:{api_port}/health", "backend")
    wait_for(f"http://localhost:{web_port}/", "frontend")
    webbrowser.open(f"http://localhost:{web_port}/")

    if not running:
        print("everything was already running.")
        return 0
    print("\nCtrl-C stops the servers. Postgres keeps running; `make down` stops it.")
    try:
        while True:
            for process in running:
                if process.poll() is not None:
                    raise KeyboardInterrupt
            time.sleep(1)
    except KeyboardInterrupt:
        for process in running:
            process.terminate()
        for process in running:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
