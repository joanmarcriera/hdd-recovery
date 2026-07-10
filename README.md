# hdd-forensics

**Deployed at:** [recover.joanmarcriera.es](https://recover.joanmarcriera.es) (private tool)

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
- Exposes one browser UI on port `7788`: review dashboard at `/`, terminal TUI at `/terminal/`, and health/status at `/health` and `/status`
- Supports separate mount roots for raw images, SQLite catalogs, recovered exports, and logs so TrueNAS can place each workload on the right pool
- Integrates with remote **Ollama** instances for LLM-assisted image tagging

---

## Architecture

```
┌─────────────────────────────────────┐       rsync / SSH        ┌─────────────────────────────────────────────┐
│   Imaging machine (Optiplex/Kali)   │ ────────────────────────► │   TrueNAS SCALE (12 CPU / 96 GB / RTX 4060) │
│                                     │                           │                                             │
│   • ddrescue  →  disk.img           │                           │   Storage pools / datasets                  │
│   • send image to TrueNAS           │                           │   ├── /data/images   raw ddrescue images    │
│                                     │                           │   ├── /data/db       SQLite on fast NVMe    │
│                                     │                           │   ├── /data/exports  recovered data         │
│                                     │                           │   └── /data/logs     maps and stage logs    │
│   Plug in the next disk  ──────────►│                           │                                             │
└─────────────────────────────────────┘                           │   Docker container: hdd-forensics           │
                                                                   │   ├── full forensics toolchain             │
                                                                   │   ├── Textual TUI via /terminal/           │
                                                                   │   ├── review UI via /                     │
                                                                   │   ├── health/status via /health /status    │
                                                                   │   └── Ollama client to remote endpoint(s)  │
                                                                   └─────────────────────────────────────────────┘
```

The imaging machine does one thing: run `ddrescue` to capture a raw image, then copy it to the image dataset. TrueNAS runs the Docker container and handles analysis. SQLite writes can go to NVMe while raw images and recovered outputs stay on large-capacity storage.

---

## Quick start on TrueNAS SCALE

### Option A — Custom App (recommended)

1. In the TrueNAS web UI go to **Apps → Discover Apps → Custom App**.
2. Set the image to `joanmarcriera/hdd-forensics:latest`.
3. Add environment variables:

   | Variable | Value |
   |----------|-------|
   | `TTYD_PASSWORD` | your chosen password (required) |
   | `OLLAMA_HOST` | `http://host-gateway:11434` or a LAN URL |
   | `IMAGE_ROOT` | `/data/images` |
   | `DB_ROOT` | `/data/db` |
   | `EXPORT_ROOT` | `/data/exports` |
   | `LOG_ROOT` | `/data/logs` |

4. Under **Storage**, add four host path volumes:
   - Raw images: host `/mnt/BigDisk/CryptoBackup/images` → container `/data/images`
   - SQLite DB: host `/mnt/FastPool/hdd-recovery-db` → container `/data/db`
   - Recovered exports: host `/mnt/BigDisk/CryptoBackup/exports` → container `/data/exports`
   - Logs/maps: host `/mnt/BigDisk/CryptoBackup/logs` → container `/data/logs`

5. Under **Networking**, add one port forward:
   - Container port `7788` → Host port `7788`

6. Click **Install**. After about a minute, open `http://truenas-ip:7788/` for the review UI or `http://truenas-ip:7788/terminal/` for the TUI terminal. Log in with username `admin` and the password you set.

Use `GET /health` on the same mapped UI port for the TrueNAS health probe.

### Option B — docker compose

```bash
# On TrueNAS, open a shell:
git clone https://github.com/joanmarcriera/hdd-forensics.git /mnt/BigDisk/hdd-forensics
cd /mnt/BigDisk/hdd-forensics/docker

# Edit .env or export variables: set TTYD_PASSWORD and host mount paths

docker compose --env-file .env pull
docker compose --env-file .env up -d
```

Then open `http://truenas-ip:7788/` for the review UI or `http://truenas-ip:7788/terminal/` for the terminal TUI. The review UI reuses `TTYD_USER`/`TTYD_PASSWORD` unless `WEBUI_USER`/`WEBUI_PASSWORD` are set separately.

### Option C — docker run (one-liner)

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

---

## Sending images from the imaging machine

After `ddrescue` finishes on the Kali/Linux imaging machine, initialize and transfer:

```bash
# 1. Initialize the analysis database and scan partition structure
IMAGE=/path/to/disk.img
DB="$(bin/image-analysis-init.sh "$IMAGE" --print-db-path)"
bin/image-structure-scan.sh "$DB"

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

All commands run **inside the container** — either via the browser terminal at `http://truenas-ip:7788/terminal/` or `docker exec -it hdd-forensics bash`. Use `http://truenas-ip:7788/` for read-only review of dashboards, artifacts, galleries, timelines, and SQL queries.

```bash
IMAGE=/data/images/disk.img
DB=/data/db/disk.img.analysis.sqlite

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
bin/image-bulk-discovery-run.sh /data/images/disk.img
```

`image-bulk-discovery-run.sh` accepts `--map <ddrescue-map>` and `--with-photorec` flags.

### Stage ordering rule

Always run the metadata-first path (TSK index → wallet/picture detection) before the heavy stages. Never start a second DB-writing stage while one is already running.

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TTYD_PASSWORD` | **yes** | — | Password for the browser terminal and, by default, the review UI |
| `TTYD_USER` | no | `admin` | Username for the browser terminal and, by default, the review UI |
| `WEBUI_PASSWORD` | no | `TTYD_PASSWORD` | Optional separate HTTP Basic auth password for the review UI |
| `WEBUI_USER` | no | `TTYD_USER` | Optional separate HTTP Basic auth username for the review UI |
| `UI_PORT` | no | `7788` | Host-side UI port in docker compose |
| `TTYD_INTERNAL_PORT` | no | `17681` | Internal localhost-only ttyd port used behind `/terminal/` |
| `TTYD_CMD` | no | `cd /root/hdd-recovery && exec bin/tui.sh` | Command the terminal runs on each new connection |
| `WEB_INTERNAL_PORT` | no | `17788` | Internal localhost-only `image-serve.py` port |
| `IMAGE_ROOT` | no | `/data/images` | Container path for raw ddrescue images |
| `DB_ROOT` | no | `/data/db` | Container path for per-image SQLite catalogs |
| `EXPORT_ROOT` | no | `/data/exports` | Container path for recovered files and reports |
| `LOG_ROOT` | no | `/data/logs` | Container path for ddrescue maps and stage logs |
| `OLLAMA_HOST` | no | `http://host-gateway:11434` | Primary remote Ollama API URL |
| `OLLAMA_HOSTS` | no | — | Comma-separated Ollama API URLs for parallel image tagging |

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
| **Terminal UI** | Python Textual app, served via `ttyd` under `/terminal/` |
| **Web review UI** | Read-only dashboard, artifact browser, galleries, timeline, and SQL query UI at `/` |
| **Process manager** | Go supervisor (single public UI port, internal ttyd/web backends, health/status endpoints) |

> **Cracking GPU setup:** hashcat stages require NVIDIA Container Toolkit and a container started with GPU access, for example `--gpus all`. The helper in `lib/gpu_check.sh` runs `hashcat -I` and hard-fails if no NVIDIA GPU is visible. This prevents silent CPU fallback, which can waste days.

---

## Health and Status

The Go supervisor exposes health and status on the same UI port:

```
GET /health
  200  {"ok":true}
  503  {"ok":false,"error":"ui backend not running"}

GET /status
  200  {
         "ok":          true,
         "ttyd_up":     true,
         "ttyd_pid":    12345,
         "restarts":    0,
         "started_at":  "2026-04-24T09:00:00Z",
         "uptime_s":    3600,
         "ollama_host": "http://host-gateway:11434",
         "ollama_hosts": ["http://host-gateway:11434"],
         "ollama_ok":   true,
         "ollama_msg":  "reachable"
       }
```

The supervisor restarts ttyd and the review UI automatically on crash (up to 20 times, with a 5-second backoff). It tests configured Ollama endpoint connectivity at startup and reports it in `/status`.

---

## Ollama integration

If [Ollama](https://ollama.com) is running on the TrueNAS host, the container can reach it via `http://host-gateway:11434` where Docker supports the host-gateway alias. For remote LAN Ollama servers, set `OLLAMA_HOST=http://192.168.x.x:11434`.

For multiple Ollama workers, set:

```bash
OLLAMA_HOSTS=http://ollama-a:11434,http://ollama-b:11434
```

`bin/image-tag-photos.py` accepts the same comma-separated list via `--ollama` and defaults its worker count to the number of configured endpoints.

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

The container uses four independent roots. Mount them to storage with the right performance profile:

```
/data/images/                  raw disk image files (*.img)
  disk.img
/data/db/                      SQLite catalogs on fast storage
  disk.img.analysis.sqlite
/data/exports/<image-name>/    per-image analysis outputs
  structure/                   fdisk / parted / mmls / blkid outputs
  recovered/                   carved and recovered files by method
  indexes/                     bulk_extractor output, TSK index
  hits/                        wallet candidates, OCR seed hits
  logs/                        per-stage log files
  reports/                     generated summary reports
/data/logs/                    ddrescue map files and rate logs
```

For `disk.img`, the default SQLite path is `/data/db/disk.img.analysis.sqlite`. The database records the raw image path and export root in `image_info`.

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

## Documentation

Full guides live under [`docs/`](docs/) ([index](docs/README.md)):

- **Operator** — [acquisition checklist](docs/operator/acquisition-checklist.md), [ddrescue workflow](docs/operator/ddrescue-workflow.md), [next-disk checklist](docs/operator/future-disk-checklist.md)
- **Analysis** — [image-analysis workflow](docs/analysis/image-analysis-workflow.md), [bulk-discovery runbook](docs/analysis/bulk-discovery-runbook.md)
- **Recovery** — [wallets](docs/recovery/wallets.md), [pictures](docs/recovery/pictures.md)
- **Reference** — [tool selection](docs/reference/tool-selection.md)
- **Internal** (design notes, history) — [`docs/internal/`](docs/internal/)

---

## Security notes

- Report vulnerabilities privately — see [SECURITY.md](SECURITY.md).
- **Intended use:** authorized recovery of your own data and authorized forensic/security work only. This toolchain extracts private keys, passwords, and personal files; do not point it at media you are not authorized to examine.
- The browser terminal under `/terminal/` requires a password set via `TTYD_PASSWORD`. The review UI also requires HTTP Basic auth when `TTYD_PASSWORD` or `WEBUI_PASSWORD` is set. Use strong credentials even on a LAN.
- Treat the unified UI as LAN-only. If you expose it beyond the LAN, put a real reverse proxy with TLS in front of port `7788`.
- The container does not require `--privileged` or any special Linux capabilities. It reads image files exclusively via the volume mount.
- `/health` and `/status` are unauthenticated and intended for LAN health probes.

---

## Contributing

Issues and pull requests are welcome. This project is actively used for real data recovery work — bug reports with reproduction steps are especially valued.

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, the test/lint commands, and the safety conventions every change must follow (notably: scripts default to a preview and require an explicit `--run`; source disks are never written to). By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## License

MIT — see [LICENSE](LICENSE).
