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
- Single browser UI at `http://host:7788/`: review dashboard at `/`, terminal TUI at `/terminal/`, health/status at `/health` and `/status`
- Separate mount roots for raw images, SQLite databases, recovered exports, and logs
- Go supervisor manages ttyd and the review UI, auto-restarts both on crash, and reports Ollama status
- **Ollama** integration: reaches one or more remote Ollama instances via `OLLAMA_HOST` or `OLLAMA_HOSTS`

## Quick start — TrueNAS SCALE Custom App

1. Go to **Apps → Discover Apps → Custom App**
2. Set the image to `joanmarcriera/hdd-forensics:latest`
3. Add environment variables:
   - `TTYD_PASSWORD` = your password (required)
   - `OLLAMA_HOST` = `http://host-gateway:11434`
4. Add four host path volumes:
   - `/mnt/BigDisk/CryptoBackup/images` → `/data/images`
   - `/mnt/FastPool/hdd-recovery-db` → `/data/db`
   - `/mnt/BigDisk/CryptoBackup/exports` → `/data/exports`
   - `/mnt/BigDisk/CryptoBackup/logs` → `/data/logs`
5. Add one port forward: host `7788` → container `7788`
6. Click **Install**, then open `http://truenas-ip:7788/` for the review UI or `http://truenas-ip:7788/terminal/` for the TUI

## Quick start — docker run

```bash
docker run -d \
  --name hdd-forensics \
  --restart unless-stopped \
  -p 7788:7788 \
  -e TTYD_PASSWORD=yourpassword \
  -e OLLAMA_HOST=http://host-gateway:11434 \
  -e IMAGE_ROOT=/data/images \
  -e DB_ROOT=/data/db \
  -e EXPORT_ROOT=/data/exports \
  -e LOG_ROOT=/data/logs \
  --add-host host-gateway:host-gateway \
  -v /mnt/BigDisk/CryptoBackup/images:/data/images \
  -v /mnt/FastPool/hdd-recovery-db:/data/db \
  -v /mnt/BigDisk/CryptoBackup/exports:/data/exports \
  -v /mnt/BigDisk/CryptoBackup/logs:/data/logs \
  joanmarcriera/hdd-forensics:latest
```

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TTYD_PASSWORD` | **yes** | — | Password for the browser terminal |
| `TTYD_USER` | no | `admin` | Username for the browser terminal |
| `IMAGE_ROOT` | no | `/data/images` | Raw ddrescue image path |
| `DB_ROOT` | no | `/data/db` | SQLite database path |
| `EXPORT_ROOT` | no | `/data/exports` | Recovered outputs path |
| `LOG_ROOT` | no | `/data/logs` | Logs and ddrescue maps path |
| `OLLAMA_HOST` | no | `http://host-gateway:11434` | Primary Ollama API URL |
| `OLLAMA_HOSTS` | no | — | Comma-separated Ollama API URLs |

## Ports

| Port | Service |
|------|---------|
| `7788` | Unified UI, terminal, health, and status |

## Health API

```
GET /health  →  200 {"ok":true} when UI backends are running, 503 otherwise
GET /status  →  JSON with ttyd/web PIDs, restart counts, uptime, and Ollama status
```

## Full documentation

See the GitHub repository for architecture diagrams, the complete analysis workflow, all environment variables, data layout, and security notes:

**https://github.com/joanmarcriera/hdd-forensics**
