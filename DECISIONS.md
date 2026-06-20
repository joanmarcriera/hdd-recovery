# Decisions

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
