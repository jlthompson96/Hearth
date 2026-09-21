# The six commands in CLAUDE.md are the contract. Everything else here exists
# to serve them.

VENV := .venv

# The GPU host runs Windows natively with no WSL, where a virtualenv puts its
# executables in Scripts/ rather than bin/. Recipes below use $(BIN), never a
# hardcoded path, so migrate/seed/test/lint work on both machines.
ifeq ($(OS),Windows_NT)
PYTHON ?= py -3.12
BIN    := $(VENV)/Scripts
else
PYTHON ?= python3.12
BIN    := $(VENV)/bin
endif

PY  := $(BIN)/python
PIP := $(BIN)/pip

-include .env
export

API_PORT ?= 8000

.PHONY: dev migrate seed unseed test eval lint fmt install up down logs freeze clean hooks

## dev — docker compose up + backend + frontend
dev: install up
	@trap 'kill 0' EXIT INT TERM; \
		$(BIN)/uvicorn api.main:app --reload --port $(API_PORT) & \
		(cd web && npm run dev) & \
		wait

## migrate — alembic upgrade head
migrate: install
	@test -f alembic.ini || { echo "Migrations land in Phase 1 (docs/plan.md)."; exit 1; }
	$(BIN)/alembic upgrade head

## seed — load the golden fixture dataset (also used by evals)
seed: install
	@test -f scripts/seed.py || { echo "The golden fixture lands in Phase 1 (docs/plan.md)."; exit 1; }
	$(PY) -m scripts.seed

## unseed — empty the development database, before a first real import
unseed: install
	$(PY) -m scripts.seed --empty

## test — pytest
test: install
	$(BIN)/pytest

## eval — run the behavioural case file (against its own hearth_eval database)
eval: install
	@test -f evals/cases.yaml || { echo "The case file lands in Phase 7 (docs/plan.md)."; exit 1; }
	$(BIN)/pytest evals -p no:cacheprovider

## hooks — point git at .githooks (tracked, unlike .git/hooks)
hooks:
	git config core.hooksPath .githooks
	@echo "pre-commit installed: warns when prompts/, agents/ or tools/bindings.py change without a fresh eval run"

## lint — ruff + mypy
lint: install
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .
	$(BIN)/mypy
	cd web && npm run typecheck

fmt: install
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

install: $(VENV) web/node_modules

# `python -m pip`, not `pip`: on Windows pip.exe cannot replace itself while it
# is the running process, so `pip install --upgrade pip` fails outright and the
# venv is left half-built — with the directory present, so make considers the
# target done and never installs the requirements.
$(VENV):
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

# `cd web && npm`, not `npm --prefix web`: on Windows npm resolves a relative
# --prefix against the wrong root and looks for package.json beside the
# Makefile instead of inside web/.
web/node_modules: web/package.json
	cd web && npm install
	touch web/node_modules

# Two ways to get a Postgres. Setting PGDATA in .env selects a native cluster
# driven by pg_ctl; leaving it unset keeps docker compose. The GPU host has no
# working Docker — its WSL2 backend needs a Windows feature that is off, and the
# WSL2 route is ruled out anyway — so it runs native. The Mac runs compose.
#
# Native gets you Postgres and nothing else: SearXNG is a compose service, and
# Phase 9 will need a native home for it or a working Docker on this machine.
#
# The server is started through Start-Process, not straight from the recipe,
# because a postgres launched from the shell inherits make's stdout on Windows
# and never lets go of it. Shell redirection does not prevent that — the handle
# is inherited at CreateProcess time — so `make up | anything` would hang
# forever on a server that had in fact started. Start-Process gives the child
# fresh handles. Readiness is then pg_isready's job rather than pg_ctl -w's.

## up — infrastructure only; waits for Postgres to accept connections
up:
ifdef PGDATA
	@"$(PGBIN)/pg_ctl" -D "$(PGDATA)" status >/dev/null 2>&1 || powershell -NoProfile -Command \
		"Start-Process -FilePath '$(PGBIN)/pg_ctl.exe' -WindowStyle Hidden -ArgumentList \
		 '-D','$(PGDATA)','-l','$(PGDATA)/server.log','start'"
	@until "$(PGBIN)/pg_isready" -q -h 127.0.0.1 -p $(POSTGRES_PORT); do sleep 1; done
	@echo "postgres ready on 127.0.0.1:$(POSTGRES_PORT)"
else
	docker compose up -d --wait
endif

down:
ifdef PGDATA
	@"$(PGBIN)/pg_ctl" -D "$(PGDATA)" -m fast stop </dev/null || true
else
	docker compose down
endif

logs:
ifdef PGDATA
	@tail -f "$(PGDATA)/server.log"
else
	docker compose logs -f
endif

## freeze — turn requirements.txt ranges into exact pins, once resolved
freeze: install
	$(PIP) freeze --exclude-editable > requirements.lock
	echo "Wrote requirements.lock — commit it."

clean:
	rm -rf $(VENV) web/node_modules web/dist .pytest_cache .mypy_cache .ruff_cache
