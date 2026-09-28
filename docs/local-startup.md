# Local startup guide

This guide covers the fastest way to start Hearth on a local machine, including the Windows-native PostgreSQL path used when Docker is not available.

## Prerequisites

- Python 3.12
- Node 20.19+ or 22.12+
- PostgreSQL 17, either through Docker Compose or a native local install
- LM Studio running locally at http://localhost:1234/v1
- Make (optional, but recommended)

The project expects a local-first setup: the database, model endpoint, and app all run on the same machine.

## 1) Create the virtual environment

From the repo root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

On macOS/Linux, the equivalent is:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

## 2) Install frontend dependencies

```bash
cd web
npm install
cd ..
```

## 3) Start Postgres

### Option A: Docker Compose

This is the default repo path when Docker is available:

```bash
make up
```

Or directly:

```bash
docker compose up -d --wait
```

### Option B: Native PostgreSQL on Windows

When Docker is unavailable, use the native Postgres install and let the repo point at the local cluster.

1. Install PostgreSQL 17.
2. Ensure the cluster is running.
3. Set the Postgres data directory in `.env` using the project convention expected by the repo.
4. Start it with the repo helper:

```powershell
make up
```

If you prefer direct commands:

```powershell
"C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe" -D "<PGDATA>" start
"C:\Program Files\PostgreSQL\17\bin\pg_isready.exe" -h localhost -p 5432
```

The local app expects PostgreSQL on `localhost:5432`.

## 4) Apply the schema and seed data

From the repo root:

```bash
make migrate
make seed
```

These create the app schema and load the golden fixture dataset used by the local app.

## 5) Start the application

### Quickest local start

```bash
make dev
```

This starts the backend and frontend together. The backend binds to port 8000 and the frontend uses the Vite dev server.

### Manual start

Backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd web
npm run dev
```

## 6) Verify the app is up

Check the backend health endpoint:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
```

Expected result:

```json
{"status":"ok","version":"0.1.0"}
```

The frontend is typically available at:

- http://127.0.0.1:5173

## Troubleshooting

### Port 8000 is already in use

A stale Python process may still be bound to the port. Check and kill it:

```powershell
netstat -ano -p tcp | findstr :8000
Stop-Process -Id <PID> -Force
```

### PostgreSQL connection times out

Check readiness:

```powershell
"C:\Program Files\PostgreSQL\17\bin\pg_isready.exe" -h localhost -p 5432
```

Then verify the service is running and the database exists.

### Missing model endpoint

LM Studio must be running and exposing OpenAI-compatible endpoints at:

```text
http://localhost:1234/v1
```

If the app starts but the model route fails, verify LM Studio is serving a chat model and that the model names are set in the app config.

## Useful commands

```bash
make migrate
make seed
make test
make lint
make dev
```

## Notes

- Real CSV data lives outside the repo in `HEARTH_DATA_DIR`.
- The repo is designed to be run entirely on the local machine; no outbound cloud telemetry or live financial APIs are used.
- The local dev setup is intentionally conservative and should be kept this way.
