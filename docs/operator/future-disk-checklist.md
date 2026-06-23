# Future Disk Checklist

## 1. Before Imaging

1. Identify the source disk by model, serial, size, and `/dev/disk/by-path`.
2. Confirm the destination mount is correct and has enough free space.
3. Confirm no source partition is mounted.
4. Start the monitor if desired:

```bash
/root/start-recovery-monitor.sh
```

If analysing a specific image, pin the monitor to that image database:

```bash
ANALYSIS_DB=/mnt/recovery16tb/recovery/images/<basename>.img.analysis.sqlite \
BACKUP_MOUNT=/mnt/CryptoBackup \
/root/start-recovery-monitor.sh
```

## 2. Image The Disk

1. Preview:

```bash
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/<job>.conf plan
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/<job>.conf first
```

2. Run first pass:

```bash
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/<job>.conf first --run
```

3. Check map status:

```bash
/root/hdd-recovery/bin/ddrescue-status.sh /mnt/recovery16tb/recovery/logs/<basename>.map
```

4. If needed, later passes:

```bash
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/<job>.conf retry --run
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/<job>.conf reverse --run
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/<job>.conf retrim --run
```

## 3. Initialize DB and Transfer to TrueNAS (on Optiplex)

1. Initialize the analysis database and scan structure:

```bash
IMAGE=/mnt/recovery16tb/recovery/images/<basename>.img
/root/hdd-recovery/bin/image-analysis-init.sh "$IMAGE" --map /mnt/recovery16tb/recovery/logs/<basename>.map
/root/hdd-recovery/bin/image-structure-scan.sh "${IMAGE}.analysis.sqlite"
```

2. Transfer to TrueNAS for all heavy analysis:

```bash
/root/hdd-recovery/bin/send-image-to-truenas.sh "$IMAGE" <truenas-host>
```

The script transfers the image, SQLite DB, ddrescue logs, and any existing exports.
It prints the exact `docker exec` command to continue in the container after transfer.

## 4. Fast Post-Imaging Path (in TrueNAS container)

Access the container: `docker exec -it hdd-forensics bash`
Or via browser terminal: `http://<truenas-ip>:7681` (username: admin, password: TTYD_PASSWORD)
— the browser terminal launches the TUI by default; use `docker exec` for a plain shell.

1. Run the core path:

```bash
/root/hdd-recovery/bin/image-process.sh /mnt/recovery16tb/recovery/images/<basename>.img
```

2. Review:

```bash
/root/hdd-recovery/bin/image-query.sh /mnt/recovery16tb/recovery/images/<basename>.img.analysis.sqlite summary
/root/hdd-recovery/bin/image-query.sh /mnt/recovery16tb/recovery/images/<basename>.img.analysis.sqlite wallets
/root/hdd-recovery/bin/image-query.sh /mnt/recovery16tb/recovery/images/<basename>.img.analysis.sqlite pictures
```

## 5. Decide Whether To Go Heavy

Go heavy if:
- the fast path looks too sparse
- deleted files matter
- the disk is old or heavily reused
- you want free-space / historical artifact recovery

If not, stop at the fast path and review/export manually.

## 6. Heavy Bulk Discovery (in TrueNAS container)

One-shot (does not include OCR seed scan — run that separately after):

```bash
/root/hdd-recovery/bin/image-bulk-discovery-run.sh /mnt/recovery16tb/recovery/images/<basename>.img
```

Or stage by stage:

```bash
DB=/mnt/recovery16tb/recovery/images/<basename>.img.analysis.sqlite

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

## 7. Manual-Only Steps

Run manually only if needed:
- `testdisk`
- content-specific parsing such as `.part.met`, wallet formats, archive review

## 8. Review Order

1. per-image report
2. ext recovery outputs
3. raw `bulk_extractor` outputs
4. `foremost` output
5. `scalpel` output
6. recovered-corpus `bulk_extractor`
7. OCR seed scan results (`hits/ocr-seeds/<timestamp>/summary.txt`)
8. Recoll results
9. NTFS/Windows artifact summary
10. PhotoRec results

## 9. Good First Things To Inspect

- sidecar metadata files
- wallet-like filenames and paths
- picture directories and image files
- archives
- PDFs and office documents
- SQLite/DB files
- JSON, text, CSV, backups

## 10. Monitoring

Prefer the tmux monitor for long runs:

```bash
ANALYSIS_DB=/mnt/recovery16tb/recovery/images/<basename>.img.analysis.sqlite \
BACKUP_MOUNT=/mnt/CryptoBackup \
/root/start-recovery-monitor.sh
tmux attach -t recovery-monitor
```

Use the `analysis` window for:
- scan-run status
- active recovery processes
- analysis log tails
- recovered/index output growth
- `/mnt/recovery16tb` and `/mnt/CryptoBackup` mount visibility

Direct log checks:

```bash
tail -f /mnt/recovery16tb/recovery/exports/<basename>/logs/ext-recover.log
tail -f /mnt/recovery16tb/recovery/exports/<basename>/logs/bulk-extractor-raw.log
tail -f /mnt/recovery16tb/recovery/exports/<basename>/logs/carve-foremost.log
tail -f /mnt/recovery16tb/recovery/exports/<basename>/logs/carve-scalpel.log
du -sh /mnt/recovery16tb/recovery/exports/<basename>/indexes/bulk_extractor_raw
find /mnt/recovery16tb/recovery/exports/<basename>/recovered -type f | wc -l
```

## 11. Key Decision Rules

- Always do the fast path first.
- Only do the heavy path intentionally.
- Keep outputs by method.
- Review metadata sidecars early.
- Treat wallet hits as candidates, not proof.
- Expect false positives and duplicates.
- Do not clean, compact, or deduplicate evidence outputs before human review.
- Do not start the next DB-writing stage while a long-running writer is still active.
