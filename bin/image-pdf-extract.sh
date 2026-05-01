#!/usr/bin/env bash
# Extract text from recovered PDF files using pdftotext and scan for
# BIP39 seed phrases. Wallet software (Electrum, MetaMask, hardware wallets)
# commonly exports recovery instructions as PDFs.
# High-confidence hits go to wallet_candidates and notes.
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-pdf-extract.sh <db-path> [--min-words <n>] [--wordlist <path>]

Scans recovered PDF artifacts from recovered_artifacts:
  1. Extracts text with pdftotext
  2. Scans for runs of consecutive BIP39 words (≥ min-words, default 6)
  3. Writes hits to findings table (category=seed_phrase)
  4. High-confidence hits (≥ 12 consecutive words) go to wallet_candidates
     and are appended to the notes table

Requires: pdftotext (poppler-utils)
Homepage: https://poppler.freedesktop.org
EOF
}

db="${1:-}"
min_words=6
wordlist_path=""
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --min-words)  min_words="$2";    shift 2 ;;
    --wordlist)   wordlist_path="$2"; shift 2 ;;
    -h|--help)    usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd pdftotext
need_cmd python3

export_root="$(db_image_export_root "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="$export_root/hits/pdf-seeds/$timestamp"
log_path="$export_root/logs/pdf-extract-$timestamp.log"
mkdir -p "$out_dir"

run_id="$(record_scan_start "$db" "pdf-extract" "$0 $db" "$log_path" "$out_dir")"
status="ok"

{
python3 - "$db" "$out_dir" "$min_words" "${wordlist_path:-}" <<'PY'
import csv, os, re, sqlite3, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

db_path, out_dir, min_words_str, wordlist_arg = sys.argv[1:5]
min_words = int(min_words_str)

conn = sqlite3.connect(db_path)
now  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Load BIP39 wordlist ──────────────────────────────────────────────────────
BIP39_PATHS = [
    wordlist_arg,
    "/usr/local/share/bip39-english.txt",
    "/root/hdd-recovery/config/bip39-english.txt",
]
bip39: set[str] = set()
for p in BIP39_PATHS:
    if p and os.path.isfile(p):
        words = {l.strip().lower() for l in open(p) if l.strip()}
        if len(words) >= 2000:
            bip39 = words
            print(f"BIP39 wordlist loaded: {p}  ({len(bip39)} words)")
            break

if not bip39:
    print("WARNING: BIP39 wordlist not found — seed detection disabled")

def best_bip39_run(text):
    tokens = re.sub(r"[^a-z\s]", " ", text.lower()).split()
    best, cur = [], []
    for t in tokens:
        if t in bip39:
            cur.append(t)
            if len(cur) > len(best):
                best = list(cur)
        else:
            cur = []
    return len(best), best

def pdftotext(path):
    try:
        r = subprocess.run(
            ['pdftotext', '-enc', 'UTF-8', path, '-'],
            capture_output=True, text=True, timeout=60,
        )
        return r.stdout
    except Exception:
        return ''

# ── Find PDF artifacts ───────────────────────────────────────────────────────
rows = conn.execute("""
    SELECT ra.id, ra.full_path, ra.relative_path
    FROM   recovered_artifacts ra
    WHERE  lower(ra.mime_type) = 'application/pdf'
       OR  lower(substr(ra.relative_path, -4)) = '.pdf'
    ORDER  BY ra.size_bytes ASC NULLS LAST
""").fetchall()

print(f"Found {len(rows)} PDF artifacts to scan.")

hits_path = os.path.join(out_dir, 'hits.tsv')
hits = []

with open(hits_path, 'w', newline='') as f:
    writer = csv.writer(f, delimiter='\t')
    writer.writerow(['score', 'run_len', 'artifact_id', 'path', 'sequence'])

    for art_id, full_path, rel_path in rows:
        if not full_path or not os.path.isfile(full_path):
            continue

        text = pdftotext(full_path)
        if not text.strip():
            continue

        if bip39:
            run_len, run_words = best_bip39_run(text)
            if run_len >= min_words:
                score = min(100, run_len * 4)
                seq   = ' '.join(run_words)
                hits.append((score, run_len, art_id, full_path, seq))
                writer.writerow([score, run_len, art_id, full_path, seq])
                print(f"  HIT score={score} run={run_len}: {full_path}")
                print(f"       {seq}")

        # Always record the PDF as a text-extracted finding (informational)
        preview = text[:200].replace('\n', ' ')
        conn.execute("""
            INSERT OR IGNORE INTO findings
                (source_tool, category, artifact_id, path, key, value, score, created_at)
            VALUES ('pdf-extract', 'metadata', ?, ?, 'text_preview', ?, 10, ?)
        """, (art_id, full_path, preview, now))

# Write seed phrase findings and cross-post high-confidence hits
for score, run_len, art_id, full_path, seq in hits:
    conn.execute("""
        INSERT OR IGNORE INTO findings
            (source_tool, category, artifact_id, path, key, value, score, notes, created_at)
        VALUES ('pdf-extract', 'seed_phrase', ?, ?, 'bip39_run', ?, ?, ?, ?)
    """, (art_id, full_path, seq, score,
          f"run_len={run_len} min_words={min_words}", now))

    if run_len >= 12:
        # Write to notes table
        conn.execute(
            "INSERT INTO notes(created_at, note) VALUES(?,?)",
            (now, f"pdf-extract: probable seed phrase (run={run_len}) in {full_path}: {seq}"),
        )
        # Cross-post to wallet_candidates (match by artifact basename)
        name = os.path.basename(full_path)
        row = conn.execute(
            "SELECT id FROM files WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        if row:
            conn.execute("""
                INSERT OR IGNORE INTO wallet_candidates
                    (file_id, source_stage, score, reason, details, created_at)
                VALUES (?, 'pdf-extract', ?, 'pdf-bip39-seed', ?, ?)
            """, (row[0], score, f"seed phrase (run={run_len}): {seq[:120]}", now))

conn.commit()
conn.close()
print(f"\nDone. PDFs scanned: {len(rows)}  Seed hits: {len(hits)}")
print(f"Results: {hits_path}")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
}

record_scan_end "$db" "$run_id" "$status"
