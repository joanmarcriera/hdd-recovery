# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

### Analysis — Heavy Stages

| Script | Purpose |
|--------|---------|
| `bin/image-ext-recover.sh <db>` | ext3/ext4 journal-aware deleted file recovery (extundelete/ext4magic) |
| `bin/image-bulk-extractor.sh <db> --scope raw\|recovered` | Run bulk_extractor on raw image or recovered corpus; backs up prior output with timestamp; imports hits into DB up to `BULK_HIT_LIMIT` |
| `bin/image-carve.sh <db> --method foremost\|scalpel` | Carving pass; registers artifacts in DB afterward |
| `bin/image-photorec-run.sh <db> --profile broad\|photos` | Unattended PhotoRec via `/cmd`; output under `recovered/photorec/<profile>-<timestamp>` |
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

### Web UI (`bin/image-serve.py`)

Started via the TUI or `python3 bin/image-serve.py --port 7788`. Routes:

| Route | Purpose |
|-------|---------|
| `/` | Home: all databases with file/artifact/wallet counts and a map icon link |
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
