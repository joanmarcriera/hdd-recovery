# Image Analysis Workflow

This workflow starts only after ddrescue imaging is complete.

Core rules:
- never write to the original source disk
- prefer to work from a copied image if the image itself is now high-value evidence
- keep all image exposure read-only
- do filesystem-aware recovery first
- do carving second
- query the per-image SQLite catalog before rescanning

## Two-machine architecture

Analysis is split across two machines:

**Phase 1 — Optiplex (Kali Linux, imaging machine)**
- Run ddrescue to produce the raw image.
- Run `image-analysis-init.sh` and `image-structure-scan.sh` (lightweight, no heavy I/O).
- Transfer to TrueNAS with `bin/send-image-to-truenas.sh <image> <truenas-host>`.

**Phase 2 — TrueNAS SCALE (Docker container, 12 CPU / 96 GB RAM / RTX 4060)**
- Container image: `joanmarcriera/hdd-forensics:latest`
- Browser terminal: `http://<truenas-ip>:7681` (username: admin, password: TTYD_PASSWORD)
  — the terminal launches the TUI by default; for a plain shell, use `docker exec`
- Direct shell: `docker exec -it hdd-forensics bash`
- Data path inside container: `/mnt/recovery16tb/` (host path: `/mnt/BigDisk/CryptoBackup`)
- All heavy stages (TSK index, bulk_extractor, carving, ext recovery, OCR) run here.
- Ollama runs on the TrueNAS host and is reachable from the container via `OLLAMA_HOST`.

## Stage Order

Use the stages in this order. Each later step assumes the earlier step already ran.

### Optiplex stages (before transfer)

1. Initialize the per-image catalog.
   - `image-analysis-init.sh <image> [--map <mapfile>]`
   - this creates or refreshes the per-image SQLite DB and export tree
   - do this before every other analysis step

2. Scan image structure.
   - `image-structure-scan.sh <db>`
   - collects `fdisk`, `parted`, `mmls`, `img_stat`, and `blkid` outputs
   - do this before filesystem-aware indexing
   - rerunning this after indexing requires `--force` because it rebuilds partition metadata

2a. Transfer to TrueNAS.
   - `bin/send-image-to-truenas.sh <image> <truenas-host>`
   - rsyncs image, SQLite DB, ddrescue logs, and any existing exports to TrueNAS
   - prints the `docker exec` command to continue analysis in the container

### TrueNAS container stages (after transfer)

3. Build a filesystem-aware inventory.
   - `image-index-tsk.sh <db>`
   - uses `fiwalk` in metadata-first mode
   - preserves paths, inodes, timestamps, and partition context better than carving
   - this is the first real file inventory pass
   - wallet/picture detection depends on this step

4. Detect wallet and picture candidates from that inventory.
   - `image-detect-wallets.sh <db>`
   - `image-detect-pictures.sh <db>`
   - use this before any heavy carving so you can review the low-cost metadata-first results first

5. Run targeted recovery passes only when useful.
   - `image-ext-recover.sh <db>` for ext3/ext4 partitions
   - `image-bulk-extractor.sh <db> --scope raw`
   - `image-carve.sh <db> --method foremost|scalpel`
   - `image-bulk-extractor.sh <db> --scope recovered` after carving
   - `image-ocr-seed-scan.py <db>` after recovered corpus is registered (scans recovered images for BIP-39 seed phrases)
   - these are slower and noisier than the earlier steps
   - do them after the metadata-first path, not before

6. Query and export without rescanning.
   - `image-query.sh <db> wallets`
   - `image-query.sh <db> pictures`
   - `image-export.sh <db> --file-id <id>`
   - use this after indexing or after any recovery stage that writes new artifacts

7. Generate a report.
   - `image-report.sh <db>`
   - use this at the end of a pass, then rerun it after any heavy stage if you want a fresh summary

## Top-level command

For one image:

```bash
/root/hdd-recovery/bin/image-process.sh /mnt/recovery16tb/recovery/images/<basename>.img
```

This now runs only the fast core metadata-first path:
- init
- structure scan
- filesystem-aware index
- wallet detection
- picture detection
- report

Explicitly opt in to heavy stages:

```bash
/root/hdd-recovery/bin/image-process.sh /mnt/recovery16tb/recovery/images/<basename>.img --with-ext --with-bulk
/root/hdd-recovery/bin/image-process.sh /mnt/recovery16tb/recovery/images/<basename>.img --with-carve
```

## Per-image artifacts

For image `foo.img`:
- database: `foo.img.analysis.sqlite`
- export root: `/mnt/recovery16tb/recovery/exports/foo/`
- structure outputs: `/mnt/recovery16tb/recovery/exports/foo/structure/`
- recovered outputs by method: `/mnt/recovery16tb/recovery/exports/foo/recovered/`
- reports: `/mnt/recovery16tb/recovery/exports/foo/reports/`

## Why this order

Filesystem-aware recovery comes first because it preserves more context:
- original path names
- timestamps
- inode or metadata addresses
- partition provenance

Carving remains valuable, but it usually loses filenames and directory structure.

## Practical Sequence

On Optiplex (before transfer):

```bash
IMAGE=/mnt/recovery16tb/recovery/images/<basename>.img
bin/image-analysis-init.sh "$IMAGE" --map /mnt/recovery16tb/recovery/logs/<basename>.map
DB="${IMAGE}.analysis.sqlite"
bin/image-structure-scan.sh "$DB"
bin/send-image-to-truenas.sh "$IMAGE" <truenas-host>
```

In TrueNAS container (`docker exec -it hdd-forensics bash`):

1. `image-index-tsk.sh <db>`
2. `image-detect-wallets.sh <db>`
3. `image-detect-pictures.sh <db>`
4. `image-query.sh <db> summary`
5. `image-query.sh <db> wallets`
6. `image-query.sh <db> pictures`
7. If metadata-first results are weak:
   `image-bulk-extractor.sh <db> --scope raw`
8. If ext3/ext4 and deleted-file recovery matters:
   `image-ext-recover.sh <db>`
9. If you still need more deleted-content recovery:
   `image-carve.sh <db> --method foremost`
   `image-carve.sh <db> --method scalpel`
   `image-photorec-run.sh <db> --profile broad` (or photorec manually)
10. `image-bulk-extractor.sh <db> --scope recovered`
11. `image-ocr-seed-scan.py <db>`
12. `image-report.sh <db>`

## Heavy stages

Use these when needed (all run in the TrueNAS container):
- `image-ext-recover.sh` for ext3/ext4 journal-aware recovery attempts
- `image-carve.sh` when deleted content matters more than intact metadata
- `image-bulk-extractor.sh` when you want raw-image triage for emails, URLs, address-like strings, and other text artifacts
- `image-ocr-seed-scan.py` when you want to scan recovered images for BIP-39 seed phrases
- `image-index-recoll.sh` when you want full-text indexing over recovered outputs
