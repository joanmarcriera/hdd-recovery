# Acquisition Readiness Checklist

Ordered preflight before first imaging run:

1. Install GNU ddrescue.
   - Status: completed on 2026-04-21.
   - Installed packages: `gddrescue`, `ddrescueview`, `ddrutility`.
   - Verify with `command -v ddrescue ddrescuelog ddrescueview`.

2. Choose and document one source disk per run.
   - Record model, serial, size, and by-path before touching it.
   - Prefer motherboard SATA for the source disk.
   - If the source disk is not obviously distinct from `/dev/sda`, `/dev/sdb`, and `/dev/sdc`, stop and re-verify.

3. Create one acquisition manifest for that source disk.
   - Use `manifests/source-disk-manifest-template.yaml`.
   - Fill in disk identity, attachment path, operator notes, image filename, and ddrescue log filename before starting.

4. Confirm the destination is mounted and has adequate free space.
   - Use `/mnt/recovery16tb/recovery/images` for image files.
   - Use `/mnt/recovery16tb/recovery/logs` for ddrescue logs.
   - Keep manifests under `/mnt/recovery16tb/recovery/manifests` or mirror them from `/root/hdd-recovery/manifests`.

5. Start the low-impact monitor session if it is not already running.
   - Command: `/root/start-recovery-monitor.sh`
   - Keep SMART checks infrequent and do not start self-tests during imaging.

6. Start with full-disk imaging only.
   - Do not mount the source disk first unless there is a specific reason.
   - Use a resumable ddrescue log file from the start.
   - Save image files with stable, human-readable names based on date, model, serial, and source path.

7. After imaging completes, work from the image file only.
   - Hash the image if desired.
   - Perform filesystem or partition analysis on the image, not on the original disk.

Recommended naming pattern:
- Image: `YYYYMMDD_<model>_<serial>_<source-dev>.img`
- Log: `YYYYMMDD_<model>_<serial>_<source-dev>.ddrescue.log`
- Manifest: `YYYYMMDD_<model>_<serial>_<source-dev>.yaml`

Do not do these during acquisition:
- format destination structures that already exist
- write anything to a source disk
- mount a source read-write
- run SMART self-tests
- run benchmarking or broad disk scans
