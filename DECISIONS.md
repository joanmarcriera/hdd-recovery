# Decisions

## 2026-06-20 — Detached Web Runs Are Tracked Per Image DB

**Decision:** F4 stores detached web-launched pipeline and queue jobs in a new
per-image `supervised_runs` table. A single-image pipeline gets one row in that
image DB. A multi-image queue gets one matching queue row in each selected image
DB, all carrying the same queue PID/PGID, command, and log path. Web detection
checks these rows first and falls back to legacy `pgrep` only for older
unmanaged launches.

**Alternatives considered:**

- Add a central queue database outside the per-image DB layout.
- Continue using only `pgrep -af image-queue.py`.
- Record queue state only in the queue log file.

**Rationale:** The repository already treats each image DB as the durable
source of truth. Per-image rows avoid adding a second global state file and let
startup reconciliation work with the same PID marker guard used for `scan_runs`.
Duplicating queue rows across selected DBs is acceptable because cancellation
and reconciliation are idempotent against the recorded process group.

**Consequences:**

- Queue cancellation may send SIGTERM to the same PGID more than once when a
  queue spans multiple DBs; this is harmless.
- Supervised rows are updated at runner boundaries, while detailed per-stage
  useful progress remains in `scan_runs`.
- Existing unmanaged detached jobs can still appear via pgrep fallback until
  they finish.

**Revisit if:**

- A future multi-user deployment needs one global audit table for all queues.
- F7 monitor stuckness needs queue-level progress mirrored continuously from
  active `scan_runs`.

## 2026-06-20 — Useful Progress Is Separate From Console Output

**Decision:** F3 adds useful-progress probes to `lib/watchdog.py` rather than
treating stdout/stderr output as proof of progress. The pipeline and queue paths
now use `STAGE_PROGRESS_TIMEOUT` and `STAGE_PROGRESS_INTERVAL` with per-stage
counters from `lib/progress.py`; `scan_runs.heartbeat_at` is refreshed on probe
polls and `scan_runs.last_progress_at` is refreshed only when the counter
advances. Probe DB writes are rate-limited to avoid SQLite churn. Pipeline
stdout-idle timeout is available through `STAGE_IDLE_TIMEOUT` but defaults to
disabled.

**Alternatives considered:**

- Rely only on the F1 stdout-idle timeout.
- Add progress updates inside each individual recovery shell script.
- Treat log mtime as progress for every stage.

**Rationale:** Tools such as `bulk_extractor` can print repeated progress lines
while producing no new feature files, so stdout is not a reliable liveness
signal. Keeping probes in the supervisor avoids touching many forensic stage
scripts and lets offline tests exercise ddrescue maps, directories, and SQLite
row-count signals directly. Log mtime remains a fallback only when no stronger
stage output signal exists.

**Consequences:**

- A noisy process whose output directory or DB counter stalls can now be killed
  with rc `124` before the 12-hour wall timeout.
- Stages that create no measurable output before a long internal scan need a
  conservative `STAGE_PROGRESS_TIMEOUT` or an explicit override of `0`.
- The active `scan_runs` row now carries heartbeat/progress timestamps useful to
  F7 monitor stuckness.

**Revisit if:**

- A stage is found to be legitimately quiet for longer than the default progress
  timeout and needs a stage-specific timeout or probe.
- F4 durable supervised runs need the same progress-probe data outside
  per-image `scan_runs`.

## 2026-06-20 — Shared Watchdog Runner Uses Async Core With Sync Wrapper

**Decision:** Stage supervision now lives in `lib/watchdog.py`. The shared core
launches commands in a new session, streams stdout/stderr, applies a wall
timeout and optional idle-output timeout, and terminates the whole process group
with SIGTERM -> SIGKILL. `bin/image-pipeline.py` uses a synchronous wrapper so
its public `run_command()` contract remains `(rc, elapsed)` with rc `124` for
timeouts. The TUI launches commands with `start_new_session=True` and uses the
same async stream supervisor from the live log viewer.

**Alternatives considered:**

- Keep the existing synchronous CLI runner and duplicate timeout logic in the
  TUI.
- Convert the CLI pipeline completely to async.
- Add only `start_new_session=True` to the TUI and defer timeout handling.

**Rationale:** A small async core is directly testable with stdlib `asyncio`
and works naturally for the TUI streaming path. The sync wrapper keeps the CLI
and web queue behavior stable while sharing the process-group kill semantics.
Separating wall timeout (`STAGE_TIMEOUT`) from TUI idle-output timeout
(`STAGE_IDLE_TIMEOUT`) keeps F1 scoped while leaving F3 useful-progress probes
to handle tools that produce noisy but unproductive output.

**Consequences:**

- Pipeline child output is now relayed through the shared runner; it remains
  visible to queue logs and is also written to the pipeline log handle when one
  is open.
- Leaving the TUI live log screen no longer drops supervision; a detached
  watchdog continues draining stdout and enforcing timeout limits.
- TUI idle-output timeout defaults to 3600 seconds and can be disabled with
  `STAGE_IDLE_TIMEOUT=0`.

**Revisit if:**

- F3 useful-progress probes become the sole timeout source and stdout-idle
  timeout should be disabled by default.
- Queue/detached web runs move to a durable `supervised_runs` table in F4 and
  need a richer run result model than rc/elapsed/timeout kind.

## 2026-06-20 — Review UI Auth Uses TTYD Credentials By Default

**Decision:** The Python review UI now enables HTTP Basic auth when
`WEBUI_PASSWORD` or `TTYD_PASSWORD` is set. `WEBUI_USER`/`WEBUI_PASSWORD` take
precedence when present; otherwise the review UI reuses `TTYD_USER` and
`TTYD_PASSWORD`, with username defaulting to `admin`.

**Alternatives considered:**

- Add only `WEBUI_PASSWORD` and require operators to configure a second secret.
- Protect the review UI in the Go supervisor instead of `bin/image-serve.py`.
- Leave the review UI unauthenticated and document LAN-only use.

**Rationale:** Docker deployments already require `TTYD_PASSWORD`, so reusing it
protects existing LAN review routes without adding mandatory configuration.
Optional `WEBUI_*` overrides still allow separate credentials. Implementing the
gate inside `bin/image-serve.py` also protects standalone review UI runs when
operators set the same env vars.

**Consequences:**

- Existing Docker deployments will require review UI credentials after upgrade
  because `TTYD_PASSWORD` is already set.
- Local standalone `image-serve.py` runs remain unauthenticated unless
  `TTYD_PASSWORD` or `WEBUI_PASSWORD` is set.
- `/health` and `/status` remain unauthenticated for TrueNAS and Docker health
  probes.

**Revisit if:**

- The deployment moves outside a trusted LAN and needs first-class TLS/session
  handling.
- The Go supervisor becomes the sole public auth boundary for all web surfaces.
