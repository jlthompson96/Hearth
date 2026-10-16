# Hearth on a Raspberry Pi 5 (replacing the NAS)

## Context
Hearth was moving onto the Synology NAS (branch `storage-on-nas`, since merged to `main`). Partway
through the NAS setup, the owner decided to host everything on a **Raspberry Pi 5** instead. The
desktop keeps running **LM Studio only**, as before.
- **Storage:** a **USB 3 SSD**, which the Pi also boots from.
- **Backups:** to the **NAS**.

Most of the branch carries over unchanged:
- the password gate (`api/auth.py`, `api/routes/session.py`, the sign-in UI, `tests/test_auth.py`);
- the startup guard in `config.py`;
- Postgres closed by default (`POSTGRES_HOST_AUTH_METHOD=reject`, `docker/postgres/initdb-nas/02-restrict-hba.sh`);
- the Hearth container's fixed address `172.30.42.10`, the only internal address Postgres admits;
- the `Dockerfile`.

The Pi lacks three things DSM was supplying, so the plan has to replace them:
- an HTTPS reverse proxy;
- a firewall screen, which also needs a Docker-specific fix;
- Hyper Backup.

**The architecture now works in our favour.** The Pi 5 is ARM64, the same as the Mac. The image
already built natively and passed the full end-to-end check on the Mac (that first build was
arm64). pgvector, SearXNG, Caddy, python-slim and the PGDG `postgresql-client-17` all publish
arm64 builds.

## Topology
| Machine | Runs |
|---|---|
| Desktop | LM Studio (serving on the LAN; Windows Firewall admits the **Pi's** address only) |
| Pi 5 | `caddy` (HTTPS, the only way into Hearth), `hearth`, `postgres`, `searxng` |
| NAS | Receives nightly dumps over NFS; Hyper Backup can take them offsite |
| Mac | Development; builds the image |

## Changes

### 1. Compose: `docker-compose.nas.yml` → `docker-compose.pi.yml`
- Rename the file (`git mv`) and rewrite the Synology wording. The service definitions stay as they are, except for these changes:
- **Add `caddy`** (official image, pinned by tag and digest like SearXNG). It publishes `443`, and `80` only to redirect to 443. It reaches `hearth:8000` by service name.
  - **`hearth` loses its `ports:` entirely.** It is reachable only through Caddy, on the project network, rather than through the "loopback plus DSM proxy" route.
- **Add `docker/caddy/Caddyfile`:**
  - global options: `local_certs` (an internal CA, so no ACME and no outbound call) and `admin off`;
  - one site, `hearth.local` (from `HEARTH_HOSTNAME`), with `tls internal` and `reverse_proxy hearth:8000`.
  - Caddy passes the Host header through, so `api/hosts.py` sees `hearth.local`.
- **Caddy's data directory is bind-mounted** (`/srv/hearth/caddy`), so the local CA survives restarts and each device trusts it only once.
- **Paths become ordinary Linux paths under `/srv/hearth`** (`pgdata`, `data`, `backups`, `exports`, `caddy`). The variables are renamed `NAS_*` → `PI_*`, and `NAS_BIND_IP` → `PI_BIND_IP`, which is used for Postgres's port.

### 2. Firewall, done the way Docker requires
- **Docker publishes ports through its own iptables rules, ahead of `ufw`.** A `ufw` rule therefore does **not** protect Postgres's port, and a plan that relied on it would be wrong.
- **Add `docker/pi/firewall.sh` and a systemd unit `docker/pi/hearth-firewall.service`** that write `DOCKER-USER` chain rules at boot:
  - `5432` accepted from `HEARTH_CLIENT_IPS` (the desktop) only;
  - `443` and `80` from the LAN subnet;
  - everything else to published ports dropped.
- `ufw` still covers the host itself: SSH from the LAN only.
- `pg_hba.conf` remains the second, independent lock. It is closed by default.

### 3. Backups to the NAS
- **Dumps stay on the Pi's SSD first** (`/srv/hearth/backups`, already mounted as `/backups`). Then they're copied to an **NFS** export on the NAS.
  - NFS rather than SMB: it's restricted by the Pi's address on the Synology side, and no NAS password is stored on the Pi.
- **Add `docker/pi/hearth-backup.service` and a `.timer` (nightly):**
  - `docker exec hearth-hearth-1 python -m scripts.backup` (the existing script, which reads each dump back and keeps the newest 14);
  - then `mountpoint -q /mnt/nas-hearth` **or fail**, so a dropped mount never writes into the empty mountpoint on the SSD and reads as success;
  - then `rsync` the dumps across.
  - Failure shows up in `systemctl --failed`. No notification service is added (rule 6).
- **NAS side:** a shared folder with NFS enabled, permission granted to the Pi's IP only, and optionally a Hyper Backup task offsite.

### 4. Image
- **The Makefile's `nas-image` target becomes `pi-image`,** building `linux/arm64`. That's native on the Mac, so it takes seconds and needs no emulation. Same archive scheme: `dist/hearth-<commit>-arm64.tar.gz`, with the revision label kept.
- **The guide also documents building on the Pi itself** (`docker compose build`), since the repo is on the Pi anyway.

### 5. Docs
- **`docs/nas-setup.md` → `docs/pi-setup.md`**, rewritten. Outline:
  1. **Desktop:**
     - `make backup`;
     - a DHCP reservation for the desktop **and** the Pi;
     - LM Studio's Windows Firewall rule admitting the **Pi's** IP;
     - `make password`.
  2. **Pi OS:**
     - Raspberry Pi Imager → **Pi OS Lite 64-bit** onto the USB SSD;
     - in Imager's settings, set hostname `hearth`, add an SSH key, and **turn off Imager's anonymous usage statistics** (rule 6's spirit);
     - official 27 W supply, because a USB SSD draws power;
     - boots from USB by default with no SD card fitted;
     - `unattended-upgrades`.
     - A short USB SSD note: if `dmesg` shows UAS resets, add the bridge's `usb-storage.quirks`.
  3. **Docker Engine** from Docker's apt repo, plus the compose plugin. Use `sudo`, not the `docker` group, which is root-equivalent.
  4. **Folders:**
     - `/srv/hearth/{app,pgdata,data,backups,exports,caddy}`;
     - `chown 10001 backups exports`;
     - clone into `app`;
     - write the Pi's `.env`.
  5. **Firewall:** install the firewall unit and enable `ufw`.
  6. **Data, in the order that works:** Postgres alone → check the `restricted to` log line → point the desktop's `.env` at the Pi → `make restore` → load the image → `up -d`.
  7. **Trust Caddy's root certificate on each device.** Copy `root.crt` from `/srv/hearth/caddy/.../pki/authorities/local/`, then install it per platform (iPhone profile, Android, macOS Keychain, Windows).
  8. **Backups:** mount the NAS over NFS in `/etc/fstab`, enable the timer, run it once by hand.
  9. **Desktop:** `make eval`.
- **README:**
  - "Running Hearth on the NAS" → "Running Hearth on the Pi" (topology, guardrails, what crosses the LAN in the clear);
  - the Guardrails table row about the Docker firewall;
  - the Prerequisites line.
- **`docs/plan.md`:** rewrite the 2026-10-14 storage addendum. Record why the NAS gave way to the Pi, the Docker/ufw finding, and that the NAS keeps the backups.
- **`.env.example`:** `INFRA_HOST=hearth.local`.
- **CLAUDE.md** still says "Everything runs on my machine" and gives LM Studio at `localhost:1234`. Flag it to the owner again; it's their file.

### 6. Unwind the NAS attempt (owner, by hand)
No real data was ever restored there.
- `sudo docker compose -f docker-compose.nas.yml down` in `/volume1/homes/Joey/Hearth/app`;
- remove the `hearth` image in Container Manager;
- delete `homes/Joey/Hearth`;
- remove LM Studio's firewall rule for the NAS IP;
- turn SSH off again. Keep NFS on for backups.
- Include these as a short section at the end of `docs/pi-setup.md`.

## Files
- **Modify:** `docker-compose.nas.yml` → `docker-compose.pi.yml`, `docker/postgres/initdb-nas/` → `initdb-pi/` (comments only), `Makefile`, `README.md`, `docs/plan.md`, `.env.example`, `docs/nas-setup.md` → `docs/pi-setup.md`
- **New:** `docker/caddy/Caddyfile`, `docker/pi/firewall.sh`, `docker/pi/hearth-firewall.service`, `docker/pi/hearth-backup.service`, `docker/pi/hearth-backup.timer`
- **Unchanged:** all app code, `Dockerfile`, `tests/`

## Verification
**On the Mac first,** with Docker Desktop, the same way the NAS stack was checked:
- `docker compose -p hearth-pitest -f docker-compose.pi.yml up -d` with throwaway values.
- `curl -k --resolve hearth.local:443:127.0.0.1 https://hearth.local/` should return the UI.
- `/api/threads` should return 401; signing in should give 204 and a `Secure` cookie, and the threads route should then return 200.
- `hearth` should have no published port (`docker port`), and SearXNG should be reachable by name only.
- An unlisted Postgres client should be refused.
- `make test` against the Pi compose file's Postgres.
- The `DOCKER-USER` script can only be exercised on Linux. Lint it with `shellcheck` here, and test it on the Pi.

**On the Pi:**
- **Postgres admission:**
  - the `pg_hba.conf: TCP connections restricted to <desktop-ip> 172.30.42.10` log line;
  - the desktop connects;
  - **the Mac is refused on 5432 at the firewall** (`nc -vz <pi-ip> 5432` times out). That proves `DOCKER-USER`, not just `pg_hba`.
- **The app:**
  - `https://hearth.local` loads on a phone with no certificate warning, once its root is trusted;
  - sign in works;
  - Data & imports totals match Fidelity;
  - a chat question answers, which proves the model across the LAN.
- **Backups:**
  - running the backup service by hand puts a dump on the NAS;
  - with the NFS mount unmounted, the unit **fails** rather than writing locally.
- **Reboot:** after a power cycle the Pi comes back with everything running (`restart: unless-stopped`, Docker enabled, the firewall unit applied).
- **From the desktop:** `make eval`. This is the first eval with the model reached over the network.
