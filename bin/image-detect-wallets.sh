#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-detect-wallets.sh <db-path>

Scores filesystem-aware file inventory entries for wallet relevance using
filename/path heuristics. Content-based signals should be added later from
bulk_extractor or exported-file review.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

keywords_file="${WALLET_KEYWORDS_FILE:-$ROOT_DIR/config/keywords/wallet_keywords.txt}"
[[ -f "$keywords_file" ]] || die "wallet keywords file not found: $keywords_file"

export_root="$(db_image_export_root "$db")"
log_path="$export_root/logs/detect-wallets.log"
run_id="$(record_scan_start "$db" "detect-wallets" "$0 $db" "$log_path" "$export_root/hits")"

{
  sqlite3 "$db" "DELETE FROM wallet_candidates WHERE source_stage='detect-wallets';"

  while IFS= read -r keyword; do
    [[ -n "$keyword" ]] || continue
    lower_keyword="$(printf '%s' "$keyword" | tr '[:upper:]' '[:lower:]')"
    sqlite3 "$db" <<EOF
INSERT OR IGNORE INTO wallet_candidates(file_id,source_stage,score,reason,details,created_at)
SELECT id,'detect-wallets',60,'keyword-path','matched keyword: $(sql_escape "$keyword")','$(timestamp_utc)'
FROM files
WHERE is_dir = 0
  AND (
    lower(COALESCE(path,'')) LIKE '%$(sql_escape "$lower_keyword")%'
    OR lower(COALESCE(name,'')) LIKE '%$(sql_escape "$lower_keyword")%'
  );
EOF
  done < "$keywords_file"

  sqlite3 "$db" <<EOF
INSERT OR IGNORE INTO wallet_candidates(file_id,source_stage,score,reason,details,created_at)
SELECT id,'detect-wallets',95,'wallet-dat-filename','exact filename wallet.dat','$(timestamp_utc)'
FROM files
WHERE lower(COALESCE(name,'')) = 'wallet.dat';

INSERT OR IGNORE INTO wallet_candidates(file_id,source_stage,score,reason,details,created_at)
SELECT id,'detect-wallets',20,'wallet-extension','interesting extension/dat/json/db/sqlite','$(timestamp_utc)'
FROM files
WHERE is_dir = 0
  AND lower(COALESCE(extension,'')) IN ('dat','json','db','sqlite','wallet');

INSERT OR IGNORE INTO wallet_candidates(file_id,source_stage,score,reason,details,created_at)
SELECT id,'detect-wallets',70,'wallet-directory-pattern','common wallet directory pattern','$(timestamp_utc)'
FROM files
WHERE is_dir = 0
  AND (
    lower(COALESCE(path,'')) LIKE '%/.bitcoin/%'
    OR lower(COALESCE(path,'')) LIKE '%/electrum/%'
    OR lower(COALESCE(path,'')) LIKE '%/multibit/%'
    OR lower(COALESCE(path,'')) LIKE '%/armory/%'
    OR lower(COALESCE(path,'')) LIKE '%/keystore/%'
  );

INSERT OR IGNORE INTO wallet_candidates(file_id,source_stage,score,reason,details,created_at)
SELECT id,'detect-wallets',75,'wallet-name-and-extension','interesting extension combined with wallet-related path/name','$(timestamp_utc)'
FROM files
WHERE is_dir = 0
  AND lower(COALESCE(extension,'')) IN ('dat','json','db','sqlite','wallet')
  AND (
    lower(COALESCE(path,'')) LIKE '%wallet%'
    OR lower(COALESCE(path,'')) LIKE '%bitcoin%'
    OR lower(COALESCE(path,'')) LIKE '%electrum%'
    OR lower(COALESCE(path,'')) LIKE '%armory%'
    OR lower(COALESCE(path,'')) LIKE '%multibit%'
    OR lower(COALESCE(name,'')) LIKE '%wallet%'
    OR lower(COALESCE(name,'')) LIKE '%bitcoin%'
    OR lower(COALESCE(name,'')) LIKE '%electrum%'
  );
EOF

  sqlite3 "$db" <<'EOF'
.headers on
.mode column
SELECT wc.score, f.path, wc.reason
FROM wallet_candidates wc
JOIN files f ON f.id = wc.file_id
ORDER BY wc.score DESC, f.path
LIMIT 30;
EOF
} 2>&1 | tee "$log_path"

record_scan_end "$db" "$run_id" "ok"
