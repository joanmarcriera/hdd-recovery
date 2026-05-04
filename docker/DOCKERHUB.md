# hdd-forensics

A self-contained Docker container for hard-disk image forensics, focused on recovering Bitcoin and cryptocurrency wallet artifacts. Designed for a two-machine workflow: one machine captures raw disk images with `ddrescue`; this container runs on a second, more powerful machine (TrueNAS SCALE or any Docker host) and handles all analysis — filesystem indexing, file carving, OCR seed-phrase detection, and LLM-assisted review.

## Key features

- Full filesystem inventory of live, deleted, and orphaned files via **The Sleuth Kit / fiwalk**
- Wallet scoring: `wallet.dat`, Electrum databases, Ethereum keystores, known wallet paths
- **bulk_extractor** scans for Bitcoin addresses, raw private keys, and AES keys in raw image data
- File carving via **foremost**, **scalpel**, **recoverjpeg**, and **magicrescue**
- Deleted file recovery for ext3/4 (**extundelete**, **ext4magic**), NTFS, FAT, XFS, and Btrfs
- OCR via **Tesseract** with BIP-39 seed-word detection across all recovered images
- Per-image **SQLite** database tracking every stage: what ran, when, what it found
- Interactive **TUI** (Python Textual) served at `http://host:7681` — no SSH required
- Read-only **review UI** served at `http://host:7788` for dashboards, recovered artifacts, galleries, timeline, and SQL queries
- Go supervisor manages ttyd, auto-restarts on crash, exposes `/health` and `/status` on port `8080`
- **Ollama** integration: reaches a local Ollama instance via `OLLAMA_HOST`

## Quick start — TrueNAS SCALE Custom App

1. Go to **Apps → Discover Apps → Custom App**
2. Set the image to `joanmarcriera/hdd-forensics:latest`
3. Add environment variables:
   - `TTYD_PASSWORD` = your password (required)
   - `OLLAMA_HOST` = `http://host-gateway:11434`
4. Add a host path volume: host `/mnt/BigDisk/CryptoBackup` → container `/mnt/recovery16tb`
5. Add port forwards: `7681:7681` (TUI), `7788:7788` (review UI), and `8080:8080` (health)
6. Click **Install**, then open `http://truenas-ip:7681` for the TUI or `http://truenas-ip:7788` for the review UI

## Quick start — docker run

```bash
docker run -d \
  --name hdd-forensics \
  --restart unless-stopped \
  -p 7681:7681 -p 7788:7788 -p 8080:8080 \
  -e TTYD_PASSWORD=yourpassword \
  -e OLLAMA_HOST=http://host-gateway:11434 \
  --add-host host-gateway:host-gateway \
  -v /mnt/BigDisk/CryptoBackup:/mnt/recovery16tb \
  joanmarcriera/hdd-forensics:latest
```

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TTYD_PASSWORD` | **yes** | — | Password for the browser terminal |
| `TTYD_USER` | no | `admin` | Username for the browser terminal |
| `TTYD_PORT` | no | `7681` | Browser terminal port |
| `WEB_PORT` | no | `7788` | Read-only review UI port |
| `HEALTH_PORT` | no | `8080` | Health/status API port |
| `OLLAMA_HOST` | no | `http://host-gateway:11434` | Ollama API URL on the Docker host |

## Ports

| Port | Service |
|------|---------|
| `7681` | ttyd browser terminal (TUI) |
| `7788` | Read-only web review UI |
| `8080` | Supervisor health API (`/health`, `/status`) |

## Health API

```
GET /health  →  200 {"ok":true} when ttyd is running, 503 otherwise
GET /status  →  JSON with ttyd PID, restart count, uptime, Ollama status
```

## Full documentation

See the GitHub repository for architecture diagrams, the complete analysis workflow, all environment variables, data layout, and security notes:

**https://github.com/joanmarcriera/hdd-forensics**
