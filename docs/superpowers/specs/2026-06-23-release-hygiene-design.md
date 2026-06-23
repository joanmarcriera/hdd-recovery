# Release Hygiene — Open-Source Readiness

**Date:** 2026-06-23
**Status:** Approved, implementing
**Scope:** Second workstream toward public release. Two sub-cycles: **2a portability** (code/config, tests-first) and **2b docs/repo hygiene**. Decisions confirmed with the owner:

- **Keep real identifiers** (disk serial, UUID, LAN IP, build hostnames) — owner's own gear, acceptable to publish. **No privacy scrub.**
- **Keep the `.claude/skills/` project skills** (run-hdd-recovery, deploy-hdd-forensics); lightly genericize so a forker can override infra via env vars. Gitignore the other agent dotdirs.
- **Full `docs/` reorganization.**

## Sub-cycle 2a — Portability (behavior-preserving, tests-first)

Several modules hardcode the owner's storage layout instead of the config/env var that exists for it. For an OSS release these should honour an env override while keeping the **current value as the default** (so existing deployments behave identically). Per the owner's rule, characterization tests land **before** each edit.

### Tests first (new/extended)
- `tests/unit/test_common_sh_paths.py` (NEW): characterize `lib/common.sh` `image_name`, `image_basename`, `default_db_path`, `default_export_root` across env permutations (`DB_ROOT`/`EXPORT_ROOT`/`DB_SUFFIX` set vs unset) via a bash subprocess. These are critical, currently untested, and adjacent to the paths work.
- `tests/unit/test_serve_images.py` (EXTEND): assert `image_roots()` honours `RECOVERY_ROOT`/`EXPORT_ROOT` env precedence and falls back to the current default when unset.
- Backup scripts: add a `--print-dest` (or dry-run preview) characterization path only if it can be done without side effects; otherwise test the destination-resolution by sourcing and echoing the resolved var. No rsync is executed in tests.

### Edits (each guarded by the tests above)
- `lib/serve_app.py`, `lib/serve_images.py`: replace the literal `/mnt/recovery16tb/recovery` fallback with `os.environ.get("RECOVERY_ROOT")` → `EXPORT_ROOT`-derived → the existing literal as last-resort default. Identical behavior when env unset.
- `bin/send-image-to-truenas.sh`: use `${LOG_ROOT:-<current default>}` instead of the hardcoded logs path.
- `bin/recovery-backup.sh`, `tui/screens/backup.py`: honour `RECOVERY_BACKUP_DST` env (default = current `/mnt/CryptoBackup/...`).
- `manifests/source-disk-manifest-template.yaml`: the *template* hostname becomes a generic placeholder (it's a fill-in template, not real data).

### Out of scope for 2a
No change to the keep-as-default values themselves; no scrubbing of real identifiers; no broad bash retrofit.

## Sub-cycle 2b — Docs & repo hygiene (low code risk)

### Repo hygiene
- `git rm --cached .DS_Store`; add `.DS_Store` to `.gitignore`.
- Add agent tooling dirs to `.gitignore`: `.codebuddy/`, `.continue/`, `.junie/`, `.kiro/`, `.agents/` (and keep `.archive/` ignored if not already). **Keep `.claude/`** tracked (the project skills live there).
- `skills-lock.json`: gitignore.

### Standard OSS files (NEW)
- `CONTRIBUTING.md` — dev setup, `./tests/run-unit.sh`, shellcheck, the preview/`--run` safety convention, PR expectations.
- `SECURITY.md` — responsible-disclosure contact; note the forensic/dual-use nature.
- `CODE_OF_CONDUCT.md` — Contributor Covenant reference.
- `CHANGELOG.md` — stub (Keep a Changelog) seeded with the de-dup + hygiene work.

### Docs reorg (full tree)
Create and populate:
```
docs/
  README.md            (hub/index)
  operator/   acquisition-checklist, future-disk-checklist, ddrescue-workflow
  analysis/   image-analysis-workflow, bulk-discovery-runbook
  recovery/   wallets (was BITCOIN-WALLET-RECOVERY), pictures (was PICTURE-RECOVERY)
  reference/  tool-selection (was TOOL-SELECTION-IMAGE-ANALYSIS)
  internal/   DECISIONS, TASKS, PROJECT_STATE, CODEX-HANDOFF, STAGE1-PROGRESS, IMPROVEMENTS
```
Moves use `git mv` (preserve history). **Keep at root:** `README.md`, `LICENSE`, `TODO.md`, `CLAUDE.md` and `AGENTS.md` (the latter two are read from the repo root by the AI tools that use them — moving them breaks discovery).

### README polish
Front-door rewrite: one-line what-it-is, safety/ethics banner, quickstart (Docker + local), a "Documentation" section linking the new `docs/` tree, and pointers to CONTRIBUTING/SECURITY. Keep existing technical content; relink moved docs.

### `.claude/skills` genericization
Add a short "Forking this project" note to `deploy-hdd-forensics` showing the env vars to override (`IMAGE_REPO`/Docker Hub account, builder host, NAS host/IP) while leaving the owner's values as the working defaults.

## Testing & verification
- `./tests/run-unit.sh` green after 2a (including the new path tests) and after 2b.
- `shellcheck --severity=error` clean on any touched scripts.
- Every moved doc link is updated (grep for old paths; no dangling links).
- A code-review subagent reviews the 2a diff (logic) and the link integrity of 2b before commit.

## Risk & rollout
- 2a is behavior-preserving (env defaults = current literals); covered by new tests.
- 2b is docs/config only — no runtime behavior change; the live container is unaffected and undeployed.
- Each sub-cycle is its own commit set; independently revertable.

## Acceptance criteria
1. New `test_common_sh_paths.py` + extended `test_serve_images.py` pass and were written before the edits.
2. The five hardcoded-path sites honour an env override with the current value as default; `./tests/run-unit.sh` green.
3. `.DS_Store` untracked + gitignored; agent dotdirs gitignored; `.claude/` still tracked.
4. CONTRIBUTING/SECURITY/CODE_OF_CONDUCT/CHANGELOG exist; README links the new docs tree.
5. Docs relocated under `docs/` via `git mv` with no dangling internal links; CLAUDE.md/AGENTS.md remain at root.
6. shellcheck clean; no deployment performed.
