# Bulk Discovery Runbook

This runbook is for repeated post-acquisition work on multiple disk images,
including disks that are larger, older, or in worse shape than the first
Hitachi run.

Use this after the raw image exists and you have decided to do broad discovery,
not just a quick metadata-first review.

## Execution environment

All stages in this runbook run inside the Docker container on TrueNAS SCALE
(12 CPU, 96 GB RAM, RTX 4060). The Optiplex handles only ddrescue imaging,
`image-analysis-init.sh`, `image-structure-scan.sh`, and the transfer via
`send-image-to-truenas.sh`.

Access the container:
- Browser terminal: `http://<truenas-ip>:7681` (username: admin, password: TTYD_PASSWORD)
  — the browser terminal launches the TUI by default; for a plain shell, use `docker exec`
- Direct shell: `docker exec -it hdd-forensics bash`

Data path inside container: `/mnt/recovery16tb/recovery/` (mapped from `/mnt/BigDisk/CryptoBackup` on TrueNAS host).

## Scope

This runbook covers:
- what to run
- in what order
- what can take hours
- what is safe to repeat
- what to inspect first afterward
- what is still manual

It assumes the image already exists under `/mnt/recovery16tb/recovery/images`.

## High-Level Sequence

For each image:

1. Fast core path
2. Ext-specific deleted-file recovery if applicable
3. Raw-image triage over free/deleted/unallocated space
4. Carving
5. Triage over the recovered corpus
6. Optional full-text indexing
7. Human review

Use:
- [image-analysis-workflow.md](image-analysis-workflow.md) for the core path
- this runbook for the heavy path; the older bulk-discovery workflow note has
  been archived as a superseded historical document

## Commands

### Fast core path

```bash
/root/hdd-recovery/bin/image-process.sh /mnt/recovery16tb/recovery/images/<basename>.img
```

### One-shot heavy path

```bash
/root/hdd-recovery/bin/image-bulk-discovery-run.sh /mnt/recovery16tb/recovery/images/<basename>.img
```

### Manual stage-by-stage heavy path

Run these inside the Docker container on TrueNAS (`docker exec -it hdd-forensics bash`):

```bash
DB=/mnt/recovery16tb/recovery/images/<basename>.img.analysis.sqlite

/root/hdd-recovery/bin/image-index-tsk.sh "$DB"
/root/hdd-recovery/bin/image-detect-wallets.sh "$DB"
/root/hdd-recovery/bin/image-detect-pictures.sh "$DB"
/root/hdd-recovery/bin/image-ext-recover.sh "$DB"
/root/hdd-recovery/bin/image-bulk-extractor.sh "$DB" --scope raw
/root/hdd-recovery/bin/image-carve.sh "$DB" --method foremost
/root/hdd-recovery/bin/image-carve.sh "$DB" --method scalpel
/root/hdd-recovery/bin/image-bulk-extractor.sh "$DB" --scope recovered
/root/hdd-recovery/bin/image-ocr-seed-scan.py "$DB"
/root/hdd-recovery/bin/image-index-recoll.sh "$DB" --path "$(sqlite3 -readonly "$DB" 'select export_root || "/recovered" from image_info where id=1;')"
/root/hdd-recovery/bin/image-ntfs-artifact-summary.sh "$DB"
/root/hdd-recovery/bin/image-photorec-run.sh "$DB" --profile broad
/root/hdd-recovery/bin/image-report.sh "$DB"
```

Note: `image-ocr-seed-scan.py` reads recovered images from `recovered_artifacts` in the DB.
Run it after `image-bulk-extractor.sh --scope recovered` so the corpus is fully registered.
The BIP-39 wordlist at `/usr/local/share/bip39-english.txt` is available inside the container.

## Re-Run Safety

| Stage | Re-run behavior | Operator rule |
| --- | --- | --- |
| `image-ext-recover.sh` | Adds/registers artifacts again by method and path. Current script separates `extundelete` and `ext4magic` provenance for future runs. | Avoid re-running until reviewed unless you intentionally want a new ext pass. |
| `image-bulk-extractor.sh --scope raw` | Preserves previous output/logs with `.prev-YYYYmmddTHHMMSSZ`, then creates a fresh output tree. | Safe to re-run, but expect duplicated DB hits unless the old run is handled later. |
| `image-bulk-extractor.sh --scope recovered` | Preserves previous output/logs with `.prev-YYYYmmddTHHMMSSZ`, then creates a fresh output tree. | Safe to re-run if it hangs, but wait for the current process to exit first. |
| `image-carve.sh --method foremost` | Preserves previous log with `.prev-YYYYmmddTHHMMSSZ`; output directory remains method-specific. | Do not re-run blindly on evidence you have not reviewed. |
| `image-carve.sh --method scalpel` | Preserves previous log with `.prev-YYYYmmddTHHMMSSZ`; output directory remains method-specific. | Do not re-run blindly on evidence you have not reviewed. |
| `image-ntfs-artifact-summary.sh` | Rebuilds a review bundle from raw `bulk_extractor` NTFS/Windows feature files. | Safe and additive enough for review; source feature files are not modified. |
| `image-photorec-run.sh --profile broad` | Creates a timestamped PhotoRec output directory under `recovered/photorec`. | Safe to run unattended on image copies; expect noisy output and duplicates. |
| `image-index-recoll.sh` | Rebuilds the Recoll index for the chosen recovered tree. | Safe after recovered outputs are stable. |
| `image-ocr-seed-scan.py` | Creates a new timestamped output dir under `hits/ocr-seeds/` each run; does not overwrite prior results. | Safe to re-run; each run is independent. |
| `image-report.sh` / `image-status.sh` | Regenerates reports/status from SQLite. | Safe anytime after DB writers finish. |

## What Changes For Bigger Or Older Disks

### Bigger disks

Expect:
- much longer `bulk_extractor` runtime
- much larger carve output
- much larger duplicate sets
- more SQLite growth
- much larger Recoll index

Practical consequence:
- run heavy stages one by one, not all at once
- keep enough free space for recovered outputs, not just the original image
- expect hours for 150 GB and potentially much longer for 500 GB, 1 TB, or more

### Older or more heavily reused disks

Expect:
- more deleted-file residue
- more stale metadata
- more false positives
- more unrelated historical content from previous uses

Practical consequence:
- `extundelete`/`ext4magic` and carving become more valuable
- triage and review become more important than any single heuristic

## Runtime Expectations

The first Hitachi bulk-discovery run showed:
- ext recovery: short enough to run early
- raw `bulk_extractor`: multi-hour on a 150 GB image
- carving: expected to be hours-scale on larger images or noisier media
- artifact registration after carving can continue after the carve tool exits
  because the workflow still hashes and catalogs the recovered files

Rule of thumb:
- fast path: minutes
- raw-image heavy path: hours

Do not start the heavy path unless you are willing to let it run.

## Monitoring

Preferred live monitor:

```bash
ANALYSIS_DB=/mnt/recovery16tb/recovery/images/<basename>.img.analysis.sqlite \
BACKUP_MOUNT=/mnt/CryptoBackup \
/root/start-recovery-monitor.sh
tmux attach -t recovery-monitor
```

Use the `analysis` tmux window during post-acquisition work. It tracks:
- scan-run status from the per-image SQLite database
- active recovery and indexing processes
- analysis log tails
- recovered/index/report/log output growth
- `/mnt/recovery16tb` and `/mnt/CryptoBackup` mount state

Use the log and output tree directly:

```bash
tail -f /mnt/recovery16tb/recovery/exports/<basename>/logs/ext-recover.log
tail -f /mnt/recovery16tb/recovery/exports/<basename>/logs/bulk-extractor-raw.log
tail -f /mnt/recovery16tb/recovery/exports/<basename>/logs/carve-foremost.log
tail -f /mnt/recovery16tb/recovery/exports/<basename>/logs/carve-scalpel.log
tail -f /mnt/recovery16tb/recovery/exports/<basename>/logs/post-bulk-queue.log
```

Watch growth:

```bash
du -sh /mnt/recovery16tb/recovery/exports/<basename>/indexes/bulk_extractor_raw
du -sh /mnt/recovery16tb/recovery/exports/<basename>/recovered/*
find /mnt/recovery16tb/recovery/exports/<basename>/recovered -type f | wc -l
```

## What Is Resumable

### Reasonably resumable

- the per-image SQLite catalog
- `bulk_extractor` output on disk
- ext recovery outputs
- carve outputs already written
- reports can always be regenerated

### Use caution when repeating

- `image-structure-scan.sh`
  Re-running after indexing requires `--force` because it rebuilds partition
  metadata.

- `image-bulk-extractor.sh`
  Preserves previous output for the selected scope by moving it to a
  timestamped `.prev-YYYYmmddTHHMMSSZ` path, then creates a fresh active output
  tree.

- carving
  Re-running preserves logs now, but outputs still go into the same
  method-specific output tree. Do not re-run carving until you have reviewed or
  intentionally snapshotted the previous method directory.

## Live Long-Run Warnings

`bulk_extractor` may sit at `100.00%` while it flushes and finalizes feature
files. During that phase:

```bash
du -sh /mnt/recovery16tb/recovery/exports/<basename>/indexes/bulk_extractor_recovered
pgrep -af 'bulk_extractor|image-bulk-extractor'
```

If output size and log mtime stop changing for a long period, treat it as a
possible hang. Do not start another DB-writing stage until the current writer
has exited or you have deliberately stopped it.

## Review Order After Heavy Discovery

Review in this order:

1. Per-image report
2. ext recovery outputs
3. raw `bulk_extractor` highlights
4. `foremost` carve results
5. `scalpel` carve results
6. recovered-corpus `bulk_extractor` highlights
7. OCR seed scan results (`hits/ocr-seeds/<timestamp>/summary.txt`)
8. Recoll searches
9. NTFS/Windows artifact summary
10. PhotoRec output

## Review Priorities

Look for these classes first:
- wallet files and wallet-like metadata
- photos and image folders
- archives
- office documents and PDFs
- SQLite/DB files
- JSON, text, CSV, backups
- application metadata sidecars that reveal original filenames

For the first Hitachi run specifically:
- `.part.met` files matter more than `.part.met.seeds`
- `.part.met` files likely tell you what the larger `.part` files were
- broad carving produced many generic cache/web-style assets, so carved output
  should be triaged by type instead of assumed useful by default

## Manual-Only Or Operator-Judgment Stages

### PhotoRec

Use it when:
- the earlier stages still look too sparse
- you suspect substantial deleted content in free space
- you accept much noisier output

Run:

```bash
/root/hdd-recovery/bin/image-photorec-run.sh "$DB" --profile broad
```

Output goes under:

```bash
/mnt/recovery16tb/recovery/exports/<basename>/recovered/photorec/
```

### NTFS / Windows artifact summary

Run this when raw `bulk_extractor` finds `ntfs*`, `winlnk`, `winprefetch`, or
`windirs` feature files. It creates a reviewable bundle under
`recovered/ntfs-artifacts`.

```bash
/root/hdd-recovery/bin/image-ntfs-artifact-summary.sh "$DB"
```

### TestDisk

Use only when you need another opinion on partition/table history or want a
manual recovery-oriented review of structure problems.

### Content-specific parsing

Examples:
- eMule/aMule `.part.met` parsing
- wallet-format-specific inspection
- archive opening
- image metadata review

These are case-specific follow-up steps, not generic first-pass automation.

## Lessons Learned From The First Hitachi Bulk Run

1. The current ext4 view was not the full story.
   Ext recovery immediately surfaced more historical files than the first
   metadata-only pass.

2. False positives happen early.
   `seeds` looked wallet-related to a naive heuristic but turned out to be
   eMule/aMule peer-source metadata.

3. Sidecar metadata files can be more valuable than payload files at first.
   On this image, `.part.met` files are likely the shortest path to
   understanding what the large partial files actually are.

4. Raw-image triage is expensive.
   `bulk_extractor` on a 150 GB image is already multi-hour, so larger disks
   should be planned as long-running jobs.

5. Keep stages separate.
   This makes it easier to compare what each recovery method added and to avoid
   losing track of provenance.

## Final Operator Advice

- Do the fast path on every image.
- Do the heavy path only when the image looks promising or uncertain enough to
  justify hours of work.
- Start with ext recovery and raw `bulk_extractor` before PhotoRec.
- Preserve outputs by method.
- Review metadata sidecars early when they exist.
