# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project does not
yet use formal version tags (track [releases](https://github.com/joanmarcriera/hdd-recovery/releases)).

## [Unreleased]

### Added
- Per-image **reset** (`bin/image-reset.sh`) and an overlay **storage guard**
  (`lib/storage_guard.py`, `bin/storage-check.sh`) that refuses to write
  recovery data onto a container overlay layer.
- Shared helpers extracted to reduce duplication: `lib/timestamp.py` (`utc_now`),
  `lib/db.py` (`ro_db`, `open_writable_db`, `fetch_image_info`, scan-run
  start/end), and bash `prepare_work_dirs` / `rotate_with_backup` in
  `lib/common.sh`.
- Open-source project files: `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, and this changelog.
- Characterization tests for `lib/common.sh` path derivation and the web image
  discovery prune.

### Changed
- Documentation reorganized under `docs/` (operator / analysis / recovery /
  reference / internal).
- Image discovery now ignores `_rsync_conflict_backups_*` directories so stale
  backup copies of a database no longer appear as phantom duplicates in the UI.

### Fixed
- Web image discovery no longer enqueues stale backup-copy databases.
