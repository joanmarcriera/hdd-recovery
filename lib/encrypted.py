"""Pure detection helpers for encrypted containers and volumes.

No I/O — callers pass in header bytes and file metadata; the orchestration
(reading the image, scanning the recovered corpus, writing to SQLite) lives in
bin/image-detect-encrypted-containers.sh's inline python block. Keeping the
classification and partition-table parsing pure makes them unit-testable offline
with synthetic bytes, with no fixtures and no source media.

Confidence is 0-100. The CLAUDE.md "wallet hits are candidates only" lesson
applies here too: signature matches are high-confidence; extension/entropy
heuristics are deliberately low-confidence leads for human review.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Detection:
    kind: str          # luks, bitlocker, kdbx, pgp-armored, pgp-message, zip-encrypted, ...
    confidence: int    # 0-100
    detail: str


# ── Extension hints (lower-confidence leads; encrypted containers often have no
#    reliable magic, e.g. VeraCrypt/TrueCrypt are entropy-only by design) ──────
EXTENSION_HINTS: dict[str, tuple[str, int]] = {
    ".tc":    ("truecrypt", 45),
    ".hc":    ("veracrypt", 45),
    ".vc":    ("veracrypt", 45),
    ".kdbx":  ("kdbx", 70),
    ".kdb":   ("keepass1", 65),
    ".gpg":   ("gpg", 70),
    ".pgp":   ("pgp", 70),
    ".asc":   ("pgp-armored", 55),
    ".luks":  ("luks", 60),
    ".axx":   ("axcrypt", 65),
    ".aes":   ("aes-archive", 50),
}


def shannon_entropy(data: bytes) -> float:
    """Shannon entropy in bits/byte (0-8). High values (>~7.5) indicate
    compressed or encrypted content."""
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    ent = 0.0
    for c in counts:
        if c:
            p = c / n
            ent -= p * math.log2(p)
    return ent


def classify_header(header: bytes) -> Optional[Detection]:
    """Identify an encrypted container/volume from its leading bytes by magic
    signature. Returns None when no reliable signature matches."""
    if len(header) < 8:
        return None

    # LUKS1 and LUKS2 both start with the same magic.
    if header[:6] == b"LUKS\xba\xbe":
        ver = struct.unpack(">H", header[6:8])[0] if len(header) >= 8 else 0
        return Detection("luks", 98, f"LUKS volume header (version {ver})")

    # BitLocker / BitLocker To Go: "-FVE-FS-" at offset 3.
    if len(header) >= 11 and header[3:11] == b"-FVE-FS-":
        return Detection("bitlocker", 97, "BitLocker FVE volume signature")

    # KeePass KDBX (1.x/2.x/3.x/4.x) and the older KDB all share this base sig.
    if header[:4] == b"\x03\xd9\xa2\x9a":
        return Detection("kdbx", 96, "KeePass database signature")

    # PGP/GPG armored text.
    if header[:34] == b"-----BEGIN PGP PRIVATE KEY BLOCK--":
        return Detection("pgp-privkey", 97, "ASCII-armored PGP private key block")
    if header[:27] == b"-----BEGIN PGP MESSAGE-----":
        return Detection("pgp-message", 88, "ASCII-armored PGP message")

    # 7-Zip archive (may or may not be encrypted; archive is itself a lead).
    if header[:6] == b"7z\xbc\xaf\x27\x1c":
        return Detection("7z-archive", 50, "7-Zip archive (check for encryption)")

    # RAR4 / RAR5 archive.
    if header[:7] == b"Rar!\x1a\x07\x00" or header[:8] == b"Rar!\x1a\x07\x01\x00":
        return Detection("rar-archive", 50, "RAR archive (check for encryption)")

    # ZIP: encrypted when general-purpose bit 0 is set in the local file header.
    if header[:4] == b"PK\x03\x04" and len(header) >= 8:
        gp_flag = struct.unpack("<H", header[6:8])[0]
        if gp_flag & 0x0001:
            return Detection("zip-encrypted", 80, "ZIP with encryption flag set")
        return None  # plain zip is not interesting on its own

    return None


def classify(name: str, header: bytes, size: int = 0) -> Optional[Detection]:
    """Best-effort classification of a candidate file: signature first, then an
    extension+entropy fallback for header-less containers (VeraCrypt et al.).

    name: filename (used only for the extension hint).
    header: leading bytes of the file (>= 512 recommended for entropy).
    size: total file size in bytes (used to gate the entropy heuristic).
    """
    sig = classify_header(header)
    if sig is not None:
        # An agreeing extension nudges confidence up slightly.
        kind_l = sig.kind.lower()
        for ext, (hint_kind, _) in EXTENSION_HINTS.items():
            if name.lower().endswith(ext) and hint_kind in kind_l:
                return Detection(sig.kind, min(99, sig.confidence + 1), sig.detail)
        return sig

    lname = name.lower()
    for ext, (hint_kind, base_conf) in EXTENSION_HINTS.items():
        if lname.endswith(ext):
            # Header-less containers must also look like random/encrypted data
            # and be non-trivial in size, or they're just a suggestive name.
            high_entropy = len(header) >= 256 and shannon_entropy(header) >= 7.4
            big_enough = size >= 64 * 1024
            conf = base_conf
            detail = f"extension {ext} suggests {hint_kind}"
            if high_entropy and big_enough:
                conf = min(90, base_conf + 25)
                detail += "; high-entropy payload"
            elif not high_entropy and len(header) >= 256:
                # Named like a container but low entropy → likely a false lead.
                conf = max(20, base_conf - 20)
                detail += "; low entropy (likely not encrypted)"
            return Detection(hint_kind, conf, detail)
    return None


# ── Partition-table parsing (pure: operates on bytes already read from the
#    image) so volume-level LUKS/BitLocker can be checked per partition without
#    mounting or external tools. ───────────────────────────────────────────────

@dataclass(frozen=True)
class Partition:
    index: int
    start_offset: int   # byte offset into the image
    size: int           # bytes (0 if unknown)
    type_hint: str      # 'mbr:0x83', 'gpt', etc.


def parse_mbr_partitions(first_sector: bytes, sector_size: int = 512) -> list[Partition]:
    """Parse the four MBR primary partition entries from the first sector.
    Returns [] if the boot signature is absent. A single 0xEE entry signals a
    protective MBR (GPT) — caller should fall back to parse_gpt_partitions."""
    if len(first_sector) < 512 or first_sector[510:512] != b"\x55\xaa":
        return []
    parts: list[Partition] = []
    for i in range(4):
        entry = first_sector[446 + i * 16: 446 + (i + 1) * 16]
        ptype = entry[4]
        start_lba, num_sectors = struct.unpack("<II", entry[8:16])
        if ptype == 0x00 or start_lba == 0:
            continue
        parts.append(Partition(
            index=i + 1,
            start_offset=start_lba * sector_size,
            size=num_sectors * sector_size,
            type_hint=f"mbr:0x{ptype:02x}",
        ))
    return parts


def is_protective_mbr(first_sector: bytes) -> bool:
    """True if the MBR is a GPT protective MBR (one 0xEE partition)."""
    if len(first_sector) < 512 or first_sector[510:512] != b"\x55\xaa":
        return False
    types = [first_sector[446 + i * 16 + 4] for i in range(4)]
    return types.count(0xEE) >= 1 and all(t in (0x00, 0xEE) for t in types)


def parse_gpt_partitions(gpt_header: bytes, entries: bytes,
                         sector_size: int = 512) -> list[Partition]:
    """Parse GPT partition entries. gpt_header is LBA1 (the GPT header);
    entries is the raw partition-entry array it points at."""
    if gpt_header[:8] != b"EFI PART":
        return []
    num_entries = struct.unpack("<I", gpt_header[80:84])[0]
    entry_size = struct.unpack("<I", gpt_header[84:88])[0]
    if entry_size < 128:
        return []
    zero_guid = b"\x00" * 16
    parts: list[Partition] = []
    for i in range(min(num_entries, len(entries) // entry_size)):
        e = entries[i * entry_size:(i + 1) * entry_size]
        if len(e) < 128 or e[0:16] == zero_guid:
            continue
        first_lba, last_lba = struct.unpack("<QQ", e[32:48])
        if first_lba == 0:
            continue
        parts.append(Partition(
            index=i + 1,
            start_offset=first_lba * sector_size,
            size=max(0, (last_lba - first_lba + 1)) * sector_size,
            type_hint="gpt",
        ))
    return parts
