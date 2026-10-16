# Running Hearth on the Raspberry Pi — step by step

The desktop runs LM Studio and nothing else. A Raspberry Pi 5, booting from a USB 3 SSD,
runs Caddy, Hearth, Postgres and SearXNG from `docker-compose.pi.yml`. The NAS receives a
copy of every nightly dump. The README's "Running Hearth on the Pi" explains the design
and why each guardrail is there. This is the order to do it in.

`<desktop-ip>`, `<pi-ip>` and `<nas-ip>` are the three machines' LAN addresses, and
`<subnet>` is the home network, as in `192.168.1.0/24`. The Pi is named `hearth`, so it
answers to `hearth.local`.

Three things the order depends on:

- **Restore before Hearth first starts.** Hearth migrates on start, and `make restore`
  refuses a database that already holds tables. So: Postgres alone, restore, then Hearth.
- **The firewall before Docker.** Docker publishes ports through its own iptables rules,
  which `ufw` never sees. Step 9 puts the restriction in Docker's `DOCKER-USER` chain and
  makes Docker refuse to start without it.
- **Caddy's root, once per device.** HTTPS comes from a local CA on the Pi, never a public
  one. Each device that opens Hearth trusts its root once (step 15).

The steps most likely to stop you are 10, 12 and 16. Each says what to look for.

---

## Part 1 — on the desktop

**1. Back up what you have now** (`.env` still pointing at the native cluster):

```
make backup
```

Note the path it prints — `...\Hearth\backups\hearth-<date>.dump`.

**2. Give the desktop and the Pi fixed addresses.** In the router, add a DHCP reservation
for each. Postgres, both firewalls and LM Studio's rule all name them. Note `<desktop-ip>`
and `<pi-ip>`.

**3. Let LM Studio answer the Pi, and only the Pi.**

- LM Studio → Developer → Server settings → **Serve on Local Network**.
- Admin PowerShell:

  ```powershell
  New-NetFirewallRule -DisplayName "LM Studio from Pi" -Direction Inbound -Protocol TCP -LocalPort 1234 -RemoteAddress <pi-ip> -Action Allow
  ```

- In "Windows Defender Firewall with Advanced Security" → Inbound Rules, disable any older
  **LM Studio** rule that allows every address. Windows makes one when you click "Allow" on
  its prompt, and it would make the narrow rule above meaningless.

**4. Make the password hash.** On the Mac or the desktop, in the repo:

```
make password
```

Keep the `HEARTH_PASSWORD_HASH=scrypt:...` line it prints for step 8.

---

## Part 2 — on the Pi

**5. Put the OS on the SSD.** On the Mac, with the SSD in a USB adapter, open Raspberry Pi
Imager:

- Device **Raspberry Pi 5**, OS **Raspberry Pi OS Lite (64-bit)**, storage the SSD.
- In its settings (the gear, or "Edit settings"):
  - hostname **`hearth`**;
  - a user, and **SSH with public-key authentication** only, with your Mac's key;
  - no Wi-Fi: the Pi is wired.
- In Imager's own options, turn off **"Enable telemetry"**. Imager sends anonymous usage
  statistics by default — the same spirit as rule 6.

Plug the SSD into one of the Pi's **blue** USB 3 ports, with no SD card fitted, and use
the official **27 W** supply: a USB SSD draws more than the Pi budgets on a weaker one. A
Pi 5 tries USB when there is no SD card, so it boots from the SSD as shipped.

**6. First login.** `ssh <user>@hearth.local`, then:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y unattended-upgrades ufw nfs-common rsync git
sudo dpkg-reconfigure -plow unattended-upgrades        # answer Yes
```

If `dmesg` shows `uas` resets or I/O errors on the SSD, its USB bridge does not handle
UAS well. Find its id with `lsusb` (as in `152d:0578`), add
`usb-storage.quirks=152d:0578:u` to the end of the single line in
`/boot/firmware/cmdline.txt`, and reboot.

**7. Docker Engine,** from Docker's own apt repository, with the compose plugin:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Use `sudo docker`. Do **not** add your user to the `docker` group: it is root by another
name, and an SSH key would then be the root password.

**8. Folders, code and the Pi's `.env`.**

```bash
sudo mkdir -p /srv/hearth/{pgdata,data,backups,exports,caddy}
sudo chown 10001 /srv/hearth/backups /srv/hearth/exports   # the hearth container's user writes here
sudo chmod 700 /srv/hearth/caddy                           # the local CA's private key
sudo chown $USER /srv/hearth && git clone -b main https://github.com/jlthompson96/Hearth.git /srv/hearth/app
ls -l /srv/hearth/app/docker/postgres/initdb-pi/            # 02-restrict-hba.sh must show -rwx
```

Make three secrets. Hex only, because the passwords go inside URLs:

```bash
openssl rand -hex 24    # POSTGRES_PASSWORD
openssl rand -hex 24    # HEARTH_RO_PASSWORD
openssl rand -hex 32    # SEARXNG_SECRET
```

Then create `/srv/hearth/app/.env` and `chmod 600` it:

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

HEARTH_HOSTNAME=hearth.local
HEARTH_PASSWORD_HASH=<from step 4, the value only>

PI_BIND_IP=<pi-ip>
HEARTH_CLIENT_IPS=<desktop-ip>
PI_PGDATA_DIR=/srv/hearth/pgdata
PI_DATA_DIR=/srv/hearth/data
PI_BACKUP_DIR=/srv/hearth/backups
PI_EXPORT_DIR=/srv/hearth/exports
PI_CADDY_DIR=/srv/hearth/caddy
```

`HEARTH_ALLOWED_HOSTS` defaults to `HEARTH_HOSTNAME`. Set it only to add another name.

**9. The firewall, both halves.**

Docker's published ports — 443, 80 and 5432 — are restricted in `DOCKER-USER`, by a unit
that runs before Docker starts and that Docker requires:

```bash
cd /srv/hearth/app
sudo install -m 755 docker/pi/firewall.sh /usr/local/sbin/hearth-firewall
sudo install -m 644 docker/pi/hearth-firewall.default /etc/default/hearth-firewall
sudo nano /etc/default/hearth-firewall          # <desktop-ip>, <subnet>; LAN_IF from `ip -br addr`
sudo install -m 644 docker/pi/hearth-firewall.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hearth-firewall
sudo systemctl restart docker
sudo iptables -S DOCKER-USER
```

The last command should list the `RETURN` lines for 443, 80 and 5432 and a `DROP` for
everything else new on the LAN interface.

`ufw` covers the Pi itself — SSH, and mDNS, without which no device can resolve
`hearth.local`:

```bash
sudo ufw default deny incoming
sudo ufw allow from <subnet> to any port 22 proto tcp
sudo ufw allow from <subnet> to any port 5353 proto udp
sudo ufw enable
```

Keep the SSH session open and check that a second one connects before closing it.

**10. Start Postgres on its own.**

```bash
cd /srv/hearth/app
sudo docker compose -f docker-compose.pi.yml up -d postgres
sudo docker compose -f docker-compose.pi.yml logs postgres | grep -E "restricted|bad interpreter|error"
```

You want to see **`pg_hba.conf: TCP connections restricted to <desktop-ip> 172.30.42.10`**.

- **`bad interpreter`** — the cluster came up closed, so nothing can reach it. Run
  `sudo docker compose -f docker-compose.pi.yml down`, empty `/srv/hearth/pgdata`,
  `chmod +x` the script, and start again.

---

## Part 3 — move the data (on the desktop)

**11. Point the desktop's `.env` at the Pi.** Change these lines and leave the rest:

```
INFRA_HOST=hearth.local
DATABASE_URL=postgresql+psycopg://hearth:<hex 1>@<pi-ip>:5432/hearth
DATABASE_URL_RO=postgresql+psycopg://hearth_ro:<hex 2>@<pi-ip>:5432/hearth
PGDATA=
```

Keep `PGBIN`: `make restore` needs `pg_restore`. The read-only password must be the Pi's
`HEARTH_RO_PASSWORD`: the restore creates the role with the desktop's, and the container
connects with the Pi's.

**12. Restore.**

```
make restore DUMP=<the path from step 1>
```

- **It hangs, then times out** — the firewall dropped it. `/etc/default/hearth-firewall`
  names a different address from the desktop's. Fix it and
  `sudo systemctl restart hearth-firewall`.
- **`pg_hba.conf rejects connection for host "<some address>"`** — the firewall let it
  through and Postgres saw another address. Linux Docker keeps the source address, so
  this should not happen. If it names `172.30.42.1`, the gateway, do not admit it: that
  admits everything the firewall lets through.

**13. Retire the desktop's Postgres.** Stop it with `pg_ctl stop -D <old PGDATA>`, and stop
starting Hearth on the desktop (`make start`, `hearth.cmd`). Keep the old data folder until
step 16 checks out.

---

## Part 4 — start Hearth (on the Pi)

**14. The image, then the rest.** Build it on the Mac and copy it across as a file. It is
never pushed to a registry. The Pi is arm64 like the Mac, so the build is native:

```bash
make pi-image          # writes dist/hearth-<commit>-arm64.tar.gz
scp dist/hearth-<commit>-arm64.tar.gz <user>@hearth.local:/tmp/
```

Then on the Pi:

```bash
sudo docker load -i /tmp/hearth-<commit>-arm64.tar.gz
cd /srv/hearth/app
sudo docker compose -f docker-compose.pi.yml up -d
sudo docker compose -f docker-compose.pi.yml logs -f hearth
```

Wait for `Uvicorn running on http://0.0.0.0:8000`. The migrations should find nothing to
do, because the restore brought the schema.
`sudo docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' hearth:local`
names the commit running. One ending in `-dirty` was built with uncommitted changes.

Building on the Pi instead works too — `sudo docker compose -f docker-compose.pi.yml build`
— but runs Node and the UI build there, and is slower.

**15. Trust Caddy's root on each device.** Caddy made its CA on first start. Fetch the root
to the Mac:

```bash
ssh <user>@hearth.local 'sudo cat /srv/hearth/caddy/caddy/pki/authorities/local/root.crt' > hearth-root.crt
```

It is a certificate, not a key, and safe to copy. Then on each device:

- **macOS:** `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain hearth-root.crt`
- **iPhone / iPad:** AirDrop or mail it to the device → Settings → *Profile Downloaded* →
  Install. Then Settings → General → About → **Certificate Trust Settings** → turn on
  full trust for "Hearth". iOS skips this second step silently.
- **Android:** Settings → Security → *Encryption & credentials* → *Install a certificate*
  → **CA certificate** (the menu names vary by maker).
- **Windows:** admin PowerShell, `certutil -addstore -f Root hearth-root.crt`.

The root can vouch for any name, and its key is in `/srv/hearth/caddy` on the Pi. That
folder is the one thing on the Pi worth more than the data: keep it root-only (step 8)
and out of every backup that leaves the house.

**16. Open it.** Go to **https://hearth.local** and sign in. There should be no
certificate warning.

- **A certificate warning** — the device does not trust the root yet (step 15), or iOS's
  full-trust switch is off.
- **The name does not resolve** — mDNS is blocked (step 9's 5353 rule), or the device
  does not do mDNS; some Android browsers do not. Give the Pi a DNS name in the router
  instead, and use that as `HEARTH_HOSTNAME`.
- **"Invalid host header"** — the name in the address bar is not in
  `HEARTH_ALLOWED_HOSTS`. Add it, then `sudo docker compose -f docker-compose.pi.yml up -d`.
- **Data & imports** — check the account totals against Fidelity's.
- **Chat** — ask something. An answer proves the Pi reaches LM Studio. "Model unavailable"
  means step 3 needs another look.

---

## Part 5 — finishing

**17. Move the CSV exports.** Copy them from the desktop's `HEARTH_DATA_DIR` into
`/srv/hearth/data`, with `scp` from the Mac or desktop. If Data & imports cannot read them,
`sudo chmod -R a+rX /srv/hearth/data`.

**18. Backups to the NAS.**

On the NAS:
- Control Panel → File Services → **NFS** → enable NFS (v4.1).
- Control Panel → Shared Folder → create **`hearth-backups`** → Edit → **NFS Permissions**
  → Create: hostname or IP **`<pi-ip>`**, Read/Write, squash **Map root to admin**,
  security `sys`. No other host.
- Optionally, a Hyper Backup task that copies `hearth-backups` offsite, with its own
  rotation. Nothing on the Pi deletes a dump from the NAS.

On the Pi:

```bash
sudo mkdir -p /mnt/nas-hearth
echo '<nas-ip>:/volume1/hearth-backups /mnt/nas-hearth nfs vers=4.1,rw,nofail,_netdev,noatime 0 0' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount /mnt/nas-hearth
cd /srv/hearth/app
sudo install -m 644 docker/pi/hearth-backup.service docker/pi/hearth-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hearth-backup.timer
sudo systemctl start hearth-backup && ls -l /mnt/nas-hearth
```

A dump should be on the NAS. Then prove the guard: `sudo umount /mnt/nas-hearth`,
`sudo systemctl start hearth-backup` — it must **fail**, with nothing new copied into the
empty folder — then `sudo mount /mnt/nas-hearth`. A failed run shows in
`systemctl --failed`; nothing else announces it.

**19. Power-cycle the Pi.** Pull the plug, wait, plug it back in. Everything should come
back on its own: `sudo iptables -S DOCKER-USER` shows the rules, `sudo docker ps` shows
four containers, and https://hearth.local signs in.

**20. From the desktop, run `make eval`.** This is the first eval with the model reached
across the network. It measures against the evals' own database on the Pi.

Last, from the Mac, prove the firewall rather than `pg_hba.conf`:
`nc -vz -w 5 <pi-ip> 5432` must **time out**. A quick "connection refused" would mean the
packet reached something.

---

## Updating later

On the Mac, `make pi-image` and copy the archive over, as in step 14. On the Pi:

```bash
sudo docker load -i /tmp/hearth-<commit>-arm64.tar.gz
cd /srv/hearth/app
git pull                # the compose file and docker/ come from here
sudo docker compose -f docker-compose.pi.yml up -d
```

Hearth applies any new migrations before it serves. If `docker/pi/` changed, install its
files again as in steps 9 and 18.

---

## Unwinding the NAS attempt

Hearth was set up on the Synology first and moved before any real data was restored there.
To clear it away, on the NAS:

- `sudo docker compose -f docker-compose.nas.yml down` in
  `/volume1/homes/Joey/Hearth/app` (the file has that name in the checkout there);
- Container Manager → Image → remove `hearth`;
- delete `homes/Joey/Hearth`;
- turn SSH off again under Control Panel → Terminal & SNMP.

Keep NFS on: step 18 uses it. On the desktop, remove the **LM Studio from NAS** firewall
rule.
