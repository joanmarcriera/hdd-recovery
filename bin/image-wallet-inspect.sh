#!/usr/bin/env bash
# Inspect Bitcoin Core wallet.dat candidates with pywallet and record extracted
# keys plus an inspection finding. Raw image scanning is optional and uses a
# read-only loop device because pywallet recovery mode expects a block device.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-wallet-inspect.sh <db-path> [--scan-raw] [--pywallet <cmd>] [--run]

Default is dry-run. Pass --run to execute pywallet and write findings.

Prerequisites:
  pywallet from https://github.com/Great-Software-Company/pywallet
  icat for exporting filesystem-indexed wallet.dat candidates
  losetup when --scan-raw is used

Outputs:
  <export_root>/logs/wallet-inspect-<timestamp>.log
  <export_root>/hits/wallet-inspect/<timestamp>/<wallet-id>/dump.txt
  <export_root>/hits/wallet-inspect/<timestamp>/summary.md

Encrypted wallets are recorded in wallet_keys with encrypted=1 and should be
handled next with image-crack-wallet.sh.
EOF
}

db="${1:-}"
scan_raw=0
run=0
pywallet_cmd="${PYWALLET_CMD:-pywallet}"
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scan-raw) scan_raw=1; shift ;;
    --pywallet) pywallet_cmd="$2"; shift 2 ;;
    --run) run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd python3
need_cmd sqlite3

export_root="$(db_image_export_root "$db")"
image_path="$(db_image_path "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="$export_root/hits/wallet-inspect/$timestamp"
log_path="$export_root/logs/wallet-inspect-$timestamp.log"
summary_md="$out_dir/summary.md"
mkdir -p "$out_dir" "$(dirname "$log_path")"

candidates_tsv="$out_dir/candidates.tsv"
sqlite3 -separator $'\t' "$db" <<'SQL' > "$candidates_tsv"
WITH wallet_rows AS (
  SELECT
    wc.id AS candidate_id,
    f.id AS file_id,
    COALESCE(f.path, '') AS file_path,
    COALESCE(f.name, '') AS file_name,
    COALESCE(f.inode, '') AS inode,
    COALESCE(p.start_sector, 0) AS start_sector,
    COALESCE(p.sector_size, 512) AS sector_size
  FROM wallet_candidates wc
  JOIN files f ON f.id = wc.file_id
  LEFT JOIN partitions p ON p.id = f.partition_id
  WHERE lower(COALESCE(f.name, '')) = 'wallet.dat'
     OR lower(COALESCE(f.path, '')) LIKE '%wallet.dat%'
)
SELECT
  wr.candidate_id,
  wr.file_id,
  wr.file_path,
  wr.file_name,
  wr.inode,
  wr.start_sector,
  wr.sector_size,
  COALESCE((
    SELECT ra.id
    FROM recovered_artifacts ra
    WHERE ra.full_path = wr.file_path
       OR lower(ra.relative_path) LIKE '%' || lower(wr.file_name)
       OR lower(ra.full_path) LIKE '%' || lower(wr.file_name)
    ORDER BY ra.id
    LIMIT 1
  ), ''),
  COALESCE((
    SELECT ra.full_path
    FROM recovered_artifacts ra
    WHERE ra.full_path = wr.file_path
       OR lower(ra.relative_path) LIKE '%' || lower(wr.file_name)
       OR lower(ra.full_path) LIKE '%' || lower(wr.file_name)
    ORDER BY ra.id
    LIMIT 1
  ), '')
FROM wallet_rows wr
ORDER BY wr.candidate_id;
SQL

candidate_count="$(grep -c '.' "$candidates_tsv" 2>/dev/null || true)"

if [[ "$run" -ne 1 ]]; then
  printf 'DRY RUN: wallet inspection only. Re-run with --run to execute.\n'
  printf 'Candidates: %s\n' "$candidate_count"
  while IFS=$'\t' read -r candidate_id file_id file_path file_name inode start_sector sector_size artifact_id artifact_path; do
    wallet_path="$artifact_path"
    [[ -n "$wallet_path" ]] || wallet_path="$file_path"
    printf 'candidate=%s file_id=%s path=%s\n' "$candidate_id" "$file_id" "${wallet_path:-$file_path}"
    printf '  %s --dumpwallet --wallet %q\n' "$pywallet_cmd" "${wallet_path:-<exported-wallet.dat>}"
    printf '  %s --recover --recov_device=%q --output_keys=%q\n' "$pywallet_cmd" "${wallet_path:-<exported-wallet.dat>}" "$out_dir/$candidate_id/recovered_keys.txt"
  done < "$candidates_tsv"
  if [[ "$scan_raw" -eq 1 ]]; then
    printf 'raw scan preview: losetup -r --show -f %q\n' "$image_path"
    printf 'raw scan preview: %s --recover --recov_device=<loopdev> --output_keys=%q\n' "$pywallet_cmd" "$out_dir/raw-device/recovered_keys.txt"
  fi
  exit 0
fi

run_id="$(record_scan_start "$db" "wallet-inspect" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

{
  printf '# wallet-inspect %s\n\n' "$timestamp" > "$summary_md"
  printf 'Candidates: %s\n' "$candidate_count" | tee -a "$summary_md"

  if [[ "$candidate_count" -eq 0 ]]; then
    printf 'No wallet candidates found.\n' | tee -a "$summary_md"
    notes="no wallet candidates"
  else
    need_cmd "$pywallet_cmd"
    need_cmd icat

    while IFS=$'\t' read -r candidate_id file_id file_path file_name inode start_sector sector_size artifact_id artifact_path; do
      wallet_dir="$out_dir/$candidate_id"
      mkdir -p "$wallet_dir"
      wallet_path=""
      if [[ -n "$artifact_path" && -f "$artifact_path" ]]; then
        wallet_path="$artifact_path"
      elif [[ -n "$file_path" && -f "$file_path" ]]; then
        wallet_path="$file_path"
      elif [[ -n "$inode" ]]; then
        wallet_path="$wallet_dir/wallet.dat"
        icat -o "$start_sector" -b "$sector_size" "$image_path" "$inode" > "$wallet_path"
      else
        printf 'candidate %s: no physical path or inode available; skipped\n' "$candidate_id"
        python3 - "$db" "$artifact_id" "$file_path" <<'PY'
import sqlite3
import sys
from datetime import datetime, timezone

db_path, artifact_id, path = sys.argv[1:4]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
conn = sqlite3.connect(db_path)
conn.execute(
    """
    INSERT INTO findings(source_tool, category, artifact_id, path, key, value, score, notes, created_at)
    VALUES('pywallet', 'wallet-scan-skipped', ?, ?, 'reason', 'no physical path or inode', 0, 'wallet-inspect skipped candidate', ?)
    """,
    (int(artifact_id) if artifact_id else None, path, now),
)
conn.commit()
conn.close()
PY
        continue
      fi

      dump_txt="$wallet_dir/dump.txt"
      recover_txt="$wallet_dir/recovered_keys.txt"
      printf '\n## candidate %s\n\nPath: `%s`\n\n' "$candidate_id" "$wallet_path" >> "$summary_md"
      printf 'Inspecting candidate %s: %s\n' "$candidate_id" "$wallet_path"

      dump_rc=0
      "$pywallet_cmd" --dumpwallet --wallet "$wallet_path" > "$dump_txt" 2>&1 || dump_rc=$?
      if [[ "$dump_rc" -ne 0 ]]; then
        printf 'pywallet --dumpwallet failed for candidate %s; trying recovery mode\n' "$candidate_id"
        "$pywallet_cmd" --recover --recov_device="$wallet_path" --output_keys="$recover_txt" >> "$dump_txt" 2>&1 || true
        [[ -f "$recover_txt" ]] && cat "$recover_txt" >> "$dump_txt"
      fi

      python3 - "$db" "$artifact_id" "$wallet_path" "$dump_txt" "$summary_md" <<'PY'
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

db_path, artifact_id_raw, wallet_path, dump_path, summary_path = sys.argv[1:6]
artifact_id = int(artifact_id_raw) if artifact_id_raw else None
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

try:
    text = open(dump_path, "r", errors="replace").read()
except FileNotFoundError:
    text = ""

encrypted = 1 if re.search(r"\b(encrypted|mkey|ckey|passphrase|password)\b", text, re.I) else 0
wifs = sorted(set(re.findall(r"\b[5KL][1-9A-HJ-NP-Za-km-z]{50,51}\b", text)))
hex_keys = sorted(set(
    m.group(1).lower()
    for m in re.finditer(r"(?i)(?:hexsec|hex|private)[^0-9a-f]{0,40}([0-9a-f]{64})", text)
))

conn = sqlite3.connect(db_path)
for key in wifs:
    conn.execute(
        """
        INSERT INTO wallet_keys(source_artifact_id, source_method, key_type, key_value, encrypted, notes, created_at)
        VALUES(?, 'pywallet', 'wif', ?, ?, ?, ?)
        """,
        (artifact_id, key, encrypted, wallet_path, now),
    )
for key in hex_keys:
    conn.execute(
        """
        INSERT INTO wallet_keys(source_artifact_id, source_method, key_type, key_value, encrypted, notes, created_at)
        VALUES(?, 'pywallet', 'hex', ?, ?, ?, ?)
        """,
        (artifact_id, key, encrypted, wallet_path, now),
    )

key_count = len(wifs) + len(hex_keys)
if encrypted and key_count == 0:
    conn.execute(
        """
        INSERT INTO wallet_keys(source_artifact_id, source_method, key_type, key_value, encrypted, notes, created_at)
        VALUES(?, 'pywallet', 'wallet.dat', ?, 1, 'encrypted wallet placeholder; run image-crack-wallet.sh next', ?)
        """,
        (artifact_id, wallet_path, now),
    )

value = json.dumps({"path": wallet_path, "key_count": key_count, "encrypted": encrypted})
score = 80 if key_count else (60 if encrypted else 10)
notes = "encrypted wallet; run image-crack-wallet.sh next" if encrypted else ""
conn.execute(
    """
    INSERT INTO findings(source_tool, category, artifact_id, path, key, value, score, notes, created_at)
    VALUES('pywallet', 'wallet', ?, ?, 'inspection', ?, ?, ?, ?)
    """,
    (artifact_id, wallet_path, value, score, notes, now),
)
conn.commit()
conn.close()

with open(summary_path, "a") as summary:
    summary.write(f"- extracted_keys: {key_count}\n")
    summary.write(f"- encrypted: {encrypted}\n")
    if encrypted:
        summary.write("- next: run image-crack-wallet.sh for password recovery\n")
PY
    done < "$candidates_tsv"
  fi

  if [[ "$scan_raw" -eq 1 ]]; then
    need_cmd "$pywallet_cmd"
    need_cmd losetup
    raw_dir="$out_dir/raw-device"
    mkdir -p "$raw_dir"
    loopdev=""
    cleanup_loop() {
      if [[ -n "$loopdev" ]]; then
        losetup -d "$loopdev" || true
      fi
    }
    trap cleanup_loop EXIT
    loopdev="$(losetup -r --show -f "$image_path")"
    printf 'Raw pywallet scan loop device: %s\n' "$loopdev"
    if ! "$pywallet_cmd" --recover --recov_device="$loopdev" --output_keys="$raw_dir/recovered_keys.txt" > "$raw_dir/raw-scan.log" 2>&1; then
      printf 'Raw-device pywallet scan skipped or failed; see %s\n' "$raw_dir/raw-scan.log"
      python3 - "$db" "$raw_dir/raw-scan.log" <<'PY'
import sqlite3
import sys
from datetime import datetime, timezone

db_path, log_path = sys.argv[1:3]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
conn = sqlite3.connect(db_path)
conn.execute(
    """
    INSERT INTO findings(source_tool, category, path, key, value, score, notes, created_at)
    VALUES('pywallet', 'wallet-scan-skipped', ?, 'raw-device', 'pywallet recovery mode failed', 0, 'non-fatal raw scan failure', ?)
    """,
    (log_path, now),
)
conn.commit()
conn.close()
PY
    fi
  fi
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="wallet inspection failed or completed with errors"
}

if [[ "$candidate_count" -eq 0 ]]; then
  notes="no wallet candidates"
elif [[ -z "$notes" ]]; then
  inspected="$(sqlite3 -noheader "$db" "SELECT COUNT(*) FROM findings WHERE source_tool='pywallet' AND category='wallet';")"
  notes="wallet findings=$inspected"
fi
record_scan_end "$db" "$run_id" "$status" "$notes"
