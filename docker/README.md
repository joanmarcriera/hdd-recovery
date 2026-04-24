# hdd-forensics Docker Container

## Overview

`joanmarcriera/hdd-forensics` is the analysis half of a two-machine recovery workflow. The acquisition machine (Optiplex/Kali) images source HDDs with ddrescue and transfers the raw `.img` files to a TrueNAS SCALE NAS over SSH. This container runs on TrueNAS and provides the full forensics toolchain — sleuthkit, bulk_extractor, foremost, scalpel, PhotoRec, ext4magic, extundelete, tesseract-ocr, recoll, and the project's analysis scripts — accessible through a browser-based terminal (ttyd). A Go supervisor manages ttyd, exposes health and status API endpoints for TrueNAS health probes, and tests Ollama connectivity at startup. The container never touches source disks; it operates exclusively on image files stored in the mounted ZFS dataset.

---

## TrueNAS SCALE — Custom App Setup

This is the primary installation method. Use the TrueNAS web UI: **Apps → Custom App → Install**.

### 1. Image

| Field | Value |
|-------|-------|
| Repository | `joanmarcriera/hdd-forensics` |
| Tag | `latest` |
| Pull Policy | `Pull the image if not already present` |

### 2. Environment Variables

Add each variable using the **Add** button under Environment Variables.

| Name | Value | Notes |
|------|-------|-------|
| `TTYD_PASSWORD` | your chosen password | **Required.** Container refuses to start without it. |
| `TTYD_USER` | `admin` | Username for the browser terminal auth dialog. |
| `OLLAMA_HOST` | `http://host-gateway:11434` | See Ollama section below. If `host-gateway` does not resolve on your TrueNAS build, use the explicit LAN IP: `http://192.168.x.x:11434` |

### 3. Storage

Add one volume mount under **Storage**.

| Field | Value |
|-------|-------|
| Type | Host Path |
| Host Path | `/mnt/BigDisk/CryptoBackup` |
| Mount Path | `/mnt/recovery16tb` |
| Read Only | **OFF** (the container writes analysis databases and export outputs here) |

### 4. Port Forwarding

Add two port entries under **Port Forwarding**.

| Container Port | Host Port | Protocol | Purpose |
|---------------|-----------|----------|---------|
| `7681` | `7681` | TCP | Browser terminal (ttyd) |
| `8080` | `9999` | TCP | Health / status API |

> **Note on port 8080:** TrueNAS itself often binds 8080. Map the container's `8080` to a free host port such as `9999`. Set `HEALTH_PORT=9999` in Environment Variables if you change this. The TrueNAS health probe must point at whichever host port you choose.

### 5. GPU

Do **not** enable GPU passthrough. Ollama runs on the TrueNAS host, not inside this container.

### 6. Restart Policy

Set to **Unless Stopped**.

### After Install

Open the browser terminal:

```
http://<truenas-ip>:7681
```

Log in with username `admin` (or whatever you set `TTYD_USER` to) and the `TTYD_PASSWORD` you configured. The terminal starts the TUI by default; change `TTYD_CMD` to `bash` if you want a plain shell.

---

## docker compose Setup

Use this method on any Linux host, or for local development and testing.

```bash
git clone https://github.com/joanmarcriera/hdd-recovery.git
cd hdd-recovery/docker
cp .env.example .env
$EDITOR .env          # set TTYD_PASSWORD at minimum; adjust DATA_ROOT if needed
docker compose up -d
```

To **build locally** instead of pulling from Docker Hub, the `docker-compose.yml` already includes a `build:` block pointing at `docker/Dockerfile`. Force a local build with:

```bash
docker compose up -d --build
```

To pull the published image explicitly:

```bash
docker compose pull && docker compose up -d
```

The compose file expects the `.env` file to live at `docker/.env`. Run all `docker compose` commands from the `docker/` directory, or pass `--env-file docker/.env` from the repo root.

---

## Environment Variables

| Name | Default | Required | Description |
|------|---------|----------|-------------|
| `TTYD_PASSWORD` | — | **Yes** | Password for the browser terminal. The supervisor exits immediately if this is unset. |
| `TTYD_USER` | `admin` | No | Username shown in the browser auth dialog. |
| `TTYD_PORT` | `7681` | No | Port ttyd listens on inside the container. Change only if you have an internal conflict. |
| `TTYD_CMD` | `cd /root/hdd-recovery && exec bin/tui.sh` | No | Shell command each terminal session runs. Set to `bash` for a plain shell. |
| `HEALTH_PORT` | `8080` | No | Port the supervisor's health/status HTTP server listens on inside the container. |
| `OLLAMA_HOST` | `http://host-gateway:11434` | No | Base URL of the Ollama API. Must be reachable from inside the container. |

---

## Accessing the Container

### Browser Terminal

```
http://<truenas-ip>:7681
```

Enter username and password when prompted. The terminal launches the TUI by default. Supports multiple simultaneous browser connections.

### docker exec

```bash
docker exec -it hdd-forensics bash
```

### Running Analysis Directly

```bash
IMAGE=/mnt/recovery16tb/recovery/images/hitachi.img
DB=${IMAGE}.analysis.sqlite

docker exec -it hdd-forensics bash -c "
  IMAGE=$IMAGE DB=$DB
  cd /root/hdd-recovery
  bin/image-process.sh \$IMAGE
  bin/image-query.sh \$DB summary
  bin/image-query.sh \$DB wallets
"
```

For the full heavy pipeline after transfer from the acquisition machine, `bin/send-image-to-truenas.sh` prints the exact `docker exec` command to run.

---

## Health and Status API

The supervisor exposes two endpoints on `HEALTH_PORT` (default `8080` inside the container; map to a free host port).

### GET /health

Returns `200 OK` when ttyd is running, `503 Service Unavailable` otherwise.

```json
{"ok":true}
```

```bash
curl http://<truenas-ip>:9999/health
```

### GET /status

Returns a full JSON payload regardless of ttyd state.

```json
{
  "ok": true,
  "ttyd_up": true,
  "ttyd_pid": 42,
  "restarts": 0,
  "started_at": "2026-04-24T08:00:00Z",
  "uptime_s": 3600,
  "ollama_host": "http://192.168.1.10:11434",
  "ollama_ok": true,
  "ollama_msg": "reachable"
}
```

| Field | Description |
|-------|-------------|
| `ok` | `true` when ttyd is currently running |
| `ttyd_up` | Same as `ok` |
| `ttyd_pid` | PID of the ttyd process, or `0` if not running |
| `restarts` | Number of times ttyd has been restarted since container start |
| `started_at` | Container start time (RFC 3339, UTC) |
| `uptime_s` | Seconds since container start |
| `ollama_host` | Value of `OLLAMA_HOST` at startup |
| `ollama_ok` | Whether the Ollama `/api/tags` probe succeeded at startup |
| `ollama_msg` | `"reachable"` or a short error description |

```bash
curl http://<truenas-ip>:9999/status
curl -s http://<truenas-ip>:9999/status | jq .ollama_ok
```

---

## Ollama Integration

Ollama runs on the **TrueNAS host**, not inside this container. The container communicates with it over HTTP.

### host-gateway vs explicit IP

`host-gateway` is a Docker special name that resolves to the host's internal Docker gateway IP. It works out of the box with Docker Engine on most Linux hosts. On some TrueNAS SCALE builds (depending on the Kubernetes/Docker version in use), `host-gateway` may not resolve correctly.

- **If `host-gateway` works:** leave `OLLAMA_HOST=http://host-gateway:11434` as-is.
- **If it does not resolve:** find the TrueNAS LAN IP (`ip addr` or the TrueNAS dashboard) and set `OLLAMA_HOST=http://192.168.x.x:11434` explicitly.

The supervisor probes `${OLLAMA_HOST}/api/tags` at container startup with a 5-second timeout. The result (`ollama_ok`, `ollama_msg`) is immediately visible in `/status` and in the startup banner in `docker logs hdd-forensics`. The probe runs once at startup; it does not continuously poll.

To verify Ollama is running on the TrueNAS host:

```bash
curl http://<truenas-ip>:11434/api/tags
```

### Future Use

Vision models (e.g. LLaVA) are planned for scanning carved image files for seed phrases and wallet-related text. The `bin/image-ocr-seed-scan.py` script is the intended integration point. This capability requires Ollama to be reachable and a suitable multimodal model to be pulled on the host.

---

## Volume Layout

The ZFS dataset `/mnt/BigDisk/CryptoBackup` on TrueNAS is mounted at `/mnt/recovery16tb` inside the container. Expected layout:

```
/mnt/recovery16tb/
  recovery/
    images/          # raw disk images (*.img) and SQLite databases (*.img.analysis.sqlite)
    exports/         # per-image analysis output trees
      <basename>/
        structure/   # fdisk, parted, mmls, img_stat, blkid outputs
        recovered/   # carving and recovery tool outputs (by method subdirectory)
        indexes/     # bulk_extractor feature files
        logs/        # per-stage log files
        reports/     # generated summary reports
        hits/
        state/
        exports/
    logs/            # ddrescue map files, rate logs, event logs
    manifests/       # source disk manifests
```

The analysis scripts derive all paths from the image file location. The SQLite database for `foo.img` is always `foo.img.analysis.sqlite` in the same directory.

---

## Troubleshooting

**Container not starting**
- Confirm GPU passthrough is disabled in the Custom App config.
- Confirm Restart Policy is set to `Unless Stopped`.
- Check logs: `docker logs hdd-forensics`
- The most common cause is a missing `TTYD_PASSWORD` — the supervisor calls `log.Fatal` immediately if it is unset.

**Port conflict on 8080**
- TrueNAS binds 8080 by default. Use `HEALTH_PORT=9999` (or any free port) and map container `8080` → host `9999` in the port forwarding config.

**ttyd not loading in the browser**
- Confirm `TTYD_PASSWORD` is set and non-empty.
- Check `/health`: `curl http://<truenas-ip>:9999/health` — if `ok` is `false`, ttyd crashed; check `docker logs hdd-forensics` for the reason.
- Confirm the browser is hitting the correct host port (default `7681`).

**Ollama unreachable**
- Verify `OLLAMA_HOST` contains the correct IP or hostname.
- Check that Ollama is running on the TrueNAS host: `curl http://<truenas-ip>:11434/api/tags`
- If using `host-gateway` and it fails, switch to the explicit LAN IP.
- The startup probe result is in `docker logs hdd-forensics` and at `/status`.

**Checking container logs**

```bash
docker logs hdd-forensics
docker logs --follow hdd-forensics
docker logs --tail 100 hdd-forensics
```

---

## Building and Pushing Updates

Build from the repo root (the Dockerfile uses `COPY` paths relative to the repo root, so the build context must be the repo root):

```bash
docker build -f docker/Dockerfile -t joanmarcriera/hdd-forensics:latest .
```

Push to Docker Hub:

```bash
docker push joanmarcriera/hdd-forensics:latest
```

After pushing, update the running container on TrueNAS:

```bash
docker pull joanmarcriera/hdd-forensics:latest
docker compose -f /path/to/docker/docker-compose.yml up -d
```

Or in the TrueNAS Custom App UI: **Update** the app — it will pull the new `latest` tag according to the configured pull policy.
