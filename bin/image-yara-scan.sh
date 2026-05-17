#!/usr/bin/env bash
# Run YARA rules over the recovered corpus. Detects wallet file formats,
# extended keys, WIF private keys, and armored key material.
# Results go to the findings table (category=wallet) and are cross-posted
# to wallet_candidates for high-scoring matches.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-yara-scan.sh <db-path> [--rules-dir <dir>]

Scans the recovered corpus directory with all YARA rule files found in
--rules-dir (default: /root/hdd-recovery/config/yara/).

Results are written to:
  findings table  (source_tool=yara, category=wallet)
  wallet_candidates (score ≥ 65)
  hits/yara/<timestamp>/hits.tsv

Requires: yara
Homepage: https://virustotal.github.io/yara/
EOF
}

db="${1:-}"
rules_dir="$ROOT_DIR/config/yara"
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rules-dir) rules_dir="$2"; shift 2 ;;
    -h|--help)   usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]]        || { usage; exit 1; }
[[ -f "$db" ]]        || die "database not found: $db"
[[ -d "$rules_dir" ]] || die "rules directory not found: $rules_dir"
need_cmd yara
need_cmd python3

export_root="$(db_image_export_root "$db")"
corpus_dir="$export_root/recovered"
[[ -d "$corpus_dir" ]] || die "recovered corpus not found: $corpus_dir (run a carving stage first)"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="$export_root/hits/yara/$timestamp"
log_path="$export_root/logs/yara-scan-$timestamp.log"
hits_tsv="$out_dir/hits.tsv"
mkdir -p "$out_dir"

run_id="$(record_scan_start "$db" "yara-scan" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

{
  # Collect all .yar rule files
  mapfile -t rule_files < <(find "$rules_dir" -name '*.yar' -o -name '*.yara' 2>/dev/null | sort)
  [[ ${#rule_files[@]} -gt 0 ]] || die "no .yar files found in $rules_dir"
  printf 'Rules: %s\n' "${rule_files[*]}"
  printf 'Corpus: %s\n' "$corpus_dir"

  # Build combined rules temp file to run once
  tmpfile="$(mktemp /tmp/yara-combined-XXXXXX.yar)"
  trap 'rm -f "$tmpfile"' EXIT
  cat "${rule_files[@]}" > "$tmpfile"

  # Run YARA; output format: rule_name  file_path
  printf 'Running YARA...\n'
  yara --no-warnings -r "$tmpfile" "$corpus_dir" 2>&1 | tee "$out_dir/raw_output.txt" || true
  printf 'YARA scan complete.\n'

python3 - "$db" "$out_dir/raw_output.txt" "$hits_tsv" "$run_id" <<'PY'
import csv, os, re, sqlite3, sys
from datetime import datetime, timezone

db_path, raw_out, hits_tsv, run_id = sys.argv[1:5]
conn = sqlite3.connect(db_path)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# YARA output line: rule_name\tfile_path  OR  rule_name  file_path
SCORE_MAP = {
    'ethereum_keystore':         85,
    'electrum_wallet_json':      80,
    'metamask_encrypted_vault':  85,
    'bitcoin_core_wallet_dat':   75,
    'exodus_wallet_passphrase_hint': 70,
    'trust_wallet_keystore':     65,
    'bip32_extended_key':        92,
    'wif_private_key':           88,
    'armored_private_key':       60,
    'mnemonic_wordlist_file':    55,
}

hits = []
try:
    with open(raw_out, 'r', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('[') or 'error' in line.lower():
                continue
            # YARA line format: "rule_name file_path"
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            rule_name, file_path = parts[0], parts[1]
            score = SCORE_MAP.get(rule_name, 50)
            hits.append((rule_name, file_path, score))
except FileNotFoundError:
    print("Raw YARA output file not found — no hits to import.")
    sys.exit(0)

print(f"Importing {len(hits)} YARA hits...")

# Resolve artifact_id
def artifact_id_for(path):
    row = conn.execute(
        "SELECT id FROM recovered_artifacts WHERE full_path = ? LIMIT 1", (path,)
    ).fetchone()
    return row[0] if row else None

with open(hits_tsv, 'w', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['score', 'rule', 'file_path'])
    for rule_name, file_path, score in sorted(hits, key=lambda x: -x[2]):
        w.writerow([score, rule_name, file_path])

        art_id = artifact_id_for(file_path)

        conn.execute("""
            INSERT OR IGNORE INTO findings
                (source_tool, category, artifact_id, path, key, value, score, created_at)
            VALUES ('yara', 'wallet', ?, ?, 'rule_match', ?, ?, ?)
        """, (art_id, file_path, rule_name, score, now))

        # Cross-post to wallet_candidates for high-score hits
        if score >= 65:
            # Find the file in the TSK files table by path
            file_id = None
            name = os.path.basename(file_path)
            row = conn.execute(
                "SELECT id FROM files WHERE name = ? LIMIT 1", (name,)
            ).fetchone()
            if row:
                file_id = row[0]

            if file_id:
                conn.execute("""
                    INSERT OR IGNORE INTO wallet_candidates
                        (file_id, source_stage, score, reason, details, created_at)
                    VALUES (?, 'yara', ?, ?, ?, ?)
                """, (file_id, score, f'yara:{rule_name}',
                      f'YARA rule {rule_name} matched {file_path}', now))

conn.commit()
conn.close()
print(f"Done. {len(hits)} hits written to findings + wallet_candidates.")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="YARA scan failed or completed with errors"
}

hit_count="$(grep -c '.' "$hits_tsv" 2>/dev/null || echo 0)"
notes="$(( hit_count - 1 )) hits; rules_dir=$rules_dir"
record_scan_end "$db" "$run_id" "$status" "$notes"
