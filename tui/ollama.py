"""Ollama availability probing for the photo-tagging config screen (#14).

If Ollama is unreachable the tagging job opens a scan_runs record and then dies
with an unhelpful connection error, leaving a `failed` stage to clean up. This
module lets the TUI probe each configured host's /api/tags endpoint up front and
show green/red status before the run is launched. Textual-free so it is
unit-testable offline with an injected opener.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass


def parse_ollama_urls(raw: str) -> list[str]:
    """Split a comma-separated OLLAMA_HOSTS string into clean, de-duplicated
    base URLs (mirrors image-tag-photos.py's parse_ollama_urls)."""
    urls: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").split(","):
        url = part.strip().rstrip("/")
        if not url or url in seen:
            continue
        urls.append(url)
        seen.add(url)
    return urls


@dataclass(frozen=True)
class HostStatus:
    url: str
    ok: bool
    detail: str   # "3 model(s)" when reachable, else a short error reason


def _short_err(exc: Exception) -> str:
    msg = str(getattr(exc, "reason", "") or exc) or exc.__class__.__name__
    return msg[:60]


def probe_host(url: str, opener=urllib.request.urlopen, timeout: float = 2.0) -> HostStatus:
    """Hit <url>/api/tags and report reachability + model count."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/api/tags")
        with opener(req, timeout=timeout) as resp:
            payload = resp.read()
        data = json.loads(payload.decode("utf-8", "replace"))
        models = data.get("models", []) if isinstance(data, dict) else []
        return HostStatus(url, True, f"{len(models)} model(s)")
    except Exception as exc:  # network, JSON, HTTP — all mean "not usable"
        return HostStatus(url, False, _short_err(exc))


def probe_hosts(raw: str, opener=urllib.request.urlopen,
                timeout: float = 2.0) -> list[HostStatus]:
    """Probe every host in a comma-separated OLLAMA_HOSTS string."""
    return [probe_host(u, opener, timeout) for u in parse_ollama_urls(raw)]


def any_reachable(statuses: list[HostStatus]) -> bool:
    return any(s.ok for s in statuses)


def summarize(statuses: list[HostStatus]) -> str:
    """One-line plain-text summary (the screen colourizes per host itself)."""
    if not statuses:
        return "no Ollama hosts configured"
    up = sum(1 for s in statuses if s.ok)
    return f"{up}/{len(statuses)} host(s) reachable"
