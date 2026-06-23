# Unified UI And Split Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose one LAN-facing human UI while allowing raw images, SQLite databases, exports, and logs to live on separate mounted storage roots.

**Architecture:** Keep the Go supervisor as the container front door. It will expose one public UI port, serve `/health` and `/status`, proxy `/terminal/` to an internal localhost-only ttyd process, and proxy all other human UI routes to `image-serve.py`. Scripts will derive database, image, export, and log paths from explicit root variables.

**Tech Stack:** Go `net/http` supervisor, Python `image-serve.py`, Bash recovery scripts, SQLite, Docker Compose.

---

### Task 1: Path Root Contract

**Files:**
- Modify: `lib/common.sh`
- Modify: `config/analysis-pipeline.env`
- Modify: `tui/config.py`
- Test: `tests/smoke/T13-path-roots.sh`

- [ ] **Step 1: Write the failing test**

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

CONFIG="$WORK_DIR/analysis.env"
mkdir -p "$WORK_DIR/images" "$WORK_DIR/db" "$WORK_DIR/exports" "$WORK_DIR/logs"
truncate -s 4096 "$WORK_DIR/images/sample.img"

cat >"$CONFIG" <<EOF
IMAGE_ROOT="$WORK_DIR/images"
DB_ROOT="$WORK_DIR/db"
EXPORT_ROOT="$WORK_DIR/exports"
LOG_ROOT="$WORK_DIR/logs"
DB_SUFFIX=".analysis.sqlite"
EOF

HDD_RECOVERY_ROOT="$ROOT_DIR" \
HDD_RECOVERY_CONFIG="$CONFIG" \
"$ROOT_DIR/bin/image-analysis-init.sh" "$WORK_DIR/images/sample.img" --print-db-path >"$WORK_DIR/db-path.txt"

expected="$WORK_DIR/db/sample.img.analysis.sqlite"
actual="$(cat "$WORK_DIR/db-path.txt")"
[[ "$actual" == "$expected" ]] || {
  echo "expected DB path $expected, got $actual" >&2
  exit 1
}

sqlite3 "$expected" "SELECT image_path FROM image_info WHERE id=1;" | grep -Fx "$WORK_DIR/images/sample.img"
sqlite3 "$expected" "SELECT export_root FROM image_info WHERE id=1;" | grep -Fx "$WORK_DIR/exports/sample"
test -d "$WORK_DIR/exports/sample/recovered"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `tests/smoke/T13-path-roots.sh`
Expected: FAIL because `DB_ROOT` is ignored and the DB is created beside the image.

- [ ] **Step 3: Implement minimal path root behavior**

Add `HDD_RECOVERY_ROOT` support to scripts that source `lib/common.sh`, add `DB_ROOT`, and make `default_db_path` derive `DB_ROOT/<image-name><DB_SUFFIX>` when `DB_ROOT` is set.

- [ ] **Step 4: Run test to verify it passes**

Run: `tests/smoke/T13-path-roots.sh`
Expected: PASS.

### Task 2: Supervisor Single UI Port

**Files:**
- Modify: `docker/supervisor/main.go`
- Test: `docker/supervisor/main_test.go`

- [ ] **Step 1: Write failing unit tests**

Test that supervisor config defaults to one public `UI_PORT`, gives ttyd and web UI localhost-only internal ports, and returns multiple Ollama endpoints from `OLLAMA_HOSTS`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/supervisor && go test ./...`
Expected: FAIL because config helpers do not exist yet.

- [ ] **Step 3: Implement config helpers and reverse proxy**

Create config parsing helpers, bind ttyd to `127.0.0.1`, bind `image-serve.py` to `127.0.0.1`, expose one supervisor HTTP server on `UI_PORT`, proxy `/terminal/` to ttyd, proxy all other routes to image UI, and keep `/health` and `/status` on the same port.

- [ ] **Step 4: Run Go tests**

Run: `cd docker/supervisor && go test ./...`
Expected: PASS.

### Task 3: Docker Runtime Contract

**Files:**
- Modify: `docker/Dockerfile`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/DOCKERHUB.md`
- Test: static grep checks

- [ ] **Step 1: Add static expectations**

Verify that Docker exposes only `7788`, compose maps only `${UI_PORT:-7788}:7788`, and compose mounts `IMAGE_ROOT`, `DB_ROOT`, `EXPORT_ROOT`, and `LOG_ROOT` separately.

- [ ] **Step 2: Update Docker and compose files**

Set `EXPOSE 7788`, configure env vars for the four roots, retain `TTYD_PASSWORD`, and keep remote Ollama settings.

- [ ] **Step 3: Run static checks**

Run:

```bash
grep -n "EXPOSE 7788" docker/Dockerfile
grep -n "7681\\|8080" docker/docker-compose.yml && exit 1 || true
grep -n "IMAGE_ROOT\\|DB_ROOT\\|EXPORT_ROOT\\|LOG_ROOT" docker/docker-compose.yml
```

Expected: Dockerfile exposes one port; compose no longer publishes `7681` or `8080`.

### Task 4: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docker/README.md`
- Modify: `AGENTS.md` only if required by changed architecture

- [ ] **Step 1: Update public docs**

Document one URL, four mount roots, remote/multiple Ollama endpoints, LAN-only security, `docker run`, Compose, and TrueNAS-style setup.

- [ ] **Step 2: Verify stale port claims are removed from public docs**

Run:

```bash
rg -n "7681|8080|beside the image|same directory" README.md docker/README.md docker/DOCKERHUB.md
```

Expected: No stale public setup instructions for exposed `7681` or `8080`; any mention is clearly historical/internal.

### Task 5: Final Verification

**Files:**
- All changed files

- [ ] **Step 1: Run smoke tests**

Run: `tests/smoke/T13-path-roots.sh`
Expected: PASS.

- [ ] **Step 2: Run Go tests**

Run: `cd docker/supervisor && go test ./...`
Expected: PASS.

- [ ] **Step 3: Review git diff**

Run: `git diff --stat && git diff --check`
Expected: no whitespace errors and only scoped changes.
