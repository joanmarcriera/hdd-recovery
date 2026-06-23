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
the Kali base and takes many minutes:

```bash
ssh marc@optiplex990 'cd ~/hdd-recovery && nohup ./docker/build-and-push.sh > /tmp/hdd-build.log 2>&1 & echo PID $!'
ssh marc@optiplex990 'tail -30 /tmp/hdd-build.log'   # look for "Done. Pushed:"
```

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

Recreate the app via the TrueNAS UI (Apps → hdd-forensics → restart/redeploy) or
`docker pull joanmarcriera/hdd-forensics:latest` + recreate the compose service.
Do not do this unprompted while a queue is running — confirm with the operator.

## Step 4 — verify

```bash
curl -s http://192.168.0.5:7788/status | grep -i version   # should show the new :<sha>-<date>
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
