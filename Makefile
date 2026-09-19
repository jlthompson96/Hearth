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

.PHONY: dev migrate seed test eval lint fmt install up down logs freeze clean

## dev — docker compose up + backend + frontend
dev: install up
	@trap 'kill 0' EXIT INT TERM; \
		$(BIN)/uvicorn api.main:app --reload --port $(API_PORT) & \
		npm --prefix web run dev & \
		wait

## migrate — alembic upgrade head
migrate: install
	@test -f alembic.ini || { echo "Migrations land in Phase 1 (docs/plan.md)."; exit 1; }
	$(BIN)/alembic upgrade head

## seed — load the golden fixture dataset (also used by evals)
seed: install
	@test -f scripts/seed.py || { echo "The golden fixture lands in Phase 1 (docs/plan.md)."; exit 1; }
	$(PY) -m scripts.seed

## test — pytest
test: install
	$(BIN)/pytest

## eval — run the behavioural case file
eval: install
	@test -f evals/cases.yaml || { echo "The case file lands in Phase 7 (docs/plan.md)."; exit 1; }
	$(BIN)/pytest evals -p no:cacheprovider

## lint — ruff + mypy
lint: install
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .
	$(BIN)/mypy
	npm --prefix web run typecheck

fmt: install
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

install: $(VENV) web/node_modules

$(VENV):
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

web/node_modules: web/package.json
	npm --prefix web install
	touch web/node_modules

## up — infrastructure only; waits for Postgres to pass its healthcheck
up:
	docker compose up -d --wait

down:
	docker compose down

logs:
	docker compose logs -f

## freeze — turn requirements.txt ranges into exact pins, once resolved
freeze: install
	$(PIP) freeze --exclude-editable > requirements.lock
	echo "Wrote requirements.lock — commit it."

clean:
	rm -rf $(VENV) web/node_modules web/dist .pytest_cache .mypy_cache .ruff_cache
