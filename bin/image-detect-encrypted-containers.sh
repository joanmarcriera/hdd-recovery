#!/usr/bin/env bash
# Detect encrypted containers and volumes — VeraCrypt/TrueCrypt, LUKS,
# BitLocker, KeePass databases, PGP/GPG material, and encrypted archives.
# These are common backup strategies for crypto holders and are otherwise
# invisible to the keyword/picture detectors. Results are leads for human
# review, registered in the findings table (category=encrypted-container).
#
# Two scopes, both non-destructive and read-only against the image:
#   volume-level  parse the image partition table and check each partition (and
#                 the whole-disk header) for LUKS / BitLocker signatures.
#   file-level    classify recovered-corpus files and flag inventory entries
#                 (files table) whose extension suggests a container.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-detect-encrypted-containers.sh <db-path> [--max-files N] [--no-corpus]

Detects encrypted containers/volumes and registers them in the findings table
(source_tool=encrypted-detect, category=encrypted-container). Signature matches
(LUKS, BitLocker, KeePass, PGP, encrypted ZIP) are high-confidence; extension
and entropy heuristics (VeraCrypt/TrueCrypt have no magic by design) are
deliberately low-confidence leads.

Options:
  --max-files N   cap recovered-corpus files scanned (default 200000)
  --no-corpus     skip the recovered-corpus scan (volume + inventory only)

Query results:
  image-query.sh <db> findings encrypted-detect
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
case "$db" in -h|--help) usage; exit 0 ;; esac
[[ -f "$db" ]] || die "database not found: $db"
shift

max_files=200000
scan_corpus=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-files) max_files="$2"; shift 2 ;;
    --no-corpus) scan_corpus=0; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

need_cmd python3

export_root="$(db_image_export_root "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_path="$export_root/logs/detect-encrypted-$timestamp.log"
out_dir="$export_root/hits/encrypted"
mkdir -p "$out_dir" "$(dirname "$log_path")"
hits_tsv="$out_dir/hits-$timestamp.tsv"

run_id="$(record_scan_start "$db" "detect-encrypted" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

{
  PYTHONPATH="$ROOT_DIR" python3 - "$db" "$run_id" "$hits_tsv" "$max_files" "$scan_corpus" <<'PY'
import csv, os, sqlite3, sys
from datetime import datetime, timezone

sys.path.insert(0, os.environ["PYTHONPATH"])
from lib.encrypted import (  # noqa: E402
    classify, classify_header, EXTENSION_HINTS,
    parse_mbr_partitions, parse_gpt_partitions, is_protective_mbr,
)

db_path, run_id, hits_tsv, max_files_s, scan_corpus_s = sys.argv[1:6]
max_files = int(max_files_s)
scan_corpus = scan_corpus_s == "1"
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

conn = sqlite3.connect(db_path)
row = conn.execute("SELECT image_path, export_root FROM image_info WHERE id=1").fetchone()
if not row:
    print("image_info row missing — run image-analysis-init.sh first")
    sys.exit(2)
image_path, export_root = row[0] or "", row[1] or ""

# Clear prior results for an idempotent re-run (additive evidence rule applies to
# files on disk; this row set is fully reproducible from the same inputs).
conn.execute("DELETE FROM findings WHERE source_tool='encrypted-detect'")

seen: set[tuple] = set()
rows_out: list[tuple] = []


def record(kind, confidence, detail, path, file_id=None, artifact_id=None):
    dedup_key = (path, kind)
    if dedup_key in seen:
        return
    seen.add(dedup_key)
    conn.execute(
        "INSERT INTO findings (source_tool, category, file_id, artifact_id, "
        "path, key, value, score, notes, created_at) VALUES "
        "('encrypted-detect','encrypted-container',?,?,?,?,?,?,?,?)",
        (file_id, artifact_id, path, kind, detail, confidence, None, now),
    )
    rows_out.append((confidence, kind, path, detail))


# ── Volume-level: partition-table walk + per-partition / whole-disk headers ───
def read_at(fh, offset, n):
    fh.seek(offset)
    return fh.read(n)


vol_count = 0
try:
    if image_path and os.path.exists(image_path):
        size = os.path.getsize(image_path)
        with open(image_path, "rb") as fh:
            first = read_at(fh, 0, 512)
            # Whole-disk encryption (no partition table).
            d = classify_header(read_at(fh, 0, 512))
            if d:
                record(d.kind, d.confidence, f"whole-disk: {d.detail}",
                       f"{image_path}@0")
                vol_count += 1
            parts = parse_mbr_partitions(first)
            if not parts and is_protective_mbr(first):
                gpt_hdr = read_at(fh, 512, 512)
                # entry array LBA + count from the GPT header
                import struct
                ent_lba = struct.unpack("<Q", gpt_hdr[72:80])[0] if len(gpt_hdr) >= 80 else 2
                ent_cnt = struct.unpack("<I", gpt_hdr[80:84])[0] if len(gpt_hdr) >= 84 else 128
                ent_sz = struct.unpack("<I", gpt_hdr[84:88])[0] if len(gpt_hdr) >= 88 else 128
                entries = read_at(fh, ent_lba * 512, min(ent_cnt, 256) * ent_sz)
                parts = parse_gpt_partitions(gpt_hdr, entries)
            for p in parts:
                if p.start_offset + 512 > size:
                    continue
                hdr = read_at(fh, p.start_offset, 512)
                d = classify_header(hdr)
                if d and d.kind in ("luks", "bitlocker"):
                    record(d.kind, d.confidence,
                           f"partition {p.index} ({p.type_hint}): {d.detail}",
                           f"{image_path}@{p.start_offset}")
                    vol_count += 1
        print(f"Volume scan: {len(parts)} partition(s), {vol_count} encrypted volume(s)")
    else:
        print(f"Volume scan: image not present ({image_path}) — skipped")
except Exception as e:  # never let a volume read kill the stage
    print(f"Volume scan error (continuing): {e}")


# ── Inventory leads: files table entries whose extension suggests a container ─
inv_count = 0
exts = tuple(EXTENSION_HINTS.keys())
try:
    cur = conn.execute(
        "SELECT id, COALESCE(path, name), COALESCE(name,''), "
        "COALESCE(size_bytes,0), COALESCE(extension,'') FROM files WHERE is_dir=0"
    )
    for fid, fpath, fname, fsize, fext in cur:
        ext = (fext if fext.startswith(".") else "." + fext).lower() if fext else ""
        name = fname or os.path.basename(fpath or "")
        if not (ext in EXTENSION_HINTS or name.lower().endswith(exts)):
            continue
        d = classify(name, b"", size=fsize)   # no content for inventory rows
        if d:
            record(d.kind, max(20, d.confidence - 15),
                   f"inventory lead: {d.detail}", fpath, file_id=fid)
            inv_count += 1
    print(f"Inventory scan: {inv_count} extension lead(s)")
except sqlite3.OperationalError as e:
    print(f"Inventory scan skipped: {e}")


# ── File-level: classify recovered-corpus files by header + entropy ───────────
corpus_count = 0
if scan_corpus and export_root:
    corpus = os.path.join(export_root, "recovered")
    if os.path.isdir(corpus):
        scanned = 0
        art_by_path = {}
        try:
            for aid, fp in conn.execute(
                    "SELECT id, full_path FROM recovered_artifacts"):
                art_by_path[fp] = aid
        except sqlite3.OperationalError:
            pass
        for root, _dirs, names in os.walk(corpus):
            for nm in names:
                if scanned >= max_files:
                    break
                fpath = os.path.join(root, nm)
                try:
                    fsize = os.path.getsize(fpath)
                    if fsize < 256:
                        continue
                    with open(fpath, "rb") as fh:
                        header = fh.read(2048)
                except OSError:
                    continue
                scanned += 1
                d = classify(nm, header, size=fsize)
                if d and d.confidence >= 45:
                    record(d.kind, d.confidence, d.detail, fpath,
                           artifact_id=art_by_path.get(fpath))
                    corpus_count += 1
            if scanned >= max_files:
                break
        print(f"Corpus scan: {scanned} file(s) read, {corpus_count} container(s)")
    else:
        print(f"Corpus scan: {corpus} not present — skipped")
else:
    print("Corpus scan: disabled")

conn.commit()

with open(hits_tsv, "w", newline="") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["score", "kind", "path", "detail"])
    for score, kind, path, detail in sorted(rows_out, key=lambda r: -r[0]):
        w.writerow([score, kind, path, detail])

total = len(rows_out)
high = sum(1 for r in rows_out if r[0] >= 80)
print(f"Total encrypted-container findings: {total} ({high} high-confidence)")
conn.close()
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="detection failed or completed with errors"
}

hit_count="$( [[ -f "$hits_tsv" ]] && { wc -l < "$hits_tsv" | tr -d ' '; } || echo 1 )"
notes="${notes:-$(( hit_count - 1 )) findings; tsv=$hits_tsv}"
record_scan_end "$db" "$run_id" "$status" "$notes"
