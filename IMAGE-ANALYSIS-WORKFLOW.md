# Image Analysis Workflow

This workflow starts only after ddrescue imaging is complete.

Core rules:
- never write to the original source disk
- prefer to work from a copied image if the image itself is now high-value evidence
- keep all image exposure read-only
- do filesystem-aware recovery first
- do carving second
- query the per-image SQLite catalog before rescanning

## Stage Order

Use the stages in this order. Each later step assumes the earlier step already ran.

1. Initialize the per-image catalog.
   - `image-analysis-init.sh <image> [--map <mapfile>]`
   - this creates or refreshes the per-image SQLite DB and export tree
   - do this before every other analysis step

2. Scan image structure.
   - `image-structure-scan.sh <db>`
   - collects `fdisk`, `parted`, `mmls`, `img_stat`, and `blkid` outputs
   - do this before filesystem-aware indexing
   - rerunning this after indexing requires `--force` because it rebuilds partition metadata

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
   - `image-carve.sh <db> --method foremost|scalpel|photorec`
   - `image-bulk-extractor.sh <db> --scope raw`
   - these are slower and noisier than the earlier steps
   - do them after the metadata-first path, not before
   - `photorec` is currently manual/interactive only

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

For most images:

1. `image-process.sh <image>`
2. `image-query.sh <db> summary`
3. `image-query.sh <db> wallets`
4. `image-query.sh <db> pictures`
5. If metadata-first results are weak:
   `image-bulk-extractor.sh <db> --scope raw`
6. If ext3/ext4 and deleted-file recovery matters:
   `image-ext-recover.sh <db>`
7. If you still need more deleted-content recovery:
   `image-carve.sh <db> --method foremost`
   `image-carve.sh <db> --method scalpel`
   `photorec` manually if needed
8. `image-report.sh <db>`

## Heavy stages

Use these when needed:
- `image-ext-recover.sh` for ext3/ext4 journal-aware recovery attempts
- `image-carve.sh` when deleted content matters more than intact metadata
- `image-bulk-extractor.sh` when you want raw-image triage for emails, URLs, address-like strings, and other text artifacts
- `image-index-recoll.sh` when you want full-text indexing over recovered outputs
