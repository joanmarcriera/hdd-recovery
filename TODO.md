# TODO - hdd-recovery

This file is the short live TODO for durable, forward-looking work. It is **not**
a per-run operator handoff — live state for an in-flight pipeline belongs in
`scan_runs` and the pipeline log, not here. Resolved point-in-time handoffs are
moved to `.archive/` (gitignored).

## Docker And Verification

- Rebuild/redeploy the Docker image after the current long pipeline is safe to stop or has finished.
- Verify the Dockerfile `btcrecover.py` wrapper fix in the rebuilt container.
- Restore GPU visibility to the container before running hashcat/KeePass/wallet cracking checks; the current container exposes no NVIDIA runtime to `hashcat -I`.
- Run the owner-side smoke tests in `tests/smoke/` with real fixtures, GPU, and Docker access.

## Code Follow-Ups

- Add safe output-directory backup or `--force` handling to `bin/image-carve.sh` before repeated carving becomes routine.
- Make `image-pipeline.py` surface `partial` scan outcomes distinctly in the web UI, not only process return codes.
- Consider copying `tests/smoke/` into the Docker image or documenting that smoke tests are repo-host only.
- Add a lightweight stale-stage checker for `scan_runs.status='running'` when the corresponding process is gone.

## UI / Operator Improvements

- New-disk wizard: choose device, verify model/serial/by-path, generate job config, preview ddrescue, then run.
- Stage dependency enforcement in the TUI and web UI.
- Cross-disk session log with chronological stage completions and failures.
- Disk health sidebar with current SMART attributes during imaging.
- Better ETA/progress for ddrescue, bulk_extractor, and carving stages.
- TUI/export browser for recovered artifacts and wallet candidates.
- GPS-tagged photo map/filter view in the web review UI.
- Expose generated plaso SQLite/timeline file paths in the web/TUI detail panels.
- Config editor for `analysis-pipeline.env`.
- Notification hooks when long stages finish or fail.

## Analysis Improvements

- Face detection pass for recovered/picture candidates.
- Email/contact extraction importer from bulk_extractor outputs and mail stores.
- Cryptocurrency address validation and coin classification.
- Monero seed detection rule for 25-word seed phrases.
- Windows UserAssist and Shellbags parsing for wallet software execution and folder access history.
- Cross-reference deleted-file recovery outputs with TSK indexed paths to detect allocated duplicates.
- Cross-disk deduplication after human review only.
- Formal `schema_version` tracking for migrations.
