# Running Hearth on the NAS — step by step

The desktop runs LM Studio and nothing else; the Synology DS423+ runs Postgres, SearXNG and
Hearth from `docker-compose.nas.yml`. The README's "Running Hearth on the NAS" explains the
design and why each guardrail is there. This is the order to do it in.

`<desktop-ip>` and `<nas-ip>` are the two machines' LAN addresses. `<nas-name>` is the NAS's
name from Control Panel → Info Center (as in `ds423.local`).

Two things the order depends on:

- **Restore before Hearth first starts.** Hearth migrates on start, and `make restore`
  refuses a database that already holds tables. So: Postgres alone, restore, then Hearth.
- **Folder permissions.** The hearth container runs as user `10001`, which a Synology share
  does not let in by default. Step 6 handles it.

The steps most likely to stop you are 8, 10 and 14. Each says what to look for.

---

## Part 1 — on the desktop

**1. Back up what you have now** (`.env` still pointing at the native cluster):

```
make backup
```

Note the path it prints — `...\Hearth\backups\hearth-<date>.dump`.

**2. Give the desktop a fixed address.** In the router, add a DHCP reservation for it.
Note `<desktop-ip>` and `<nas-ip>`.

**3. Let LM Studio answer the NAS, and only the NAS.**

- LM Studio → Developer → Server settings → **Serve on Local Network**.
- Admin PowerShell:

  ```powershell
  New-NetFirewallRule -DisplayName "LM Studio from NAS" -Direction Inbound -Protocol TCP -LocalPort 1234 -RemoteAddress <nas-ip> -Action Allow
  ```

- In "Windows Defender Firewall with Advanced Security" → Inbound Rules, disable any older
  **LM Studio** rule that allows every address. Windows makes one when you click "Allow" on
  its prompt, and it would make the narrow rule above meaningless.

**4. Make the password hash.** On the Mac or the desktop, in the repo:

```
make password
```

Keep the `HEARTH_PASSWORD_HASH=scrypt:...` line it prints for step 7.

---

## Part 2 — on the NAS

**5. Prepare the NAS.**

- Package Center → install **Git Server**, which provides the `git` command.
- Control Panel → Terminal & SNMP → **Enable SSH service**. Turned off again in step 17.
- Control Panel → Shared Folder → create **`hearth`** on a Btrfs volume, on the M.2 NVMe
  pool if there is one.

**6. Get the code and make the folders.** SSH in (`ssh <your-dsm-user>@<nas-ip>`), then:

```bash
cd /volume1/hearth
git clone -b main https://github.com/jlthompson96/Hearth.git app
mkdir pgdata data backups exports
sudo chown 10001 backups exports          # the hearth container's user writes here
sudo chmod 755 data                       # and reads here
ls -l app/docker/postgres/initdb-nas/     # 02-restrict-hba.sh must show -rwx
```

**7. Write the NAS's `.env`.** Make three secrets first. Hex only, because the passwords go
inside URLs:

```bash
openssl rand -hex 24    # POSTGRES_PASSWORD
openssl rand -hex 24    # HEARTH_RO_PASSWORD
openssl rand -hex 32    # SEARXNG_SECRET
```

Then create `/volume1/hearth/app/.env` with `vi`, or with the Text Editor package:

```
POSTGRES_USER=hearth
POSTGRES_PASSWORD=<hex 1>
POSTGRES_DB=hearth
HEARTH_RO_PASSWORD=<hex 2>
SEARXNG_SECRET=<hex 3>
SEARCH_BLOCKED_TERMS=<surname, street, employer>

LM_STUDIO_BASE_URL=http://<desktop-ip>:1234/v1
CHAT_MODEL=<same as the desktop's .env>
EMBEDDING_MODEL=<same as the desktop's .env>

HEARTH_ALLOWED_HOSTS=<nas-name>.local,<nas-ip>
HEARTH_PASSWORD_HASH=<from step 4, the value only>

NAS_BIND_IP=<nas-ip>
HEARTH_CLIENT_IPS=<desktop-ip>
NAS_PGDATA_DIR=/volume1/hearth/pgdata
NAS_DATA_DIR=/volume1/hearth/data
NAS_BACKUP_DIR=/volume1/hearth/backups
NAS_EXPORT_DIR=/volume1/hearth/exports
```

**8. Start Postgres on its own.**

```bash
cd /volume1/hearth/app
sudo docker compose -f docker-compose.nas.yml up -d postgres
sudo docker compose -f docker-compose.nas.yml logs postgres | grep -E "restricted|bad interpreter|error"
```

You want to see **`pg_hba.conf: TCP connections restricted to <desktop-ip> 172.30.42.10`**.

- **`bad interpreter`** — the cluster came up closed, so nothing can reach it. Run
  `sudo docker compose -f docker-compose.nas.yml down`, empty `pgdata`, `chmod +x` the
  script, and start again.
- **A permission error from initdb on `pgdata`** — Synology's ACLs on the share. Stop there
  and look at the full log before changing anything.

---

## Part 3 — move the data (on the desktop)

**9. Point the desktop's `.env` at the NAS.** Change these lines and leave the rest:

```
INFRA_HOST=<nas-name>.local
DATABASE_URL=postgresql+psycopg://hearth:<hex 1>@<nas-ip>:5432/hearth
DATABASE_URL_RO=postgresql+psycopg://hearth_ro:<hex 2>@<nas-ip>:5432/hearth
PGDATA=
```

Keep `PGBIN`: `make restore` needs `pg_restore`.

**10. Restore.**

```
make restore DUMP=<the path from step 1>
```

If it is refused with **`pg_hba.conf rejects connection for host "<some address>"`**, then
Synology's Docker rewrote the desktop's address, and that message names what Postgres saw.
Do not add the address blindly. If it is the Docker gateway (`172.30.42.1`), admitting it
admits anything that reaches the port, and the firewall rule in step 13 becomes the only
restriction.

**11. Retire the desktop's Postgres.** Stop it with `pg_ctl stop -D <old PGDATA>`, and stop
starting Hearth on the desktop (`make start`, `hearth.cmd`). Keep the old data folder until
step 14 checks out.

---

## Part 4 — start Hearth (on the NAS)

**12. Build and start the rest.**

```bash
sudo docker compose -f docker-compose.nas.yml up -d --build
sudo docker compose -f docker-compose.nas.yml logs -f hearth
```

The first build compiles the UI and installs the Python packages. It takes about 10–20
minutes on the J4125. Wait for `Uvicorn running on http://0.0.0.0:8000`. The migrations
should find nothing to do, because the restore brought the schema. All three containers
also appear in Container Manager.

**13. HTTPS, firewall, and the cloud features.**

- Control Panel → Security → **Certificate**. The default self-signed certificate is fine
  to begin with.
- Control Panel → Login Portal → Advanced → **Reverse Proxy** → Create:
  - Source: HTTPS, hostname `<nas-name>.local`, port **8443**
  - Destination: HTTP, `localhost`, port **8000**
- Control Panel → Security → **Firewall**, enabled:
  - allow **5432** from `<desktop-ip>` only;
  - allow **8443** from the home subnet;
  - **keep DSM's own ports (5000/5001) and SSH allowed above any deny rule**, or you lock
    yourself out.
- Turn off QuickConnect (Control Panel → External Access) and device analytics (Info
  Center). Keep `hearth` out of Cloud Sync.

**14. Open it.** Go to **https://\<nas-name\>.local:8443**, accept the certificate once,
and sign in.

- **"Invalid host header"** — the name in the address bar is not in
  `HEARTH_ALLOWED_HOSTS`. Add it, then `sudo docker compose -f docker-compose.nas.yml up -d`.
- **Data & imports** — check the account totals against Fidelity's.
- **Chat** — ask something. An answer proves the NAS reaches LM Studio. "Model
  unavailable" means step 3 needs another look.

---

## Part 5 — finishing

**15. Move the CSV exports.** Over SMB, copy them from the desktop's `HEARTH_DATA_DIR` into
`\\<nas-ip>\hearth\data`. If Data & imports cannot read them, run
`sudo chmod -R a+rX /volume1/hearth/data`.

**16. Schedule backups.** Control Panel → Task Scheduler → Create → Scheduled Task →
User-defined script, run as **root**, daily:

```
docker exec hearth-hearth-1 python -m scripts.backup
```

Then add a Hyper Backup task that copies `/hearth/backups` somewhere other than this NAS. A
dump on the same NAS as its database is not a backup of it.

**17. Turn SSH off again.**

**18. From the desktop, run `make eval`.** The model is now reached across the LAN. This
measures it against the evals' own database on the NAS.

---

## Updating later

```bash
cd /volume1/hearth/app
git pull
sudo docker compose -f docker-compose.nas.yml up -d --build
```

Hearth applies any new migrations before it serves.
