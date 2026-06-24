---
name: deploy-hdd-forensics
description: Build and ship the joanmarcriera/hdd-forensics container after a code change, and deploy it to the TrueNAS app. Use when asked to push/build/ship/release/deploy hdd-recovery or hdd-forensics, rebuild the container, or get a code fix live on the NAS.
---

# Deploy hdd-forensics

The hdd-recovery code runs in production inside the `joanmarcriera/hdd-forensics`
container (web review UI + Textual TUI + a Go supervisor), deployed as a TrueNAS
app. Getting a code change live is a fixed four-step pipeline. **The build must
run on a native linux/amd64 host** — emulated builds on Apple Silicon segfault
CPython during Kali package post-install (`docker/build-and-push.sh` aborts if
`uname -m` isn't amd64).

## Hosts

The addresses below are the maintainer's own setup. **Forking this project?**
Substitute your own: set `IMAGE_REPO=<you>/<image>` for `docker/build-and-push.sh`,
and replace the builder/NAS host+IP with yours (any native linux/amd64 host can
build; any Docker host can run it).

| Role | Address | Notes |
|------|---------|-------|
| Dev / repo | this Mac | edit + commit + push to GitHub `main` |
| amd64 builder | `marc@optiplex990` | has Docker + `~/hdd-recovery` checkout + `docker login` as joanmarcriera |
| Runtime (NAS) | `truenas_admin@192.168.0.5` | container `ix-hdd-forensics-hdd-forensics-1`, UI on `:7788` |

## The pipeline

```bash
# 1. On the Mac: make the change, run unit tests, commit, push to main
./tests/run-unit.sh                       # 178+ stdlib unittests, no deps/fixtures
git add -A && git commit -m "..." && git push origin main

# 2. On the amd64 builder: pull + build + push the image
ssh marc@optiplex990 'cd ~/hdd-recovery && git pull --ff-only && ./docker/build-and-push.sh'
#   -> pushes joanmarcriera/hdd-forensics:latest  and  :<short-sha>-<UTCdate>
#   The build is layer-cached: code is added late, so a code-only change is fast.
#   Confirm the printed digest's :<sha> matches `git rev-parse --short HEAD`.
```

Run the build in the background and tail it — most layers are cached so it
usually finishes in well under a minute, but a Dockerfile/apt change rebuilds
the Kali base and takes many minutes. **Pull and build as SEPARATE steps:**

```bash
# a) Pull first, in the FOREGROUND, and confirm HEAD — do NOT chain it with the
#    backgrounded build. `git pull && nohup build &` backgrounds the whole
#    compound, so the build can race the pull and bake the OLD sha.
ssh marc@optiplex990 'cd ~/hdd-recovery && git pull --ff-only 2>&1 | tail -3; git rev-parse --short HEAD'
# b) Then build in the background.
ssh marc@optiplex990 'cd ~/hdd-recovery && nohup ./docker/build-and-push.sh > /tmp/hdd-build.log 2>&1 & echo PID $!'
ssh marc@optiplex990 'tail -6 /tmp/hdd-build.log'   # look for "Done. Pushed:" + :<sha> == HEAD
```

**`git pull --ff-only` aborts on stray untracked files** ("would be overwritten
by merge", e.g. a `docs/superpowers/plans/*.md` the build wrote locally). Move
the named file aside, then re-pull:
`ssh marc@optiplex990 'cd ~/hdd-recovery && mv <file> /tmp/ && git pull --ff-only'`.

## Step 3 — make the runtime pull the new image (DOES NOT happen automatically)

Pushing `:latest` does **not** restart the running container. The 47h-old
container keeps the old code until the TrueNAS app is recreated. Pulling the new
image kills whatever is running inside — **including a live analysis queue.**

- `scan_runs` rows persist on the recovery volume, and the queue runs with
  `--skip-done`, so a relaunched queue resumes where it left off (it re-runs only
  the stage that was mid-flight). But the queue's frozen arg-list is lost — you'd
  re-spawn it.
- **Before recreating, check for a running queue/pipeline:**
  ```bash
  ssh truenas_admin@192.168.0.5 'ps aux | grep -E "image-queue|image-pipeline" | grep -v grep'
  ```
- If a long run is active and the change is display-only (e.g. discovery/UI),
  **prefer to defer the redeploy** until the queue finishes, or apply the fix
  operationally on the live container instead of recreating it.

**The reliable redeploy (tested):** TrueNAS caches the image digest and reports
`image_updates_available: false`, so a plain redeploy reuses the stale local
image. Force the new image, then recreate the app:

```bash
ssh truenas_admin@192.168.0.5 'sudo docker pull joanmarcriera/hdd-forensics:latest'
ssh truenas_admin@192.168.0.5 'sudo midclt call app.redeploy hdd-forensics'   # returns a job id
```

`app.redeploy` runs as a middleware job (≈15–20 s). The container name is
`ix-hdd-forensics-hdd-forensics-1`; its compose lives under
`/mnt/.ix-apps/app_configs/hdd-forensics/`. Do this unprompted only when the
operator asked — recreating kills a live queue (which resumes via `--skip-done`).

## Step 4 — verify the new code is actually live

```bash
# image id of the running container must equal the freshly-pulled :latest id:
ssh truenas_admin@192.168.0.5 'sudo docker inspect ix-hdd-forensics-hdd-forensics-1 --format "{{.Image}}"'
ssh truenas_admin@192.168.0.5 'sudo docker image inspect joanmarcriera/hdd-forensics:latest --format "{{.Id}}"'
# or grep a code marker straight from the running container:
ssh truenas_admin@192.168.0.5 'sudo docker exec ix-hdd-forensics-hdd-forensics-1 grep -c "<a string from your change>" /root/hdd-recovery/<file>'
```

## Gotchas learned the hard way

- **Build host arch matters.** Only optiplex990 (amd64) can build this image; the
  Mac cannot. The script self-aborts on the wrong arch.
- **`:latest` ≠ deployed.** Three separate steps: push to GitHub, build+push image,
  recreate the app. Skipping step 3 means the fix is built but not live.
- **Discovery / display fixes can land without a redeploy** by removing the
  offending file from the data tree (e.g. moving a stray `_rsync_conflict_backups_*`
  DB out of `images/`) — the old code stops seeing it immediately.
- **Don't interrupt a running queue** to deploy a non-urgent change. See
  [[queue-stops-after-first-image-investigation]] for queue behavior.
- **Smoke-test the pipeline on a small image after deploying** — unit tests miss
  things that only surface end-to-end. Pick the smallest unprocessed `.img`,
  clean any stray `recovery/db/*.sqlite` stubs, and run a bounded queue:
  ```bash
  sudo docker exec -d ix-hdd-forensics-hdd-forensics-1 bash -lc '
    python3 /root/hdd-recovery/bin/image-queue.py --jobs 1 --skip-done --keep-going \
      --stage-timeout 1800 \
      --stages init-db,structure-scan,index-tsk,detect-wallets,detect-pictures,bulk-extractor-raw,carve-foremost,generate-report \
      <db1> <db2> > /mnt/recovery16tb/recovery/queue-logs/test-queue-$(date -u +%Y%m%dT%H%M%SZ).log 2>&1 &'
  ```
  Then poll the log for `queue finished: N ok, 0 failed`, confirm `recovery/db/`
  stays empty (no new stubs), and that `find_databases` shows no duplicate
  basenames. This loop caught the `init-db` DB_ROOT-stub bug and a latent
  `printf '-'` crash in `generate-report` that no unit test would have. Name test
  logs `test-queue-*.log` so they don't match the UI's `queue-*.log` glob; remove
  them when done.
