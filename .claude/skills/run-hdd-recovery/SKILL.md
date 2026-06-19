---
name: run-hdd-recovery
description: Build, launch, smoke-test, and drive the hdd-recovery web review UI (bin/image-serve.py). Use when asked to run, start, serve, screenshot, or test the hdd-recovery / hdd-forensics web UI, gallery, or image-serve server.
---

# Run hdd-recovery

`hdd-recovery` is a disk-image forensics pipeline. Its operator-facing surface is
a read-only **web review UI** (`bin/image-serve.py`) — a stdlib `http.server`
app that renders one SQLite analysis DB per disk image, plus the carved/recovered
image files those DBs point at (home dashboard, gallery with thumbnails, wallet
hits, SQL console, ddrescue map viewer). In production it runs inside the
`joanmarcriera/hdd-forensics` container behind a Go supervisor that also proxies
a ttyd-hosted Textual TUI; this skill drives the **web UI directly**, which is
the layer most changes touch.

The UI needs *data* to be meaningful, so the driver synthesizes a throwaway
workspace (a sample `*.analysis.sqlite` DB + a real carved JPEG) and launches the
server against it. All paths below are relative to the repo root.

## Prerequisites

Only `python3` + **Pillow** (everything else is stdlib). On a clean Ubuntu box:

```bash
apt-get install -y python3 python3-pil    # or: pip3 install pillow
```

The driver exits with a clear install hint if Pillow is missing.

## Run (agent path) — the driver

One command launches the real server, drives it, and asserts the behavior that
matters, then tears down (exit non-zero on any failure):

```bash
python3 .claude/skills/run-hdd-recovery/driver.py smoke --port 7802
```

Expected tail (21 checks, all `ok`):

```
  ok   home shows human image size
  ok   home shows stage-group chips
  ok   GET /queue → 200
  ok   queue offers presets + start
  ok   POST /queue empty → 400
  ...
  ok   thumb < original (707 < 34529 B)
  ok   traversal /etc/passwd → 403

21 passed, 0 failed
```

It checks: home renders (gzipped, image name not the db filename, human image
size, per-stage-group chips, build footer); the multi-image `/queue` page lists
images + presets and validates an empty POST (400); the `/db` detail page renders
the seeded `scan_runs` history; the gallery serves downscaled `/thumb` JPEGs
instead of full-res originals; thumbnails carry `ETag`/`Cache-Control` and
revalidate to `304`; `/file` is cached; path traversal is blocked.

The batch orchestrator behind `/queue` is `bin/image-queue.py`; preview the exact
per-image commands it would run without executing anything:

```bash
python3 bin/image-queue.py --dry-run --jobs 2 --skip-done --keep-going \
  --stages structure-scan,carve-foremost /tmp/a.analysis.sqlite /tmp/b.analysis.sqlite
```

The fixture (`build_workspace` in the driver) seeds `image_info.image_size_bytes`
and a spread of `scan_runs` (`ok`/`partial`/`running`/`failed`) — useful data for
work on the home dashboard's image-size and per-stage columns.

To eyeball it in a browser instead, leave it serving against the sample data:

```bash
python3 .claude/skills/run-hdd-recovery/driver.py serve --port 7803
# → http://127.0.0.1:7803/   (Ctrl-C to stop). Add --keep to retain the workspace.
```

## Screenshots (visual check)

The dashboard and gallery are visual, so `shot` mode drives headless Chrome over
the running server and writes PNGs you can open/Read:

```bash
python3 .claude/skills/run-hdd-recovery/driver.py shot --port 7808 --out /tmp/hdd-shots
# ok dashboard: /tmp/hdd-shots/dashboard.png
# ok gallery:   /tmp/hdd-shots/gallery.png
# ok queue:     /tmp/hdd-shots/queue.png
```

Needs Chrome/Chromium (auto-detected: macOS `Google Chrome.app`, or
`google-chrome`/`chromium` on PATH). `dashboard.png` should show the Image / Size
/ Stages columns with coloured stage-group chips; `queue.png` the preset
checkboxes + image list.

## Test (Go supervisor)

The container's process manager has its own Go tests:

```bash
cd docker/supervisor && go test ./...
```

## Run (against your own data)

Point the real server at any directory tree containing `*.analysis.sqlite` files
(it globs `**/*.analysis.sqlite`). Bind to localhost:

```bash
python3 bin/image-serve.py --root /path/to/db-root --host 127.0.0.1 --port 7799
```

`APP_VERSION` (env) shows in the page footer and `/status`; default is `dev`.

## Direct invocation (internals — no HTTP)

Most page changes touch pure functions in `bin/image-serve.py` (`page_home`,
`page_db`, `page_gallery`, `make_thumbnail`, …). You can build the fixture and
call them directly — faster than the HTTP loop when iterating on rendering:

```bash
python3 - <<'PY'
import importlib.util, tempfile, pathlib, shutil
def load(n,p):
    s=importlib.util.spec_from_file_location(n,p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
drv = load("drv", ".claude/skills/run-hdd-recovery/driver.py")
srv = load("imgserve", "bin/image-serve.py")
work = pathlib.Path(tempfile.mkdtemp(prefix="hdd-di-"))
db, jpeg = drv.build_workspace(work)          # the same sample DB the smoke test uses
srv.Handler.root = str(work)
print("page_home:", len(srv.page_home(str(work))), "chars")
print("page_db has carve-scalpel:", "carve-scalpel" in srv.page_db(db))
print("thumbnail bytes:", len(srv.make_thumbnail(str(jpeg))[0]))
shutil.rmtree(work, ignore_errors=True)
PY
```

## Production container (not run by this skill)

The shipped artifact is the `joanmarcriera/hdd-forensics` amd64 image (Go
supervisor + ttyd TUI + this web UI on one port). Rebuild/push it on a **native
amd64** host — emulated builds segfault CPython during a Kali package's
post-install:

```bash
./docker/build-and-push.sh        # native amd64 host, after `docker login`
```

(or the `Build and push image` GitHub Actions workflow). Verify a deployed
container with `curl -s http://<host>:7788/status | grep version`.

## Gotchas

- **Carved files must live under `--root`.** `/thumb` and `/file` reject any
  `full_path` that resolves outside the server root with `403`, and the gallery
  silently filters those rows out (so the gallery looks empty and no `/thumb`
  tags appear). The driver puts the sample DB *and* its carved JPEG under one
  root for this reason.
- **Thumbnails are disk-cached** under `$HDD_THUMB_CACHE` (default
  `$TMPDIR/hdd-thumb-cache`), keyed by source path + mtime + size. Stale-looking
  thumbnails after editing an image usually mean a cache hit; clear that dir.
- **`ThreadingHTTPServer`, no auth.** Bind to `127.0.0.1` only; the production
  supervisor is what adds the password gate (ttyd) and the single public port.
- **The TUI (`bin/tui.sh`) is not driven here** — it's a Textual app launched via
  `uv` and needs a real TTY (ttyd in production). Driving it headlessly is a
  separate tmux/`capture-pane` exercise.
- **DBs bake in *absolute* paths from the acquisition machine.** Real-world DBs
  store `image_info.export_root` / `recovered_artifacts.full_path` like
  `/mnt/recovery16tb/recovery/exports/...`. For the gallery to resolve images in
  the container, the data must be mounted at *that exact path* (not `/data/...`),
  and `WEB_ROOT` must be the common ancestor of both `images/` and `exports/`
  (e.g. `/mnt/recovery16tb/recovery`) — never the `images/` subdir alone, or the
  carved files (under the sibling `exports/`) fall outside root and 403. Do **not**
  rewrite the paths inside the DBs (evidence-preservation rule); fix the mount.
- **Only one public port in the deployed image: `7788`.** The supervisor serves
  the web UI at `/`, proxies ttyd at `/terminal/`, and exposes `/health` + `/status`.
  ttyd (`127.0.0.1:17681`) and image-serve (`127.0.0.1:17788`) are loopback-only
  behind it. Any other published host port (legacy `7681`/`8080` mappings) is dead.
- **Build amd64 on a native amd64 host only.** Cross-building under QEMU on Apple
  Silicon segfaults CPython during a Kali package's post-install
  (`py3compile … status code -11`). Use a real amd64 box or the CI workflow.

## Troubleshooting

- `FATAL: Pillow not installed` → install it (see Prerequisites).
- **"No *.analysis.sqlite files found under /data/db"** (deployed) → `WEB_ROOT`
  is empty/unset (falls back to `/data/db`) or the storage volume isn't mounted.
  Set `WEB_ROOT` to the tree that contains `images/` + `exports/` and mount the
  data there (see the path-mapping Gotcha). Check inside the container:
  `echo "$WEB_ROOT"; find "$WEB_ROOT" -name '*.analysis.sqlite' | head`.
- Gallery shows no thumbnails / `gallery uses /thumb` fails → a carved
  `full_path` is outside `--root` (see Gotchas); fix the layout or the root.
- `address already in use` → pick another `--port`.
