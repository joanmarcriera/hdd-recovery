# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Purpose

Acquire full-disk raw images from old HDDs to a local 16 TB destination disk (`/mnt/recovery16tb`), then perform all analysis on image files — never on original source media. Primary recovery goals include Bitcoin wallet artifacts and picture/photo recovery.

## Hardware Context

- OS disk: `/dev/sda` (Samsung SSD, do not touch)
- Destination: `/dev/sdc1` → `/mnt/recovery16tb` (ext4, label `RECOVERY16TB`, UUID `c2244b9a-26b3-4353-bc87-5be944139157`)
- Source disks connect to remaining SATA ports and must be identified by model/serial/by-path before use
- `/dev/sdb` is a ZFS member — do not format, mount, or alter it

## Safety Rules (Non-Negotiable)

- Never write to original source disks
- Never mount source disks read-write
- Never mount source disks before imaging unless there is a deliberate reason
- If disk identity is ambiguous, stop and verify via `lsblk`, model, serial, and `/dev/disk/by-path`
- Do not start SMART self-tests during imaging
- Do not delete, compact, vacuum, deduplicate, normalize, or clean recovery outputs unless explicitly asked
- Do not start a new DB-writing analysis stage while a long-running writer is still active
- Do not clean stale `scan_runs` rows or duplicate DB entries before human review

## Evidence Preservation Rules

- All outputs are additive — never overwrite existing recovery directories in-place
- Re-running a stage that would overwrite output backs up the old directory with a timestamp suffix (see `image-bulk-extractor.sh`)
- Keep provenance by method: `ddrescue`, `TSK/fiwalk`, `extundelete`, `ext4magic`, `foremost`, `scalpel`, `PhotoRec`, `bulk_extractor`, `Recoll`, NTFS artifacts
- One SQLite database per image, stored beside the image: `<image>.analysis.sqlite`
- Do not trust wallet keyword hits without inspecting path and file content

## Key Directories

```
/root/hdd-recovery/          # this repo: scripts, configs, docs, job files
/root/hdd-recovery/bin/      # all analysis and acquisition scripts
/root/hdd-recovery/lib/      # shared bash library (common.sh)
/root/hdd-recovery/sql/      # SQLite schema (analysis-schema.sql)
/root/hdd-recovery/config/   # pipeline env, keyword lists, tool configs
/root/hdd-recovery/jobs/     # per-disk ddrescue job configs

/mnt/recovery16tb/recovery/images/      # raw disk images (*.img)
/mnt/recovery16tb/recovery/logs/        # ddrescue maps, rate logs, event logs
/mnt/recovery16tb/recovery/exports/     # per-image analysis outputs
/mnt/recovery16tb/recovery/manifests/   # source disk manifests

/root/recovery-monitor/      # tmux monitoring helpers
```

## Per-Image Artifact Layout

For an image named `foo.img`:
- Database: `foo.img.analysis.sqlite` (beside the image)
- Export root: `/mnt/recovery16tb/recovery/exports/foo/`
  - `structure/` — fdisk, parted, mmls, img_stat, blkid outputs
  - `recovered/` — carving and recovery tool outputs by method subdirectory
  - `indexes/bulk_extractor_raw/`, `indexes/bulk_extractor_recovered/`
  - `logs/` — per-stage log files
  - `reports/` — generated reports
  - `hits/`, `state/`, `exports/`

## SQLite Schema

`lib/common.sh` initializes the DB from `sql/analysis-schema.sql` on every `ensure_db` call (idempotent). Key tables:

- `image_info` — one row (id=1), image path, SHA256, size, ddrescue map path, export root
- `scan_runs` — one row per stage execution: stage name, status (`running`/`ok`/`partial`/`failed`), timestamps, command line, log path, output dir
- `partitions`, `filesystems` — structure scan results
- `files` — filesystem-aware file inventory (TSK/fiwalk); includes deleted files and metadata
- `wallet_candidates`, `picture_candidates` — scored hits from detection scripts
- `recovered_artifacts` — files registered from carving/PhotoRec/ext-recovery, with SHA256
- `bulk_extractor_hits` — imported feature-file rows (capped per scope by `BULK_HIT_LIMIT`)
- `exports`, `notes`

## Shared Library: lib/common.sh

Every bin script sources this. Key functions:

- `record_scan_start` / `record_scan_end` — write to `scan_runs`
- `register_artifacts_from_dir` — walks a directory, SHA256s files, upserts into `recovered_artifacts` via inline Python
- `ensure_db` — creates/migrates schema idempotently
- `with_log` — tee stdout+stderr to a log file
- `default_db_path` — derives `<image>.analysis.sqlite`
- `default_export_root` — derives `/mnt/recovery16tb/recovery/exports/<basename>`
- `ensure_work_dirs` — creates the full export subdirectory tree

## Scripts Reference

### Acquisition

| Script | Purpose |
|--------|---------|
| `bin/ddrescue-run.sh <job.conf> <phase> [--run]` | Preview or run ddrescue passes; phases: `plan`, `first`, `retry`, `reverse`, `retrim`. Defaults to preview; requires `--run` to execute. Refuses if source has mounted partitions. |
| `bin/ddrescue-status.sh <mapfile>` | Summary of ddrescue map: coverage, remaining regions |
| `bin/ddrescue-job-template.conf` | Template for per-disk job configs |

### Analysis — Fast Path (Metadata-First)

| Script | Purpose |
|--------|---------|
| `bin/image-process.sh <image> [--with-ext] [--with-bulk] [--with-carve] [--with-recoll]` | Orchestrates fast path (init → structure → TSK index → wallet detection → picture detection → report); flags opt in to heavy stages |
| `bin/image-analysis-init.sh <image> [--map <mapfile>] [--hash]` | Creates/updates DB and export dir tree |
| `bin/image-structure-scan.sh <db>` | Runs fdisk, parted, mmls, img_stat, blkid; requires `--force` to re-run after indexing |
| `bin/image-index-tsk.sh <db>` | Filesystem-aware file inventory via fiwalk; populates `files` table |
| `bin/image-detect-wallets.sh <db>` | Scores files against wallet keywords/extensions; populates `wallet_candidates` |
| `bin/image-detect-pictures.sh <db>` | Scores files for picture extensions/paths; populates `picture_candidates` |

### Wallet Recovery

| Script | Purpose |
|--------|---------|
| `bin/image-wallet-inspect.sh <db> --run` | pywallet inspection of wallet.dat candidates; writes `wallet_keys` and findings |
| `bin/image-crack-wallet.sh <db> --run` | Manual bitcoin2john/hashcat wallet.dat cracking; GPU preflight required unless explicit CPU fallback |
| `bin/image-btcrecover.sh <db> --config <yml> --run` | Operator-driven btcrecover for partial seed/password recovery; tracks `crack_tasks` |
| `bin/image-crack-keepass.sh <db> --run` | KeePass KDBX cracking via keepass2john/hashcat or explicit keepass4brute CPU path |

### Analysis — Heavy Stages

| Script | Purpose |
|--------|---------|
| `bin/image-ext-recover.sh <db>` | ext3/ext4 journal-aware deleted file recovery (extundelete/ext4magic) |
| `bin/image-bulk-extractor.sh <db> --scope raw\|recovered` | Run bulk_extractor on raw image or recovered corpus; backs up prior output with timestamp; imports hits into DB up to `BULK_HIT_LIMIT` |
| `bin/image-carve.sh <db> --method foremost\|scalpel` | Carving pass; registers artifacts in DB afterward |
| `bin/image-photorec-run.sh <db> --profile broad\|photos` | Unattended PhotoRec via `/cmd`; output under `recovered/photorec/<profile>-<timestamp>` |
| `bin/image-text-seed-scan.sh <db> --run` | Scans recovered text-like files for BIP39 seed phrase runs |
| `bin/image-enrich-trid.sh <db> --run` | TrID file-type enrichment stored in DB only; never renames carved files |
| `bin/image-enrich-photos.sh <db> --run` | EXIF enrichment plus image `quality_score` population |
| `bin/image-dedup-photos.sh <db> --run` | Per-image perceptual photo deduplication; marks cluster primaries |
| `bin/image-extract-winmem.sh <db> --run` | Extracts `hiberfil.sys` and `pagefile.sys` to `winmem/` using TSK inode data |
| `bin/image-volatility-scan.sh <db> --run` | Runs focused Volatility3 plugins over extracted Windows memory artifacts |
| `bin/image-ntfs-artifact-summary.sh <db>` | Extracts Windows path/prefetch/MFT interest from raw bulk_extractor output; requires `--scope raw` to have run first |
| `bin/image-index-recoll.sh <db> --path <dir>` | Full-text Recoll index over a recovered directory; disabled by default (`ENABLE_RECOLL=0`) |
| `bin/image-bulk-discovery-run.sh <image>` | One-shot heavy pipeline runner |

### Query and Export

| Script | Purpose |
|--------|---------|
| `bin/image-query.sh <db> summary\|wallets\|pictures\|files-like <pat>\|artifacts <method>\|sql <stmt>` | Read-only DB queries |
| `bin/image-status.sh <db>` | Quick columnar view of image info, scan_runs, and counts |
| `bin/image-report.sh <db>` | Generate summary report to `reports/` |
| `bin/image-export.sh <db> --file-id <id>` | Copy a specific file to `exports/` |
| `bin/image-attach-ro.sh` / `bin/image-detach.sh` | Mount/unmount image read-only via loop device |

### Consolidation / Export (cross-image)

The only cross-image tools — they walk **every** `*.analysis.sqlite` under a root (reusing `find_databases`). `lib/harvest.py` holds the single shared definition of a "curated photo" (dedup primaries, size ≥ 20 KB, `quality_score` ≥ 30, jpeg/png/heic/heif/tiff/webp only) and a "curated document" (pdf/office/rtf/epub by MIME or TrID; `text/plain` opt-in), so inventory counts can never drift from what an exporter ships. Read-only against source images/recovered files; exporters write only external services + an `exports` provenance row and are resumable.

| Script | Purpose |
|--------|---------|
| `bin/inventory-summary.py [--root <dir>] [--out-dir <dir>]` | Cross-image inventory: totals + per-disk breakdown + top wallets, wallet/seed findings, encrypted containers, unique bulk_extractor values. Writes additive `reports/inventory-<ts>.{md,json,csv}` (`lib/inventory.py`). Served at `/inventory`. |
| `bin/immich-export.py [--db <path>] [--run] [--min-quality N] [--limit N]` | Upload curated photos to Immich, one album per disk. Dry-run by default; `--run` needs `IMMICH_INSTANCE_URL` + `IMMICH_API_KEY` (env only). Raw images never uploaded. |
| `bin/docspell-export.py [--db <path>] [--run] [--include-text] [--limit N]` | Upload curated documents to Docspell, tagged by disk, sha256-deduped across disks. Dry-run by default; `--run` needs `DOCSPELL_URL` + `DOCSPELL_SOURCE_ID` (or `DOCSPELL_COLLECTIVE` + `DOCSPELL_INTEGRATION_SECRET`). Helpers in `lib/upload_http.py` / `lib/exportlog.py`. |

### Web UI (`bin/image-serve.py`)

Started via the TUI or `python3 bin/image-serve.py --port 7788`. Routes:

| Route | Purpose |
|-------|---------|
| `/` | Home: all databases with file/artifact/wallet counts and a map icon link |
| `/inventory` | Cross-image totals, per-disk breakdown, and top wallet/encrypted hits across all databases (mirrors `bin/inventory-summary.py`) |
| `/db?db=<path>` | Database detail: image info, stage history, quick links |
| `/mapview?db=<path>` | ddrescue map visualiser — reads `ddrescue_map_path` from `image_info` |
| `/mapview?map=<path>` | ddrescue map visualiser — direct mapfile path, no DB required |
| `/wallets`, `/pictures`, `/artifacts`, `/bulk_hits`, `/findings` | Tabular result views |
| `/timeline?db=<path>` | Event timeline from `image-timeline.sh` |
| `/search?db=<path>` | Filename search across `files` table |
| `/sql?db=<path>` | Read-only SQL console (SELECT/WITH only) |

#### Image gallery (`/gallery`)

Linked from the Pictures page. Paginated (48/page) or all-on-one-page view with lazy loading. Features:
- **Hover overlay**: llava description shown over thumbnail on mouse hover (CSS only, no JS)
- **Tag count banner**: shows how many images have been tagged by llava out of the total
- **Description search**: filters gallery to only images whose llava description matches the query
- **View all / Paginated** toggle button

#### LLM photo tagging (`bin/image-tag-photos.py`)

Tags recovered images using a local Ollama vision model (default: `llava:7b`). Descriptions are stored in the `findings` table (`source_tool='llava'`, `category='photo-description'`). The job is fully resumable — already-tagged images are skipped.

```bash
# Tag real photos only (JPEG >= 20 KB) — ~3.5 h for 525 images at 21 s/image
bin/image-tag-photos.py <db> --ollama http://<host>:11434

# Dry-run: see candidates without calling Ollama
bin/image-tag-photos.py <db> --dry-run

# Tag all image/* types (includes web cache PNGs/BMPs/GIFs >= 10 KB)
bin/image-tag-photos.py <db> --scope all

# Options
#   --min-size N     override minimum file size (bytes)
#   --limit N        stop after N images (testing)
#   --force          re-tag already-tagged images
#   --model NAME     Ollama model (default: llava:7b)
#   --prompt TEXT    custom prompt
```

The findings table is created automatically if absent. Progress is tracked in `scan_runs`. Interrupt with Ctrl-C and re-run to resume — already-tagged images are skipped.

#### ddrescue map visualiser (`/mapview`)

Parses GNU ddrescue mapfile format and renders an SVG block grid (~5000 cells) colour-coded by status:

| Colour | Status char | Meaning |
|--------|-------------|---------|
| Green `#22aa44` | `+` | rescued — successfully read |
| Dark blue `#334466` | `-` | non-tried — not yet attempted |
| Yellow `#cc9900` | `/` | non-trimmed — read errors, not yet trimmed |
| Orange `#dd6600` | `*` | non-scraped — trimmed but not scraped |
| Red `#cc2222` | `?` | bad-sector — unrecoverable |

Each display cell covers `total_size / 5000` bytes. When multiple ddrescue blocks fall in the same cell, the worst status wins (bad-sector > non-scraped > non-trimmed > non-tried > rescued). The page also shows byte-level coverage statistics with inline progress bars.

The link appears automatically on the home page (📄 icon) and the DB detail page whenever `ddrescue_map_path` in `image_info` points to an existing file.

### Monitoring

```bash
/root/start-recovery-monitor.sh                    # launch tmux session
ANALYSIS_DB=<db> /root/start-recovery-monitor.sh  # pin to a specific image DB
tmux attach -t recovery-monitor
```

Monitor helper scripts under `/root/recovery-monitor/`:
- `show-analysis-status.sh` — scan_runs table view
- `show-analysis-outputs.sh` — output directory sizes
- `tail-analysis-logs.sh` — tail per-stage logs
- `show-latest-ddrescue-map-status.sh` — ddrescue map coverage
- `tail-latest-ddrescue-log.sh`
- `show-mount-status.sh`, `show-recovery-context.sh`
- `smart-snapshot.sh`, `smart-lite-all.sh`

## Standard Workflow

### Imaging a New Disk

```bash
# 1. Identify source — verify model, serial, by-path
lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT,MODEL,SERIAL,ROTA,TRAN,STATE
ls -l /dev/disk/by-path

# 2. Create job config
cp bin/ddrescue-job-template.conf jobs/<name>.conf
# Fill SOURCE_DEV, SOURCE_BY_PATH, SOURCE_MODEL, SOURCE_SERIAL, SOURCE_SIZE_BYTES, BASENAME

# 3. Preview command (never skips this)
bin/ddrescue-run.sh jobs/<name>.conf first

# 4. Run
bin/ddrescue-run.sh jobs/<name>.conf first --run

# 5. Check remaining unread areas
bin/ddrescue-status.sh /mnt/recovery16tb/recovery/logs/<basename>.map

# 6. If unread areas remain
bin/ddrescue-run.sh jobs/<name>.conf retry --run
bin/ddrescue-run.sh jobs/<name>.conf reverse --run
bin/ddrescue-run.sh jobs/<name>.conf retrim --run
```

### Analyzing an Image

```bash
IMAGE=/mnt/recovery16tb/recovery/images/<basename>.img
DB=${IMAGE}.analysis.sqlite

# Fast metadata-first path
bin/image-process.sh $IMAGE

# Query results
bin/image-query.sh $DB summary
bin/image-query.sh $DB wallets
bin/image-query.sh $DB pictures

# If metadata results are sparse — add heavy stages
bin/image-bulk-extractor.sh $DB --scope raw
bin/image-ext-recover.sh $DB
bin/image-carve.sh $DB --method foremost
bin/image-carve.sh $DB --method scalpel
bin/image-bulk-extractor.sh $DB --scope recovered
bin/image-ntfs-artifact-summary.sh $DB
bin/image-photorec-run.sh $DB --profile broad
bin/image-report.sh $DB
```

### Consolidating Results Across Images

Once several images are analysed, roll the whole haul up and push the good stuff into the tools where it's useful. All read-only against source media; exporters are dry-run by default and resumable (skip anything already in the `exports` table).

```bash
bin/inventory-summary.py --root "$DB_ROOT"                   # reports/inventory-<ts>.{md,json,csv}; also /inventory
bin/immich-export.py --root "$DB_ROOT"                       # dry-run: counts + manifest
IMMICH_INSTANCE_URL=http://nas:2283 IMMICH_API_KEY=… bin/immich-export.py --db "$DB" --run --limit 5
bin/docspell-export.py --root "$DB_ROOT"                     # dry-run
DOCSPELL_URL=http://nas:7880 DOCSPELL_SOURCE_ID=… bin/docspell-export.py --db "$DB" --run --limit 5
```

Secrets come from the environment only (never commit them).

### Stage Ordering Rule

Always: fast metadata-first → heavy deleted/free-space stages. Never run carving before filesystem-aware indexing. Do not start a new DB-writing stage while another is still running.

## Configuration

`config/analysis-pipeline.env` is sourced by `lib/common.sh` and can be overridden with `HDD_RECOVERY_CONFIG=<path>`. Key variables:

- `IMAGE_ROOT`, `EXPORT_ROOT`, `LOG_ROOT` — base directories
- `DB_SUFFIX` — default `.analysis.sqlite`
- `BULK_HIT_LIMIT` — max rows imported per bulk_extractor feature file (default 5000)
- `ENABLE_RECOLL` — set to `1` to enable Recoll indexing stage
- `WALLET_KEYWORDS_FILE`, `INTEREST_KEYWORDS_FILE`, `PICTURE_EXTENSIONS`

## Lessons Learned (Hitachi Run)

- **bulk_extractor `--scope recovered` can hang**: it may spin at 100% CPU with no output growth during a long finalization phase. Monitor `du -sh` on the output dir; if size stops growing for an extended period, treat as partial and check the scan_runs status.
- **PhotoRec unattended mode**: this installed build supports `/cmd`; `image-photorec-run.sh` uses it. Output lands under `recovered/photorec/<profile>-<timestamp>`. If the build rejects `/cmd`, the script will fail quickly and log it — fall back to interactive `photorec`.
- **NTFS/Windows artifacts from raw bulk_extractor**: can reveal prior Windows use even when the current filesystem is ext4. Always run `image-ntfs-artifact-summary.sh` after the raw bulk_extractor pass when investigating disks with unknown history.
- **eMule/aMule `.part.met` files**: `seeds` in `.part.met.seeds` likely refers to download peer-source metadata, not wallet seed phrases. `.part.met` sidecar files are more useful than `.part.met.seeds` — they can reveal the original filename of the payload.
- **Wallet hits are candidates only**: keyword scoring on filenames and paths produces many false positives. Terms like `seeds`, `wallet`, `backup` are common in non-crypto contexts. Content inspection is required before any meaningful conclusion.
- **Carving produces duplicates and false positives**: keep all outputs by method. Do not deduplicate or cross-compare until human review. Web cache files, thumbnails, and application assets are common noise.
- **Do not clean up stale scan_runs or duplicate DB rows**: leave the DB as-is for human review. Existing rows document what was actually run, including partial or failed stages.

## Conventions for New Code

- All bash scripts source `lib/common.sh` and call `record_scan_start` / `record_scan_end` to write to `scan_runs`
- Scripts default to preview/dry-run and require an explicit `--run` flag for execution
- Output directories are created fresh or backed up with timestamp suffixes — never silently overwritten
- `register_artifacts_from_dir` handles artifact registration with SHA256 and mime detection via inline Python
- New recovery stages must add a `scan_runs` row; do not write recovery outputs to DB without tracking
- SQLite writes use `sql_escape` from `common.sh`; avoid raw string interpolation into SQL
- Scripts use `set -Eeuo pipefail` and `need_cmd` checks for external tools

## TUI Design Requirements

The planned TUI guides an operator through one disk at a time — it does not automate recovery, it presents a checklist.

### State Model

The TUI must inspect actual system state rather than trust a separate state file:

| Source | What it tells you |
|--------|-------------------|
| ddrescue map file | imaging coverage and unread regions |
| job config (`jobs/*.conf`) | which source disk, basename, paths |
| `image_info` table | image registered, export root |
| `scan_runs` table | which stages ran, their status, log/output paths |
| output directories | physical presence of recovered files |
| log files | last-modified timestamp, size |
| `pgrep` / `/proc` | whether a recovery process is live right now |

### Stage Status Values

Each checklist stage should display one of: `pending`, `running`, `done`, `partial`, `failed`, `skipped`

### Per-Stage Display Fields

For each stage:
- Stage name and description
- Script/command that runs it (shown verbatim, never hidden)
- Current status (derived from scan_runs + filesystem check)
- Log file location
- Output directory location
- Rough runtime expectation
- Whether it is safe to rerun (some stages back up previous output; others require `--force`)
- Human decision required before continuing (e.g., review wallet hits before carving)
- Warning banner for destructive or long-running steps

### Checklist Stage Order

1. Identify source disk
2. Create/verify job config
3. Preview ddrescue command
4. Run ddrescue first pass
5. Check ddrescue map/status
6. Optional retry/reverse/retrim passes
7. Initialize image analysis DB
8. Run fast metadata-first scan (`image-process.sh`)
9. Run ext recovery (if ext3/ext4 applicable)
10. Run raw bulk_extractor
11. Run foremost
12. Run scalpel
13. Run recovered-corpus bulk_extractor
14. Run Recoll index
15. Run NTFS artifact summary (if Windows/NTFS artifacts found)
16. Run PhotoRec broad recovery
17. Generate report
18. Review outputs
19. Record notes and next actions

### Implementation Notes

- Python preferred (bash acceptable for MVP)
- Terminal-only; must work over SSH and inside tmux
- Prefer `curses`, `textual`, or `rich` — all available on Kali; document any added dependency
- The TUI generates commands for operator review and runs them only after explicit confirmation
- It does not hide raw commands
- It reads `scan_runs` for stage status and supplements with directory/log existence checks and `pgrep` for live processes
- Recoll stage is opt-in (`ENABLE_RECOLL=1`); TUI should reflect that


<claude-mem-context>
# Memory Context

# [hdd-recovery] recent context, 2026-05-18 11:52am GMT+1

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (17,658t read) | 992,674t work | 98% savings

### May 16, 2026
393 9:45p 🔵 docker/.env.example Contains All Stale Variables: TTYD_PORT, WEB_PORT, HEALTH_PORT, DATA_ROOT
394 " 🔴 docker/.env.example Updated: Stale Port Vars Removed, Four Storage Root Vars Added
395 9:46p 🔵 send-image-to-truenas.sh Has Two Stale References: LOG_ROOT Fallback and Port 7681 URL
396 " 🔵 send-image-to-truenas.sh Patch Failed: apply_patch Could Not Find Expected Lines
397 " 🔴 send-image-to-truenas.sh Updated: Four-Root Remote Layout and Port 7788 URLs
398 " 🔴 send-image-to-truenas.sh: container_db Path Now Respects DB_SUFFIX
399 9:47p 🔴 tui/config.py: Storage Root Vars Now Read Process Env First, Then Config File
400 " 🔵 All Modified Python Files Compile Without Errors
401 9:48p ✅ Implementation Steps 1–4 Complete; Documentation (Step 5) In Progress
402 " 🔄 Port Consolidation: Three Ports Merged into Single UI Port 7788
403 " 🟣 Four-Mount Storage Architecture for Independent Storage Pool Assignment
404 " 🟣 Multi-Instance Ollama Support via OLLAMA_HOSTS for Parallel Image Tagging
405 " 🔄 send-image-to-truenas.sh Remote Directory Layout Updated to Match New Four-Mount Structure
406 " 🔴 tui/config.py: Environment Variables Now Take Precedence Over .env File Values
407 " ✅ Final Verification: All Tests Pass, Code Compiles, No Stale Refs
408 9:49p ✅ Implementation Complete: 56 Files Changed, All Checks Pass, No Whitespace Errors
409 " 🔴 T14-ollama-hosts.sh Sets PYTHONDONTWRITEBYTECODE to Prevent __pycache__ Pollution
410 " 🔴 T14 Passes Without Generating __pycache__; Working Directory Is Clean
411 " ✅ All Final Verification Checks Pass: Implementation Fully Complete
412 9:51p 🔴 tui/config.py: DB_ROOT Default Changed from Empty String to /data/db
413 " 🔵 docker-compose.yml Final State Confirmed: Clean Four-Root Architecture
414 9:52p ✅ UI_PORT Removed from TrueNAS Custom App Env Var Tables in README.md and docker/README.md
415 " ✅ All Verification Checks Pass on Final State: Implementation Ready to Commit
416 " 🔴 send-image-to-truenas.sh: Legacy DB Path Fallback Added for Existing Deployments
417 9:53p ✅ Bash Syntax Check Passes for All Shell Scripts Including New Smoke Tests
418 " ✅ All Six Implementation Plan Steps Marked Completed
419 " ✅ Final Changeset Summary: 56 Files, 626 Insertions, 399 Deletions
### May 17, 2026
420 8:03a 🔵 Disk Image Scanner Project — Architecture Overview and Refactor Goals
S32 Disk Image Scanner Refactor — Unify dual-port Docker interfaces and add configurable external storage paths for SQLite DB and image output directory (May 17 at 8:03 AM)
S31 Refactor HDD recovery Docker container: merge dual-port interfaces into single port, add configurable storage paths for TrueNAS NVMe/ZFS separation (May 17 at 8:03 AM)
421 12:25p 🔵 Disk Image Scanner Project Architecture Review
462 2:48p 🔵 HDD Recovery Repo Structure Mapped
463 " 🔵 TUI is a Full Python Textual Application
464 " 🔵 image-pipeline.py: Sequential CLI Pipeline Orchestrator with Presets
465 " 🔵 image-process.sh: Legacy Shell Pipeline Wrapper
466 2:49p 🔵 SQLite Analysis Schema: 14 Tables Covering Full Recovery Lifecycle
467 " 🔵 Go Supervisor: Process Manager and Reverse Proxy for Container
468 " 🔵 48-Stage Pipeline: Full Workflow from Acquisition to Wallet Cracking
469 " 🔵 image-serve.py: 2007-Line Web UI with Pipeline Spawning and SQL Console
470 " 🔵 Wallet Recovery Chain: detect → inspect → crack with Full DB Tracking
471 " 🔵 YARA Scan: Hardcoded Score Map for 10 Rule Names
472 " 🔵 Smoke Tests: 14 Shell Tests Covering Key Forensic Workflows
473 " 🔵 Shell Script Error Handling: Only 3 of ~50 Scripts Use set -Eeuo pipefail
474 2:50p 🔵 Security and Quality Issues: SQL Injection Surface and Missing Error Propagation
475 " 🔵 Hardcoded Paths and Environment-Specific Assumptions Throughout Codebase
476 " 🔵 Missing Wallet Recovery Capabilities: No Hardware Wallets, VeraCrypt, or Content-Based Detection
477 " 🔵 TUI UX Gaps: No Prerequisite Visibility, No Multi-Disk Concurrency, PID Race
478 2:51p ⚖️ IMPROVEMENTS.md Plan Finalized: 23 Improvements Across 4 Priority Tiers
479 4:10p 🔵 HDD Recovery Repo Architecture and Tech Stack Mapped
480 " 🔵 Critical Security and Correctness Gaps Found in HDD Recovery Pipeline
481 " 🟣 IMPROVEMENTS.md Plan Created with 23 Prioritized Improvements
482 " ✅ IMPROVEMENTS.md Created with 23 Prioritized Improvements
S35 HDD Recovery repo analysis and improvement planning — read codebase, identify issues, create prioritized IMPROVEMENTS.md (May 17 at 4:11 PM)
**Investigated**: The entire hdd-recovery repository was read and analyzed: shell scripts in bin/, Python TUI (tui/stages.py, tui/screens/*), Go supervisor (docker/supervisor/main.go), web UI (bin/image-serve.py), SQL schema (sql/analysis-schema.sql), config files, YARA rules, and documentation (BITCOIN-WALLET-RECOVERY.md). The full 40+ stage pipeline was mapped from carving through wallet detection, photo recovery, OCR, tagging, and replication.

**Learned**: - Stack: Kali Docker container, Python Textual TUI, Go supervisor/reverse-proxy, Python HTTP web UI (2007-line monolith), SQLite state DB, 40+ shell-script pipeline stages
    - bin/image-ocr-seed-scan.py exists and is documented as Step 6 but is completely orphaned — not registered in tui/stages.py or bin/image-pipeline.py
    - lib/common.sh sql_escape() is unsafe for binary/multibyte data; shell scripts build SQL via string interpolation instead of parameterized queries
    - Most bin/image-*.sh scripts lack set -Eeuo pipefail, so tool failures are silently recorded as ok in scan_runs
    - bin/image-pipeline.py enforces stage order but does NOT verify prior stages completed with status=ok before launching dependents
    - docker/supervisor/main.go writes PID file non-atomically; TUI reads it without validating process liveness
    - wallet_candidates table has no UNIQUE constraint — same file discovered by TSK, carving, and ext recovery creates 3 duplicate rows
    - findings, wallet_keys, crack_tasks tables are created inline in individual scripts rather than in sql/analysis-schema.sql
    - tui/screens/wizard.py hardcodes _BLOCKED = {"sda","sdb","sdc"} — device-specific assumption that breaks on other hardware
    - bin/image-detect-wallets.sh scores on filename only — any file named wallet.dat scores 95 regardless of BerkeleyDB magic bytes

**Completed**: IMPROVEMENTS.md written to /Users/marc/Development/repos/hdd-recovery/IMPROVEMENTS.md with 23 improvements across 4 priority tiers: Critical (4 items: SQL injection, silent failures, stage validation, PID race), High/Effectiveness (6 items: content-based wallet detection, OCR seed scan wiring, encrypted container detection, wallet dedup, targeted wordlist generation, RAW photo formats), Medium/UX (7 items: hardcoded paths, TUI blocked-stage explanation, dynamic device blocking, Ollama pre-check, photo cluster UI, rsync reliability, schema consolidation), Low/Polish (6 items: image-serve.py refactor, GPS map view, test portability, configurable YARA scoring, multi-disk TUI panel, Volatility wallet profile). Task is fully complete — documentation-only deliverable.

**Next Steps**: Session appears complete. The natural next step in the project would be to begin implementing Priority 1 items, starting with adding set -Eeuo pipefail to all bin/image-*.sh scripts and migrating bash SQL writes to parameterized Python sqlite3 calls.


Access 993k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>