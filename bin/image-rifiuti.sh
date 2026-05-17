#!/usr/bin/env bash
# Parse Windows Recycle Bin metadata using rifiuti2.
# Recovers original file paths and deletion timestamps for deleted files —
# useful for finding deleted wallet files, documents, or exchange history.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-rifiuti.sh <db-path>

Searches recovered_artifacts for Windows Recycle Bin files:
  INFO2      — Windows 98/Me/2000/XP recycle bin index
  $I??????   — Windows Vista+ per-deleted-file metadata
  $RECYCLE.BIN directories

For each found, runs rifiuti2 and outputs original paths + deletion times.
Results go to exports/structure/recycle-bin/ and the findings table
(category=recycle_bin).

Requires: rifiuti2
Homepage: https://abelcheung.github.io/rifiuti2/
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd rifiuti2
need_cmd python3

export_root="$(db_image_export_root "$db")"
out_dir="$export_root/structure/recycle-bin"
log_path="$export_root/logs/rifiuti.log"
mkdir -p "$out_dir"

run_id="$(record_scan_start "$db" "rifiuti2" "$0 $db" "$log_path" "$out_dir")"
status="ok"

{
python3 - "$db" "$out_dir" <<'PY'
import csv, os, re, sqlite3, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

db_path, out_dir = sys.argv[1:3]
conn = sqlite3.connect(db_path)
now  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

INTEREST_RE = re.compile(
    r'(?i)(bitcoin|btc|wallet|electrum|ledger|trezor|coinbase|crypto|'
    r'seed|mnemonic|private|key|bank|tax|passport|backup|pdf)',
)

def run_rifiuti2(args):
    try:
        r = subprocess.run(
            ['rifiuti2'] + args,
            capture_output=True, text=True, timeout=60,
        )
        return r.stdout
    except Exception as e:
        print(f"  rifiuti2 error: {e}")
        return ''

# Search recovered_artifacts for Recycle Bin files
rows = conn.execute("""
    SELECT ra.id, ra.full_path, ra.relative_path
    FROM   recovered_artifacts ra
    WHERE  lower(ra.file_output) LIKE '%recycle%'
       OR  lower(ra.relative_path) LIKE '%$recycle.bin%'
       OR  lower(ra.relative_path) LIKE '%recycle.bin%'
       OR  lower(ra.relative_path) LIKE '%/info2'
       OR  lower(ra.relative_path) = 'info2'
       OR  (lower(ra.relative_path) LIKE '%/$i%'
            AND length(ra.relative_path) - length(replace(lower(ra.relative_path),'$i','')) > 0)
""").fetchall()

# Direct filesystem walk for $I files and INFO2
recovered_root = str(Path(out_dir).parent.parent / 'recovered')
direct = []
if os.path.isdir(recovered_root):
    for root, dirs, files in os.walk(recovered_root):
        for fname in files:
            if fname.lower() == 'info2' or fname.lower().startswith('$i'):
                fp = os.path.join(root, fname)
                if not any(r[1] == fp for r in rows):
                    direct.append((None, fp, fname))

all_items = list(rows) + direct
print(f"Found {len(all_items)} Recycle Bin candidate(s).")

findings  = []
all_rows  = []

for art_id, full_path, rel_path in all_items:
    if not full_path or not os.path.isfile(full_path):
        continue

    fname = os.path.basename(full_path).lower()
    print(f"  Processing: {full_path}")

    # INFO2 = legacy; $I* = modern per-file metadata
    if fname == 'info2':
        args = [full_path]
    elif fname.startswith('$i'):
        args = [full_path]
    else:
        continue

    output = run_rifiuti2(args)
    if not output.strip():
        print(f"    (no output)")
        continue

    out_file = os.path.join(out_dir, re.sub(r'[^\w.]', '_', fname) + '_' +
                            str(os.getpid()) + '.tsv')
    with open(out_file, 'w') as f:
        f.write(output)

    # Parse lines (rifiuti2 TSV output: index, deleted_time, path, size)
    for line in output.splitlines():
        if line.startswith('#') or not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) < 3:
            continue
        index, del_time = parts[0], parts[1]
        orig_path = parts[2] if len(parts) > 2 else ''
        size      = parts[3] if len(parts) > 3 else ''

        score = 70 if INTEREST_RE.search(orig_path) else 20
        all_rows.append([index, del_time, orig_path, size, full_path])
        findings.append((art_id, full_path, 'recycle_bin_entry',
                         f"{del_time} | {orig_path}", score))

# Write combined summary
with open(os.path.join(out_dir, 'all_deleted_files.tsv'), 'w', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['index', 'deleted_time', 'original_path', 'size', 'info_source'])
    w.writerows(all_rows)

# Bulk-insert findings
for art_id, path, key, value, score in findings:
    conn.execute("""
        INSERT OR IGNORE INTO findings
            (source_tool, category, artifact_id, path, key, value, score, created_at)
        VALUES ('rifiuti2', 'recycle_bin', ?, ?, ?, ?, ?, ?)
    """, (art_id, path, key, value, score, now))

conn.commit()
conn.close()

interesting = [f for f in findings if f[4] >= 70]
print(f"\nrifiuti2 done. Entries recovered: {len(all_rows)}  "
      f"High-interest: {len(interesting)}")
print(f"Output: {out_dir}")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
}

record_scan_end "$db" "$run_id" "$status"
