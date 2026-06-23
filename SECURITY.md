# Security Policy

## Reporting a vulnerability

Please report security issues **privately**. Do not open a public issue for a
vulnerability.

- Use GitHub's [private vulnerability reporting](https://github.com/joanmarcriera/hdd-recovery/security/advisories/new)
  ("Report a vulnerability" on the Security tab), or
- email the maintainer at the address on the GitHub profile.

Include what you found, how to reproduce it, and the potential impact. You can
expect an initial response within a reasonable time; please allow time for a fix
before public disclosure.

## Scope and intended use

hdd-forensics is a **dual-use forensic tool**. It extracts private keys,
passwords, browser data, and personal files from disk images. It is intended for:

- recovering **your own** data, and
- **authorized** forensic, incident-response, or security-research work.

Using it against media you are not authorized to examine may be illegal. The
maintainers accept no liability for misuse.

## Operational hardening

- The web UI (port `7788`) and the `/terminal/` shell require a password
  (`TTYD_PASSWORD` / `WEBUI_PASSWORD`). Treat the UI as **LAN-only**; if exposing
  it, front it with a TLS reverse proxy.
- `/health` and `/status` are intentionally unauthenticated for LAN probes and
  expose no sensitive data.
- The container needs no `--privileged` flag or extra capabilities; it reads
  images via a read-only-intent volume mount.
- Recovery outputs contain secrets by design — protect the `exports/` and SQLite
  catalogs accordingly.
