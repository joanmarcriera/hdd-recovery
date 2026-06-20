"""Build a targeted password wordlist from artifacts already found on a disk.

A personal wallet password is far more likely to appear in strings pulled off
the owner's own disk (their email local-parts, screen names, domains, names)
than in a generic list like rockyou. This module turns bulk_extractor feature
rows into candidate tokens; bin/image-gen-wordlist.sh does the DB I/O and file
output. Pure and unit-tested — no I/O, no source media.

Token mangling (case/digit/year permutations) is intentionally left to hashcat
rules at crack time; here we only extract clean base tokens.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator

# bulk_extractor feature-file names → how to interpret the value. Matched as
# substrings so e.g. "email_histogram.txt" and "email.txt" both count.
_EMAIL_FEATURES = ("email", "rfc822")
_DOMAIN_FEATURES = ("domain", "url", "httplog")

_SPLIT_RE = re.compile(r"[._+\-]")
_TOKEN_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Common public mail/hosting domains whose second-level label is noise, not a
# personal clue.
_COMMON_DOMAINS = {
    "gmail", "googlemail", "google", "yahoo", "ymail", "hotmail", "outlook",
    "live", "msn", "aol", "icloud", "me", "mac", "proton", "protonmail",
    "gmx", "web", "mail", "yandex", "zoho", "fastmail", "amazonaws",
    "cloudfront", "akamai", "gstatic", "googleapis", "facebook", "fbcdn",
    "twitter", "windows", "microsoft", "apple", "wordpress", "blogspot",
}


def _domain_label(host: str) -> str | None:
    """Second-level label of a hostname (example from www.example.co.uk),
    or None if it's a common/public domain or unusable."""
    host = host.strip().lower().strip(".")
    if not host or "." not in host:
        return None
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return None
    # Heuristic for two-part TLDs (co.uk, com.au): take the label before them.
    second_level_tlds = {"co", "com", "org", "net", "gov", "ac", "edu"}
    if len(parts) >= 3 and parts[-2] in second_level_tlds:
        label = parts[-3]
    else:
        label = parts[-2]
    if label in _COMMON_DOMAINS or len(label) < 3:
        return None
    return label


def derive_candidates(feature_file: str, value: str) -> list[str]:
    """Yield candidate password tokens from one bulk_extractor feature row."""
    feature_file = (feature_file or "").lower()
    value = (value or "").strip()
    if not value:
        return []
    out: list[str] = []

    def add(tok: str) -> None:
        tok = tok.strip()
        if tok and _TOKEN_OK.match(tok):
            out.append(tok)

    if any(f in feature_file for f in _EMAIL_FEATURES) and "@" in value:
        local, _, domain = value.partition("@")
        add(local)
        for piece in _SPLIT_RE.split(local):
            add(piece)
        # collapsed local part (john.doe -> johndoe)
        add(re.sub(r"[._+\-]", "", local))
        label = _domain_label(domain)
        if label:
            add(label)
    elif any(f in feature_file for f in _DOMAIN_FEATURES):
        label = _domain_label(value)
        if label:
            add(label)
    else:
        # Generic feature (names, screen names): keep the raw token and its parts.
        add(value)
        for piece in _SPLIT_RE.split(value):
            add(piece)
    return out


def build_wordlist(rows: Iterable[tuple[str, str]], min_len: int = 3,
                   max_len: int = 40) -> list[str]:
    """Turn (feature_file, value) rows into a deduplicated, length-filtered
    candidate list. Order-preserving (first occurrence wins) and
    case-insensitive for dedup, but the original-cased token is kept."""
    seen: set[str] = set()
    result: list[str] = []
    for feature_file, value in rows:
        for tok in derive_candidates(feature_file, value):
            if not (min_len <= len(tok) <= max_len):
                continue
            key = tok.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(tok)
    return result


def merge_with_base(targeted: list[str], base_lines: Iterator[str],
                    max_total: int = 0) -> Iterator[str]:
    """Yield targeted tokens first (so hashcat tries the personal candidates
    early), then base wordlist lines, skipping base entries already emitted.
    max_total>0 caps the total number of lines yielded."""
    seen: set[str] = set()
    count = 0
    for tok in targeted:
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        yield tok
        count += 1
        if max_total and count >= max_total:
            return
    for line in base_lines:
        tok = line.rstrip("\n")
        if not tok:
            continue
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        yield tok
        count += 1
        if max_total and count >= max_total:
            return
