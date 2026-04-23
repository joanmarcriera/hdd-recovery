# TODO — hdd-recovery

Future improvements roughly ordered by impact.

---

## TUI

- **New-disk wizard** — guided flow for a brand-new disk: pick device, fill model/serial, generate job config, then drop straight into stage 1. Currently you have to hand-edit a `.conf` file before the disk appears in the dashboard.
- **In-TUI notes editor** — let the operator annotate a disk or a stage (e.g. "sector 0x1A bad — physical head click") and persist them to the `notes` DB table.
- **Stage dependency enforcement** — grey out / warn when a stage's `requires_prior` list isn't satisfied, so it's harder to accidentally skip structure-scan before wallet detection.
- **Cross-disk session log** — a scrollable panel showing all stage completions across all disks in chronological order, for a running audit trail of the session.
- **Disk health sidebar** — pull latest SMART attributes (temperature, reallocated sectors, pending sectors) and show them in the detail panel while imaging is underway.
- **Progress bar for ddrescue** — parse the live ddrescue output line (`rescued: X MB, errsize: Y, errors: Z`) and display a real progress bar instead of just coverage %.
- **Estimated time remaining** — for ddrescue and bulk_extractor, compute ETA from rate log or output.
- **Keyboard shortcut to open a new tmux window** with the log tail, for operators who prefer a terminal view.
- **Export browser** — a screen to browse `recovered_artifacts` and `wallet_candidates` rows directly from the TUI, with a key to copy a file to a staging directory.
- **Config editor** — view and edit `analysis-pipeline.env` flags (e.g. enable/disable Recoll, set scalpel config) from inside the TUI.

---

## Recovery toolchain

### Additional carving / recovery tools

| Tool | Status | Purpose |
|---|---|---|
| `testdisk` / `photorec` (extended profiles) | pending | testdisk can rebuild partition tables and recover whole filesystem structures — worth adding as a guided stage before carving |
| `binwalk` | pending | Extracts compound/firmware images; useful when a disk contains embedded archives or flash dumps |
| `recoverjpeg` | **done** | Fast JPEG-only carver, lighter than foremost/scalpel for picture-focused runs |
| `magicrescue` | **done** | Recipe-based carver with good coverage for office docs, audio, and compressed archives |
| `safecopy` | **done** | Alternative to ddrescue for disks that ddrescue handles poorly (different retry strategy) |
| `ddrescueview` | **done** | Map viewer via `image-mapview.sh`; TUI stage added |

### Enrichment and deduplication

| Tool | Purpose |
|---|---|
| `exiftool` (batch) | Extract GPS, timestamps, camera model from recovered images; feed into `picture_candidates` |
| `hashdeep` / `rdfind` | Cross-disk deduplication after human review — never before |
| `file` + `trid` | Better MIME typing on carved files with ambiguous extensions |
| `strings` + `grep` | Lightweight first pass for wallet seeds/keys in raw image regions not yet processed by bulk_extractor |

### Wallet / key recovery

| Tool | Purpose |
|---|---|
| `pywallet` | Bitcoin Core `wallet.dat` inspector and key extractor |
| `bitcoin-wallet` (Core CLI) | Dump keys from a salvaged wallet.dat |
| `electrum --offline` | Can open and inspect various wallet formats |
| `btcrecover` | Brute-force / mnemonic recovery for partially-known passphrases |
| `hashcat` | GPU-accelerated password recovery for encrypted wallet files |
| `keepassxc-cli` | If KeePass databases are found, inspect structure and attempt recovery |

### Filesystem-specific

| Tool | Status | Purpose |
|---|---|---|
| `ntfsundelete` (ntfs-3g) | **done** | Targeted NTFS deleted-file recovery via `image-ntfs-recover.sh` |
| `fatcat` | **done** | FAT/exFAT explorer and deleted-file recovery via `image-fat-recover.sh` |
| `ext4magic` | **done** | More thorough than `extundelete` for some ext3/ext4 edge cases |
| `xfs_undelete` | **done** | XFS deleted-file recovery via `image-xfs-recover.sh` |
| `btrfs restore` | **done** | Btrfs file recovery via `image-btrfs-recover.sh` |

---

## Analysis pipeline

- **Automated picture quality scoring** — run `imagemagick identify` or a lightweight BRISQUE/NIQE model on recovered images and rank by estimated quality (avoids reviewing blurry/corrupt files first).
- **Face detection pass** — use a local model (e.g. `dlib` or OpenCV Haar cascade) to flag images containing faces; useful for quickly finding personal photos.
- **Duplicate image detection** — perceptual hash (`pHash` via `imagehash`) across all recovered images to group near-duplicates before export.
- **Email / contact extraction** — bulk_extractor already pulls email addresses; add a stage that imports `email.txt` hits and cross-references with found `.pst` / `mbox` / `Thunderbird` profile paths.
- **Cryptocurrency address validation** — after bulk_extractor finds address-like strings, validate checksum and classify by coin type (BTC, ETH, LTC, etc.).
- **Timeline report** — **done** (`bin/image-timeline.sh`); outputs JSON/CSV/table from `scan_runs`, `files`, and `recovered_artifacts`.

---

## Infrastructure

- **Notification hooks** — send a desktop/phone notification (e.g. via `ntfy.sh` or `gotify`) when a long stage finishes or fails.
- **Schema migration support** — currently `analysis-schema.sql` uses `CREATE TABLE IF NOT EXISTS`; add a `schema_version` table and migration scripts for future column additions.
- **Automated backup** — **done** (`bin/recovery-backup.sh`); tiered rsync to `/mnt/CryptoBackup/recovery`.
- **Remote status page** — **done** (`bin/image-serve.sh` / `bin/image-serve.py`); read-only local web UI on port 7788 with dashboard, wallets, pictures, search, artifacts, timeline, and SQL query.
