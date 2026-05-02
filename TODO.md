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

## Toolchain — Implemented in this session (2026-04-30)

The following were added as part of the deeper analysis toolchain expansion:

| Tool | Script | Status |
|------|--------|--------|
| bulk_extractor extra scanners (wordlist, base16, outlook) | `image-bulk-extractor.sh` | **done** |
| exiftool EXIF enrichment + GPS | `image-enrich-photos.sh` | **done** |
| YARA wallet/key pattern matching | `image-yara-scan.sh` + `config/yara/*.yar` | **done** |
| pdftotext BIP39 seed extraction | `image-pdf-extract.sh` | **done** |
| RegRipper Windows registry analysis | `image-regripper.sh` | **done** |
| rifiuti2 Windows Recycle Bin | `image-rifiuti.sh` | **done** |
| plaso super-timeline | `image-plaso.sh` | **done** |
| `findings` table in SQLite schema | `sql/analysis-schema.sql` | **done** |
| TUI stages for all above | `tui/stages.py` (38 stages total) | **done** |

All above are in the TUI under the appropriate stage numbers. All require `docker compose up -d --build` to take effect in the container.

---

## Toolchain — Implemented (Stage 1, 2026-05-02)

| Ticket | Tool / capability | Script / file | Status |
|--------|-------------------|---------------|--------|
| T0 | Schema migrations, `crack_tasks`, `wallet_keys`, artifact enrichment columns | `lib/common.sh`, `sql/analysis-schema.sql` | done |
| T1 | pywallet wallet.dat inspection | `bin/image-wallet-inspect.sh` | done |
| T2 | john + hashcat + GPU preflight | `lib/gpu_check.sh` | done |
| T3 | Wallet.dat cracking with john/hashcat tooling | `bin/image-crack-wallet.sh` | done |
| T4 | BTCRecover partial seed/password wrapper | `bin/image-btcrecover.sh` | done |
| T5 | Text BIP39 scanner and shared seed logic | `lib/seed_scan.py`, `bin/image-text-seed-scan.sh` | done |
| T6 | TrID enrichment without file renames | `bin/image-enrich-trid.sh` | done |
| T7a | Windows memory artifact extraction | `bin/image-extract-winmem.sh` | done |
| T7b | Volatility3 focused scan | `bin/image-volatility-scan.sh` | done |
| T8 | Perceptual image dedup via pHash | `bin/image-dedup-photos.sh` | done |
| T9 | Automated picture quality scoring | `bin/image-enrich-photos.sh` | done |
| T10 | KeePass KDBX cracking | `bin/image-crack-keepass.sh` | done |
| T11 | Plaso crypto timeline import/filter | `bin/image-plaso.sh` | done |
| T12 | TUI entries for Stage 1 tools | `tui/stages.py` | done |

### Stage 1 status

- T0 — complete; local schema checks passed.
- T1 — complete; wallet fixture, Docker, and loop-device smoke checks pending owner verification.
- T2 — complete; GPU-positive and container package checks pending owner verification.
- T3 — complete; encrypted wallet cracking/pause/resume smoke checks pending owner verification.
- T4 — complete; real BTCRecover seed recovery smoke check pending owner verification.
- T5 — complete; text scanner smoke passed, OCR/PDF fixture regression pending owner verification.
- T6 — complete; TrID container execution pending owner verification.
- T7a — complete; Windows image extraction fixture pending owner verification.
- T7b — complete; Volatility3 fixture execution pending owner verification.
- T8 — complete; imagehash container smoke pending owner verification.
- T9 — complete; quality scoring smoke passed with synthetic images, real photo check pending owner verification.
- T10 — complete; KeePass fixture/GPU smoke pending owner verification.
- T11 — complete; plaso fixture execution pending owner verification.
- T12 — complete; import checks passed, interactive TUI launch pending owner verification.

## Toolchain — Remaining (not yet implemented)

No Stage 1 wallet, seed, deduplication, or plaso keyword-filter items remain in this section.

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
| `BTCRecover` | Brute-force / mnemonic recovery for partially-known passphrases |
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

- **Face detection pass** — use a local model (e.g. `dlib` or OpenCV Haar cascade) to flag images containing faces; useful for quickly finding personal photos.
- **Email / contact extraction** — bulk_extractor already pulls email addresses; add a stage that imports `email.txt` hits and cross-references with found `.pst` / `mbox` / `Thunderbird` profile paths.
- **Cryptocurrency address validation** — after bulk_extractor finds address-like strings, validate checksum and classify by coin type (BTC, ETH, LTC, etc.).
- **Timeline report** — **done** (`bin/image-timeline.sh`); outputs JSON/CSV/table from `scan_runs`, `files`, and `recovered_artifacts`.

---

## Infrastructure

- **Notification hooks** — send a desktop/phone notification (e.g. via `ntfy.sh` or `gotify`) when a long stage finishes or fails.
- **Schema migration support** — currently `analysis-schema.sql` uses `CREATE TABLE IF NOT EXISTS`; add a `schema_version` table and migration scripts for future column additions.
- **Automated backup** — **done** (`bin/recovery-backup.sh`); tiered rsync to `/mnt/CryptoBackup/recovery`.
- **Remote status page** — **done** (`bin/image-serve.sh` / `bin/image-serve.py`); read-only local web UI on port 7788 with dashboard, wallets, pictures, search, artifacts, timeline, and SQL query.
