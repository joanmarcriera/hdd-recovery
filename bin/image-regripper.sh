#!/usr/bin/env bash
# Run RegRipper on Windows registry hive files found in the recovered corpus.
# Extracts MRU file lists, installed software, USB device history,
# user account names, and program execution history.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-regripper.sh <db-path>

Searches recovered_artifacts for Windows registry hive files:
  NTUSER.DAT  SOFTWARE  SYSTEM  SAM  SECURITY  DEFAULT  UsrClass.dat

For each hive found, runs:  regripper -r <hive> -f <profile>
Output is written to:       exports/structure/registry/
Findings are written to the findings table (category=registry).

Requires: regripper
Homepage: https://github.com/keydet89/RegRipper3.0
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd regripper
need_cmd python3

export_root="$(db_image_export_root "$db")"
out_dir="$export_root/structure/registry"
log_path="$export_root/logs/regripper.log"
mkdir -p "$out_dir"

run_id="$(record_scan_start "$db" "regripper" "$0 $db" "$log_path" "$out_dir")"
status="ok"

{
python3 - "$db" "$out_dir" <<'PY'
import csv, os, re, sqlite3, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

db_path, out_dir = sys.argv[1:3]
conn = sqlite3.connect(db_path)
now  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# Registry hive filenames → RegRipper profile
# Profile 'all' runs every plugin available for that hive type
HIVE_PROFILES = {
    'ntuser.dat':    'ntuser',
    'software':      'software',
    'system':        'system',
    'sam':           'sam',
    'security':      'security',
    'default':       'ntuser',    # DEFAULT hive uses same plugins as NTUSER
    'usrclass.dat':  'usrclass',
}

# Interest keywords for findings extraction
INTEREST_RE = re.compile(
    r'(?i)(bitcoin|btc|electrum|wallet|crypto|ledger|trezor|coinbase|'
    r'binance|kraken|blockchain|metamask|exodus|armory|private.?key|seed|'
    r'mnemonic|recovery|backup|passphrase)',
    re.IGNORECASE
)

def run_regripper(hive_path, profile, out_file):
    try:
        result = subprocess.run(
            ['regripper', '-r', hive_path, '-f', profile],
            capture_output=True, text=True, timeout=120,
        )
        with open(out_file, 'w') as f:
            f.write(result.stdout)
            if result.stderr:
                f.write('\n-- STDERR --\n')
                f.write(result.stderr)
        return result.stdout
    except subprocess.TimeoutExpired:
        return ''
    except Exception as e:
        print(f"  regripper error: {e}")
        return ''

# Find registry hive files in recovered_artifacts
rows = conn.execute("""
    SELECT ra.id, ra.full_path, ra.relative_path
    FROM   recovered_artifacts ra
    WHERE  lower(ra.file_output) LIKE '%registry%'
       OR  lower(replace(ra.relative_path,'\\','/')) LIKE '%/ntuser.dat'
       OR  lower(ra.relative_path)  LIKE '%/ntuser.dat'
       OR  lower(ra.relative_path)  = 'ntuser.dat'
       OR  lower(ra.relative_path)  LIKE '%software'
       OR  lower(ra.relative_path)  LIKE '%/system'
       OR  lower(ra.relative_path)  LIKE '%/sam'
       OR  lower(ra.relative_path)  LIKE '%/security'
       OR  lower(ra.relative_path)  LIKE '%usrclass.dat'
""").fetchall()

# Also do a direct scan of the recovered directory for registry hive names
recovered_root = str(Path(out_dir).parent.parent / 'recovered')
direct_hits = []
if os.path.isdir(recovered_root):
    for root, dirs, files in os.walk(recovered_root):
        for fname in files:
            if fname.lower() in HIVE_PROFILES:
                full = os.path.join(root, fname)
                # Avoid duplicates with DB rows
                if not any(r[1] == full for r in rows):
                    direct_hits.append((None, full, fname))

all_hives = list(rows) + direct_hits
print(f"Found {len(all_hives)} registry hive candidate(s).")

findings = []
summary_rows = []

for art_id, full_path, rel_path in all_hives:
    if not full_path or not os.path.isfile(full_path):
        continue

    hive_name = os.path.basename(full_path).lower()
    profile   = HIVE_PROFILES.get(hive_name)
    if not profile:
        # Try to detect via file magic or skip
        print(f"  Skipping unknown hive type: {full_path}")
        continue

    safe_name = re.sub(r'[^\w\-.]', '_', full_path.replace('/', '__'))[-80:]
    out_file  = os.path.join(out_dir, f'{safe_name}.rip.txt')
    print(f"  regripper -r {full_path} -f {profile} → {out_file}")

    output = run_regripper(full_path, profile, out_file)

    # Extract interesting lines
    interesting = [ln.strip() for ln in output.splitlines()
                   if INTEREST_RE.search(ln)]

    status_str = 'ok' if output.strip() else 'empty'
    summary_rows.append([hive_name, full_path, profile, status_str, len(output.splitlines()), len(interesting)])

    if interesting:
        interest_file = out_file.replace('.rip.txt', '.interesting.txt')
        with open(interest_file, 'w') as f:
            f.write('\n'.join(interesting))
        print(f"    → {len(interesting)} interesting lines")

    # Write finding per interesting line
    for line in interesting[:500]:  # cap
        findings.append((art_id, full_path, hive_name, line[:512]))

# Write summary
with open(os.path.join(out_dir, 'summary.tsv'), 'w', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['hive', 'path', 'profile', 'status', 'total_lines', 'interesting'])
    w.writerows(summary_rows)

# Bulk-insert findings
for art_id, path, hive, line in findings:
    conn.execute("""
        INSERT OR IGNORE INTO findings
            (source_tool, category, artifact_id, path, key, value, score, created_at)
        VALUES ('regripper', 'registry', ?, ?, ?, ?, 60, ?)
    """, (art_id, path, hive, line, now))

conn.commit()
conn.close()

print(f"\nRegRipper done. Hives processed: {len(summary_rows)}  "
      f"Interesting lines: {len(findings)}")
print(f"Output: {out_dir}")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
}

record_scan_end "$db" "$run_id" "$status"
