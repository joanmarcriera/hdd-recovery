# Next Run TODO - 2026-05-04

This is the handoff document for the next operator/agent run. It is based on a
repo-wide Markdown review and live evidence from the running `hdd-forensics`
container on 2026-05-04.

## Current State

- Container: `hdd-forensics`, image `joanmarcriera/hdd-forensics:latest`, healthy and up for about 31 hours when checked.
- Exposed ports: `7681` browser terminal, `7788` web UI, `8080` health/status API.
- Host memory at review time: 31 GiB RAM, 100 GiB swap. `/home/swapfile` is present and active, but currently unused; `/dev/sda4` swap had about 470 MiB used.
- No NVIDIA GPU runtime is visible inside the current container: `nvidia-smi` is missing and `hashcat -I` reports no OpenCL/HIP/CUDA platform.
- The running container does not include `tests/smoke/`; run smoke tests from the repo checkout, not from inside the container, unless the Dockerfile is changed to copy them.

## Second Disk Pipeline

The "second disk" currently under full pipeline analysis appears to be:

```text
Image:
/mnt/recovery16tb/recovery/images/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd.img

DB:
/mnt/recovery16tb/recovery/images/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd.img.analysis.sqlite

Export root:
/mnt/recovery16tb/recovery/exports/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd

Pipeline log:
/mnt/recovery16tb/recovery/exports/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd/logs/pipeline-20260503T085003Z.log
```

Pipeline command in the running container:

```bash
/root/hdd-recovery/bin/image-pipeline.py \
  /mnt/recovery16tb/recovery/images/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd.img.analysis.sqlite \
  --run \
  --log /mnt/recovery16tb/recovery/exports/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd/logs/pipeline-20260503T085003Z.log \
  --keep-going \
  init-db structure-scan index-tsk detect-wallets detect-pictures enrich-photos \
  ext-recover ntfs-recover fat-recover xfs-recover btrfs-recover extract-winmem \
  bulk-extractor-raw yara-scan pdf-extract carve-recoverjpeg carve-foremost \
  carve-scalpel carve-magicrescue bulk-extractor-recovered text-seed-scan \
  enrich-trid recoll-index ntfs-artifact-summary regripper rifiuti2 \
  plaso-timeline photorec-broad dedup-photos wallet-inspect tag-photos \
  generate-report
```

Scan state when reviewed:

```text
ok: 17
partial: 1
running: 1
skipped: 1
```

Important details:

- `structure-scan` from the pipeline was `skipped`, not a real failure. It refused to rebuild partition provenance because indexed files already existed. That is expected.
- `bulk-extractor-raw` completed successfully from `2026-05-03T08:50:12Z` to `2026-05-03T17:05:28Z`.
- `carve-recoverjpeg` completed successfully and registered 408 artifacts, about 36 MiB.
- `carve-foremost` completed successfully and registered 41,705 artifacts, about 1.45 GiB.
- `carve-scalpel` is marked `partial`. Its log shows `Killed` at the scalpel process after it reached 100% of the 111.8 GB scan. The wrapper still registered artifacts afterward, but only one zero-byte `scalpel` artifact was in SQLite. The scalpel output directory contains about 79,684 top-level directories and only one file (`audit.txt`) when counted with `find -type f`.
- `carve-magicrescue` started at `2026-05-04T01:20:50Z` and was still running at the final live check after about 14 h 20 min. It had produced about 701 MiB and 13,694 files. The log is noisy with repeated `Found mp3-id3v1` / `No output file` messages.
- Current SQLite counts for this DB at review time: `files=326`, `recovered_artifacts=42114`, `findings=0`, `wallet_keys=0`, `crack_tasks=0`.

Do not start another DB-writing stage for this DB while the current pipeline
process is alive.

## Monitor Commands

Use these from the host:

```bash
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"

docker exec hdd-forensics ps -eo pid,ppid,stat,etime,cmd

tail -n 80 /mnt/recovery16tb/recovery/exports/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd/logs/pipeline-20260503T085003Z.log

tail -n 120 /mnt/recovery16tb/recovery/exports/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd/logs/carve-magicrescue.log

sqlite3 -readonly /mnt/recovery16tb/recovery/images/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd.img.analysis.sqlite \
  "SELECT id,stage,status,started_at,ended_at,notes FROM scan_runs ORDER BY id;"

du -sh /mnt/recovery16tb/recovery/exports/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd/recovered/*
```

## Immediate TODO

1. Let the current `carve-magicrescue` stage either finish or clearly stall before doing anything else to this DB.
2. After it finishes, inspect `scan_runs` and the pipeline log. The runner should continue into:
   `bulk-extractor-recovered`, `text-seed-scan`, `enrich-trid`, `recoll-index`,
   `ntfs-artifact-summary`, `regripper`, `rifiuti2`, `plaso-timeline`,
   `photorec-broad`, `dedup-photos`, `wallet-inspect`, `tag-photos`,
   `generate-report`.
3. If the pipeline stops before those stages, resume with only the remaining stages. Use `--skip-done` when appropriate.
4. Rerun `carve-scalpel` only after the current pipeline is idle. The previous scalpel run was OOM-killed; the new 100 GiB swap should help, but protect the existing output first because `image-carve.sh` does not yet backup an existing tool output directory.
5. Rebuild and redeploy the Docker image only after the current long pipeline is safe to stop or has finished. The Dockerfile has a `btcrecover.py` wrapper fix that the running container does not yet have.

## Scalpel Rerun Plan

Before rerunning scalpel, either improve `image-carve.sh` to backup an existing
`recovered/scalpel` directory or manually move the partial directory aside.
Do not delete the partial output.

Example manual-safe sequence after the active pipeline has stopped:

```bash
EXP=/mnt/recovery16tb/recovery/exports/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd
DB=/mnt/recovery16tb/recovery/images/20260430_KINGSTON_SV300S37A120G_KINGSTON_SV300S37A120G_50026B733500B53B_sdd.img.analysis.sqlite
TS=$(date -u +%Y%m%dT%H%M%SZ)
mv "$EXP/recovered/scalpel" "$EXP/recovered/scalpel.prev-$TS"
docker exec hdd-forensics /root/hdd-recovery/bin/image-carve.sh "$DB" --method scalpel
```

After rerun:

```bash
sqlite3 -readonly "$DB" \
  "SELECT method,COUNT(*),COALESCE(SUM(size_bytes),0) FROM recovered_artifacts GROUP BY method ORDER BY COUNT(*) DESC;"
```

## Docker Rebuild TODO

The current running container is old relative to the working tree. After the
pipeline is done or safely paused, rebuild and verify:

```bash
docker build -f docker/Dockerfile -t joanmarcriera/hdd-forensics:latest .
docker rm -f hdd-forensics
docker compose up -d

docker exec hdd-forensics btcrecover.py --help >/dev/null
docker exec hdd-forensics seedrecover.py --help >/dev/null
docker exec hdd-forensics trid --version
docker exec hdd-forensics python3 -c "import volatility3, imagehash; print('imports ok')"
docker exec hdd-forensics hashcat --version
docker exec hdd-forensics john --list=formats
```

GPU-dependent verification remains blocked until the container is started with
working NVIDIA runtime access, for example `--gpus all` or the equivalent
TrueNAS setting. The current container reports no GPU runtime to hashcat.

## Owner Verification Still Needed

Run these from the repo checkout when the needed fixture/hardware exists:

```bash
bash tests/smoke/T1-pywallet.sh
bash tests/smoke/T2-gpu-check.sh
bash tests/smoke/T3-crack-wallet.sh
bash tests/smoke/T4-btcrecover.sh
bash tests/smoke/T5-refactor-regression.sh
bash tests/smoke/T6-trid.sh
bash tests/smoke/T7a-extract-winmem.sh
bash tests/smoke/T7b-volatility.sh
bash tests/smoke/T8-dedup-photos.sh
bash tests/smoke/T9-enrich-photos-quality.sh
bash tests/smoke/T10-crack-keepass.sh
bash tests/smoke/T11-plaso-crypto.sh
bash tests/smoke/T12-tui-stages.sh
```

Known verification outcomes from this review:

- Container package checks passed for `hashcat`, `john`, `bitcoin2john.py`, `TrID`, `Volatility3`, `imagehash`, `pywallet`, `seedrecover.py`, `keepass4brute.sh`, and `keepassxc-cli`.
- `btcrecover.py` failed in the running container because its shebang uses `python`; the Dockerfile now wraps it with `python3`.
- `tests/smoke/T4-btcrecover.sh` now preflights both `seedrecover.py` and `btcrecover.py` so this specific packaging issue is caught next time.

## Docs Review

All Markdown files under `/root/hdd-recovery` and `docker/` were reviewed.

Retained as live docs:

- `README.md`
- `CLAUDE.md`
- `TODO.md`
- `NEXT-RUN-TODO.md`
- `ACQUISITION-CHECKLIST.md`
- `FUTURE-DISK-CHECKLIST.md`
- `DDRESCUE-WORKFLOW.md`
- `IMAGE-ANALYSIS-WORKFLOW.md`
- `BULK-DISCOVERY-RUNBOOK.md`
- `BITCOIN-WALLET-RECOVERY.md`
- `PICTURE-RECOVERY.md`
- `TOOL-SELECTION-IMAGE-ANALYSIS.md`
- `STAGE1-PROGRESS.md`
- `docker/README.md`
- `docker/DOCKERHUB.md`

Moved to ignored archive because they are superseded or one-off historical docs:

- `BULK-DISCOVERY-WORKFLOW.md` - superseded by `BULK-DISCOVERY-RUNBOOK.md`
- `FIRST-IMAGING-HITACHI-160GB.md` - one-off first-disk plan, replaced by generic acquisition docs
- `STAGE1-WORK.md` - completed implementation ticket spec; progress retained in `STAGE1-PROGRESS.md`
- `TOOL-EVALUATION-2026-04-21.md` - historical decision note
- `VERIFIED-STATE-2026-04-21.md` - historical machine state snapshot
