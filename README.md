# hdd-forensics

[![Docker Pulls](https://img.shields.io/docker/pulls/joanmarcriera/hdd-forensics)](https://hub.docker.com/r/joanmarcriera/hdd-forensics)
[![Docker Image Size](https://img.shields.io/docker/image-size/joanmarcriera/hdd-forensics/latest)](https://hub.docker.com/r/joanmarcriera/hdd-forensics)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**A self-contained Docker container for hard-disk image forensics, focused on recovering Bitcoin and cryptocurrency wallet artifacts.**

Designed for [TrueNAS SCALE](https://www.truenas.com/truenas-scale/) but works on any Docker host. All analysis runs against `.img` disk image files — original hardware is never touched after imaging.

---

## What it does

- Catalogs every file on a disk image (live, deleted, and orphaned) using **The Sleuth Kit / fiwalk**
- Scores files for crypto-wallet relevance: `wallet.dat`, Electrum databases, Ethereum keystores, known wallet directory structures
- Runs **bulk_extractor** to find Bitcoin addresses, raw private keys, AES keys, and other artifacts directly in the raw image data
- Carves deleted files with **foremost**, **scalpel**, **recoverjpeg**, and **magicrescue**
- Recovers deleted ext3/4 files via **extundelete** and **ext4magic**
- OCRs all recovered images with **Tesseract** and flags sequences of BIP-39 seed words
- Inspects Bitcoin Core `wallet.dat` files with **pywallet** and tracks manual cracking jobs for **john**, **hashcat**, **btcrecover**, and **KeePass**
- Enriches carved files with **TrID**, image quality scores, and perceptual photo deduplication
- Extracts Windows `hiberfil.sys` / `pagefile.sys` and runs focused **Volatility3** memory scans
- Keeps a per-image **SQLite** catalog of every stage: what ran, when, what it found
- Exposes the full interactive **TUI** (Python Textual) via a browser-accessible terminal — no SSH required
- Exposes a read-only web review UI on port `7788` for dashboards, recovered artifacts, picture galleries, timelines, and SQL queries
- Integrates with a local **Ollama** instance for LLM-assisted analysis

---

## Architecture

```
┌─────────────────────────────────────┐       rsync / SSH        ┌─────────────────────────────────────────────┐
│   Imaging machine (Optiplex/Kali)   │ ────────────────────────► │   TrueNAS SCALE (12 CPU / 96 GB / RTX 4060) │
│                                     │                           │                                             │
│   • ddrescue  →  disk.img           │                           │   ZFS pool                                  │
│   • image-analysis-init.sh          │                           │   └── /mnt/BigDisk/CryptoBackup/            │
│   • image-structure-scan.sh         │                           │        ├── recovery/images/*.img            │
│   • send-image-to-truenas.sh        │                           │        ├── recovery/exports/<name>/         │
│                                     │                           │        └── recovery/logs/                   │
│   Plug in the next disk  ──────────►│                           │                                             │
└─────────────────────────────────────┘                           │   Docker container: hdd-forensics           │
                                                                   │   ├── full forensics toolchain             │
                                                                   │   ├── Textual TUI (Python)                 │
                                                                   │   ├── ttyd       →  :7681  (browser)       │
                                                                   │   ├── web UI     →  :7788  (review)        │
                                                                   │   ├── supervisor →  :8080  (health API)    │
                                                                   │   └── Ollama client (via host-gateway)     │
                                                                   └─────────────────────────────────────────────┘
```

The imaging machine does one thing: run `ddrescue` to capture a raw image, then `rsync` it to TrueNAS. TrueNAS runs the Docker container and handles all analysis while the imaging machine moves on to the next disk.

---

## Quick start on TrueNAS SCALE

### Option A — Custom App (recommended)

1. In the TrueNAS web UI go to **Apps → Discover Apps → Custom App**.
2. Set the image to `joanmarcriera/hdd-forensics:latest`.
3. Add environment variables:

   | Variable | Value |
   |----------|-------|
   | `TTYD_PASSWORD` | your chosen password (required) |
   | `OLLAMA_HOST` | `http://host-gateway:11434` |

4. Under **Storage**, add a host path volume:
   - **Host path:** `/mnt/BigDisk/CryptoBackup`
   - **Mount path:** `/mnt/recovery16tb`

5. Under **Networking**, add port forwards:
   - Container port `7681` → Host port `7681` (browser terminal)
   - Container port `7788` → Host port `7788` (read-only web review UI)
   - Container port `8080` → Host port `8080` or another free port (health check)

6. Click **Install**. After about a minute, open `http://truenas-ip:7681` for the browser terminal, or `http://truenas-ip:7788` for the review UI. Log in to the browser terminal with username `admin` and the password you set.

TrueNAS automatically uses `GET /health` on the mapped health port once the container's `8080` port is exposed.

### Option B — docker compose

```bash
# On TrueNAS, open a shell:
git clone https://github.com/joanmarcriera/hdd-forensics.git /mnt/BigDisk/hdd-forensics
cd /mnt/BigDisk/hdd-forensics/docker

cp .env.example .env
# Edit .env: set TTYD_PASSWORD and verify DATA_ROOT

docker compose --env-file .env pull
docker compose --env-file .env up -d
```

Then open `http://truenas-ip:7681` for the browser terminal or `http://truenas-ip:7788` for the review UI.

### Option C — docker run (one-liner)

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

---

## Sending images from the imaging machine

After `ddrescue` finishes on the Kali/Linux imaging machine, initialize and transfer:

```bash
# 1. Initialize the analysis database and scan partition structure
IMAGE=/path/to/disk.img
bin/image-analysis-init.sh "$IMAGE"
bin/image-structure-scan.sh "${IMAGE}.analysis.sqlite"

# 2. Transfer to TrueNAS (SSH key auth required)
#    Usage: send-image-to-truenas.sh <image> <truenas-host> [<remote-data-root>]
bin/send-image-to-truenas.sh "$IMAGE" truenas.local /mnt/BigDisk/CryptoBackup
```

The script:
- Checks ddrescue map coverage and warns if it is below 95%
- Transfers the image file via `rsync --partial` (safely restartable)
- Transfers the SQLite database and ddrescue log files
- Transfers any existing export outputs
- Prints the exact `docker exec` command to start analysis on TrueNAS

---

## Full analysis workflow

All commands run **inside the container** — either via the browser terminal at `http://truenas-ip:7681` or `docker exec -it hdd-forensics bash`. Use `http://truenas-ip:7788` for read-only review of dashboards, artifacts, galleries, timelines, and SQL queries.

```bash
IMAGE=/mnt/recovery16tb/recovery/images/disk.img
DB=${IMAGE}.analysis.sqlite

# ── Fast path (metadata-first, runs in minutes) ──────────────────────────────
bin/image-index-tsk.sh $DB          # filesystem inventory via fiwalk (all files, deleted)
bin/image-detect-wallets.sh $DB     # score files for wallet relevance
bin/image-detect-pictures.sh $DB    # score files for picture relevance

# ── Heavy stages (run overnight or in parallel) ───────────────────────────────
bin/image-bulk-extractor.sh $DB --scope raw       # scan raw image bytes
bin/image-ext-recover.sh $DB                      # ext3/4 deleted file recovery
bin/image-carve.sh $DB --method foremost          # file carving
bin/image-carve.sh $DB --method scalpel
bin/image-carve.sh $DB --method recoverjpeg
bin/image-carve.sh $DB --method magicrescue
bin/image-bulk-extractor.sh $DB --scope recovered # scan carved corpus
bin/image-ntfs-artifact-summary.sh $DB            # Windows/NTFS artifact summary

# ── OCR seed phrase detection ─────────────────────────────────────────────────
bin/image-ocr-seed-scan.py $DB      # OCR all recovered images, flag BIP-39 seeds
bin/image-text-seed-scan.sh $DB --run # scan recovered text files for BIP-39 seeds

# ── Wallet recovery (Wave A, manual cracking stages) ─────────────────────────
bin/image-wallet-inspect.sh $DB --run      # pywallet wallet.dat inspection
bin/image-crack-wallet.sh $DB --run        # bitcoin2john + hashcat -m 11300
bin/image-btcrecover.sh $DB --config <yml> --run
bin/image-crack-keepass.sh $DB --run       # keepass2john/hashcat or keepass4brute

# ── Windows memory + review efficiency (Wave B) ──────────────────────────────
bin/image-enrich-trid.sh $DB --run         # TrID guesses, no file renames
bin/image-enrich-photos.sh $DB --run       # EXIF + quality_score
bin/image-dedup-photos.sh $DB --run        # perceptual duplicate clusters
bin/image-extract-winmem.sh $DB --run      # hiberfil.sys/pagefile.sys extraction
bin/image-volatility-scan.sh $DB --run     # focused Volatility3 plugins
bin/image-plaso.sh $DB --run               # full + crypto keyword timeline

# ── Query results ─────────────────────────────────────────────────────────────
bin/image-query.sh $DB summary
bin/image-query.sh $DB wallets
bin/image-query.sh $DB pictures
bin/image-report.sh $DB
```

**Or run everything at once (unattended):**

```bash
bin/image-bulk-discovery-run.sh /mnt/recovery16tb/recovery/images/disk.img
```

`image-bulk-discovery-run.sh` accepts `--map <ddrescue-map>` and `--with-photorec` flags.

### Stage ordering rule

Always run the metadata-first path (TSK index → wallet/picture detection) before the heavy stages. Never start a second DB-writing stage while one is already running.

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TTYD_PASSWORD` | **yes** | — | Password for the browser terminal |
| `TTYD_USER` | no | `admin` | Username for the browser terminal |
| `TTYD_PORT` | no | `7681` | Container port for the browser terminal |
| `TTYD_CMD` | no | `cd /root/hdd-recovery && exec bin/tui.sh` | Command the terminal runs on each new connection |
| `WEB_PORT` | no | `7788` | Container port for the read-only web review UI |
| `HEALTH_PORT` | no | `8080` | Container port for the health/status API |
| `OLLAMA_HOST` | no | `http://host-gateway:11434` | Ollama API URL (running on the Docker host) |
| `DATA_ROOT` | no | `/mnt/BigDisk/CryptoBackup` | Host path mapped to `/mnt/recovery16tb` in docker-compose |

---

## Included tools

| Category | Tools |
|----------|-------|
| **Disk imaging** | `ddrescue`, `dc3dd` |
| **Filesystem forensics** | `sleuthkit` (fiwalk, mmls, img_stat), `testdisk`, `photorec` |
| **File carving** | `foremost`, `scalpel`, `recoverjpeg`, `magicrescue` |
| **Raw data extraction** | `bulk_extractor` |
| **Deleted file recovery** | `extundelete`, `ext4magic` |
| **Binary analysis** | `binwalk` |
| **Metadata** | `exiftool`, `file`, `sqlite3`, `db5.3-util` (BerkeleyDB / `wallet.dat`) |
| **OCR** | `tesseract-ocr` + English language data + BIP-39 wordlist |
| **Wallet recovery** | `pywallet`, `john`, `bitcoin2john.py`, `hashcat`, `btcrecover`, `keepass2john`, `keepass4brute` |
| **File identification/review** | `TrID`, `imagehash`, Pillow quality scoring |
| **Windows memory** | `Volatility3` |
| **Full-text index** | `recoll` (opt-in via `ENABLE_RECOLL=1`) |
| **Terminal UI** | Python Textual app, served via `ttyd` |
| **Web review UI** | Read-only dashboard, artifact browser, galleries, timeline, and SQL query UI on `WEB_PORT` |
| **Process manager** | Go supervisor (manages ttyd, serves health API) |

> **Cracking GPU setup:** hashcat stages require NVIDIA Container Toolkit and a container started with GPU access, for example `--gpus all`. The helper in `lib/gpu_check.sh` runs `hashcat -I` and hard-fails if no NVIDIA GPU is visible. This prevents silent CPU fallback, which can waste days.

---

## Health API

The Go supervisor exposes a small HTTP API on port `8080` (configurable via `HEALTH_PORT`):

```
GET /health
  200  {"ok":true}
  503  {"ok":false,"error":"ttyd not running"}

GET /status
  200  {
         "ok":          true,
         "ttyd_up":     true,
         "ttyd_pid":    12345,
         "restarts":    0,
         "started_at":  "2026-04-24T09:00:00Z",
         "uptime_s":    3600,
         "ollama_host": "http://host-gateway:11434",
         "ollama_ok":   true,
         "ollama_msg":  "reachable"
       }
```

`GET /` redirects to `/health` (HTTP 302) so TrueNAS health probes that hit the root path also work.

The supervisor restarts ttyd automatically on crash (up to 20 times, with a 5-second backoff). It tests Ollama connectivity at startup and reports it in `/status`.

---

## Ollama integration

If [Ollama](https://ollama.com) is running on the TrueNAS host, the container reaches it via `http://host-gateway:11434` — Docker's built-in alias for the host IP. This is already the default value of `OLLAMA_HOST`.

The supervisor tests Ollama at startup and reports the result in `GET /status`. LLM-assisted analysis (vision models for ambiguous seed-phrase images, text summarization) is available as an opt-in stage.

---

## Pushing updates to Docker Hub

```bash
# Build and tag
docker build -f docker/Dockerfile -t joanmarcriera/hdd-forensics:latest .

# Optional: tag a version
docker tag joanmarcriera/hdd-forensics:latest joanmarcriera/hdd-forensics:1.0.0

# Push
docker login
docker push joanmarcriera/hdd-forensics:latest
docker push joanmarcriera/hdd-forensics:1.0.0
```

For multi-architecture builds (amd64 + arm64):

```bash
docker buildx create --use --name multiarch
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f docker/Dockerfile \
  -t joanmarcriera/hdd-forensics:latest \
  --push .
```

---

## Data layout

The container expects the following structure inside the mounted volume (`/mnt/recovery16tb`). This layout is created automatically when you run `bin/image-analysis-init.sh`.

```
/mnt/recovery16tb/
└── recovery/
    ├── images/                 disk image files (*.img) and SQLite databases
    │   ├── disk.img
    │   └── disk.img.analysis.sqlite
    ├── exports/
    │   └── <image-name>/       per-image analysis outputs
    │       ├── structure/      fdisk / parted / mmls / blkid outputs
    │       ├── recovered/      carved and recovered files by method
    │       ├── indexes/        bulk_extractor output, TSK index
    │       ├── hits/           wallet candidates, OCR seed hits
    │       ├── logs/           per-stage log files
    │       └── reports/        generated summary reports
    └── logs/                   ddrescue map files and rate logs
```

Each image's SQLite database lives beside the image file as `<image>.analysis.sqlite` and contains:

| Table | Contents |
|-------|----------|
| `image_info` | image path, SHA256, size, ddrescue map path, export root |
| `scan_runs` | one row per stage execution: status, timestamps, log path, output dir |
| `partitions` / `filesystems` | structure scan results |
| `files` | full filesystem inventory from fiwalk (paths, inodes, timestamps, deleted flag) |
| `wallet_candidates` | scored wallet hits |
| `picture_candidates` | scored picture hits |
| `recovered_artifacts` | carved/recovered files with SHA256 and MIME type |
| `bulk_extractor_hits` | imported feature-file rows (capped at 5000 per scope) |
| `notes` | timestamped operator notes |
| `exports` | exported files |

---

## Security notes

- The browser terminal requires a password set via `TTYD_PASSWORD`. Use a strong password if TrueNAS is reachable from outside your LAN.
- Consider a reverse proxy with TLS in front of port `7681` for remote access. TrueNAS has a built-in Nginx reverse proxy that can be configured per app.
- The container does not require `--privileged` or any special Linux capabilities. It reads image files exclusively via the volume mount.
- The health API on port `8080` is unauthenticated. Restrict it to your LAN or bind it to a local interface if needed.

---

## Contributing

Issues and pull requests are welcome. This project is actively used for real data recovery work — bug reports with reproduction steps are especially valued.

All analysis runs on image files. The original source disks are never written to or mounted read-write.

---

## License

MIT — see [LICENSE](LICENSE).
