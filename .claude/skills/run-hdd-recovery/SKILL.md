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

Expected tail (12 checks, all `ok`):

```
  ok   gallery uses /thumb thumbnails
  ok   GET /thumb → 200 image/jpeg
  ok   thumb has ETag + Cache-Control
  ok   thumb < original (707 < 34529 B)
  ok   thumb revalidation → 304
  ok   GET /file → 200 with ETag
  ok   traversal /etc/passwd → 403

12 passed, 0 failed
```

It checks: home renders (gzipped, lists the sample DB, shows the build footer),
the gallery serves downscaled `/thumb` JPEGs instead of full-res originals,
thumbnails carry `ETag`/`Cache-Control` and revalidate to `304`, `/file` is
cached, and path traversal is blocked.

To eyeball it in a browser instead, leave it serving against the sample data:

```bash
python3 .claude/skills/run-hdd-recovery/driver.py serve --port 7803
# → http://127.0.0.1:7803/   (Ctrl-C to stop). Add --keep to retain the workspace.
```

## Run (against your own data)

Point the real server at any directory tree containing `*.analysis.sqlite` files
(it globs `**/*.analysis.sqlite`). Bind to localhost:

```bash
python3 bin/image-serve.py --root /path/to/db-root --host 127.0.0.1 --port 7799
```

`APP_VERSION` (env) shows in the page footer and `/status`; default is `dev`.

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

## Troubleshooting

- `FATAL: Pillow not installed` → install it (see Prerequisites).
- Gallery shows no thumbnails / `gallery uses /thumb` fails → a carved
  `full_path` is outside `--root` (see Gotchas); fix the layout or the root.
- `address already in use` → pick another `--port`.
