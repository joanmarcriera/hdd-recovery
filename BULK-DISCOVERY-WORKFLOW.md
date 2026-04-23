# Bulk Discovery Workflow

Use this workflow when the goal is broad recovery from an image and you do not
yet know what may be on the disk.

This is the "gather everything practical first, review later" path.

## Principles

- work only from image files, not original source disks
- prefer non-destructive, read-only analysis first
- preserve provenance by recovery method
- run filesystem-aware recovery before raw carving
- keep one SQLite catalog per image and add later-stage findings to it
- expect false positives, duplicates, and noisy results in the heavy stages

## Stage Order

Use the stages in this order.

1. Initialize the per-image catalog.
   - `image-analysis-init.sh <image> [--map <mapfile>]`

2. Record structure and baseline filesystem context.
   - `image-structure-scan.sh <db>`
   - `image-index-tsk.sh <db>`

3. Run lightweight candidate detection on the filesystem-aware inventory.
   - `image-detect-wallets.sh <db>`
   - `image-detect-pictures.sh <db>`

4. Run ext-specific deleted-file recovery if the image contains ext3/ext4.
   - `image-ext-recover.sh <db>`

5. Run raw-image triage that does not depend on the current directory tree.
   - `image-bulk-extractor.sh <db> --scope raw`

6. Run carving passes for deleted/free-space content.
   - `image-carve.sh <db> --method foremost`
   - `image-carve.sh <db> --method scalpel`
   - `photorec` manually if still needed
   - carving also includes a later artifact-registration phase, so the stage may
     continue after the carving tool itself exits

7. Run second-pass triage on the recovered corpus.
   - `image-bulk-extractor.sh <db> --scope recovered`
   - `image-index-recoll.sh <db> --path <recovered-dir>`

8. Query, export, and review.
   - `image-query.sh <db> summary`
   - `image-query.sh <db> wallets`
   - `image-query.sh <db> pictures`
   - `image-report.sh <db>`

## Why This Order

Filesystem-aware tools preserve:
- paths
- timestamps
- partition context
- inode references

Deleted/free-space tools can find more, but with much more noise:
- lost original filenames
- duplicate hits across methods
- fragmentary content
- many false positives

That is why broad discovery should still start with the current filesystem view.

## What Each Heavy Stage Is Best At

- `image-ext-recover.sh`
  Best for ext3/ext4 deleted files and metadata still reachable through journal
  or filesystem history.

- `image-bulk-extractor.sh --scope raw`
  Best for text-like artifacts, strings, addresses, emails, URLs, JSON, SQLite
  fragments, and other clues from raw image space.

- `image-carve.sh --method foremost`
  Good broad signature carve.

- `image-carve.sh --method scalpel`
  Good controlled carve using a tuned config.

- `photorec`
  Broadest and often most productive on damaged media, but also the noisiest.
  Use manually.

- `image-bulk-extractor.sh --scope recovered`
  Best when you want to triage the carved and recovered corpus itself after the
  raw-image passes.

- `image-index-recoll.sh`
  Best for full-text style searching over recovered outputs once there is a real
  recovered corpus to index.

## Lessons From The First Hitachi Run

Current observations from the Hitachi image:
- the visible ext4 filesystem inventory is small and mostly consists of
  `Temp/*.part`, `*.part.met`, `*.part.met.bak`, and `*.part.met.seeds`
- this is consistent with an eMule/aMule temporary download directory
- terms like `seeds` can be false positives for wallet discovery because they
  may refer to download sources rather than crypto seed phrases
- ext-specific recovery is already finding additional `*.part.met` and related
  files beyond the first metadata-only pass

What this means operationally:
- do not trust wallet keyword hits without path and content context
- inspect metadata sidecar files like `.part.met` early because they may reveal
  the original names of larger payload files
- keep heavy discovery stages separate so you can compare what each method adds
- expect broad carving output to contain many generic web/cache assets and false
  positives alongside useful files

## Recommended Human Review Order After Bulk Discovery

1. Per-image report
2. extundelete/ext4magic outputs
3. raw bulk_extractor highlights
4. foremost output
5. scalpel output
6. recovered-corpus bulk_extractor highlights
7. Recoll searches
8. PhotoRec output only if you decide the earlier stages still missed too much

## Commands

Fast core path:

```bash
/root/hdd-recovery/bin/image-process.sh /mnt/recovery16tb/recovery/images/<basename>.img
```

Full bulk discovery, stage by stage:

```bash
/root/hdd-recovery/bin/image-ext-recover.sh <db>
/root/hdd-recovery/bin/image-bulk-extractor.sh <db> --scope raw
/root/hdd-recovery/bin/image-carve.sh <db> --method foremost
/root/hdd-recovery/bin/image-carve.sh <db> --method scalpel
/root/hdd-recovery/bin/image-bulk-extractor.sh <db> --scope recovered
/root/hdd-recovery/bin/image-index-recoll.sh <db> --path <recovered-dir>
/root/hdd-recovery/bin/image-report.sh <db>
```

One-shot bulk discovery runner:

```bash
/root/hdd-recovery/bin/image-bulk-discovery-run.sh /mnt/recovery16tb/recovery/images/<basename>.img
```

Use this only when you intentionally want the heavy deleted/free-space pipeline.
