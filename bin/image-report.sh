#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-report.sh <db-path>

Generates a per-image markdown report from the SQLite catalog.
Covers image info, stage history, wallet/picture candidates, and
recovered artifact locations with disk usage.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
db_ro="file:$db?mode=ro"

export_root="$(db_image_export_root "$db")"
report_path="$export_root/reports/summary.md"
mkdir -p "$export_root/reports"

{
  printf '# Image Analysis Report\n\n'
  printf '_Generated: %s_\n\n' "$(date -u +"%Y-%m-%d %H:%M UTC")"

  # ── Image info ──────────────────────────────────────────────────────────
  printf '## Image\n\n'
  sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT image_name AS "Name", image_path AS "Path",
       COALESCE(image_sha256, '(not hashed)') AS "SHA-256",
       printf('%.1f GB', image_size_bytes/1073741824.0) AS "Size"
FROM image_info;
EOF

  # ── Map coverage ────────────────────────────────────────────────────────
  mapfile_path="$(sqlite3 -noheader "$db_ro" "SELECT COALESCE(ddrescue_map_path,'') FROM image_info WHERE id=1;")"
  if [[ -n "$mapfile_path" && -f "$mapfile_path" ]]; then
    printf '\n## ddrescue Coverage\n\n'
    printf '```\n'
    ddrescuelog -t "$mapfile_path" 2>/dev/null || printf '(ddrescuelog unavailable)\n'
    printf '```\n'
  fi

  # ── Partitions ──────────────────────────────────────────────────────────
  printf '\n## Partitions\n\n'
  part_count="$(sqlite3 -noheader "$db_ro" "SELECT COUNT(*) FROM partitions;")"
  if [[ "$part_count" -eq 0 ]]; then
    printf '_No partitions recorded. Run structure-scan._\n'
  else
    sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT slot AS "Slot", filesystem_hint AS "FS",
       printf('%s → %s', COALESCE(start_sector,'?'), COALESCE(end_sector,'?')) AS "Sectors",
       description AS "Description"
FROM partitions
ORDER BY start_sector IS NULL, start_sector;
EOF
  fi

  # ── Stage run history ───────────────────────────────────────────────────
  printf '\n## Stage Run History\n\n'
  run_count="$(sqlite3 -noheader "$db_ro" "SELECT COUNT(*) FROM scan_runs;")"
  if [[ "$run_count" -eq 0 ]]; then
    printf '_No stages recorded yet._\n'
  else
    sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT stage AS "Stage",
       status AS "Status",
       substr(started_at,1,16) AS "Started",
       COALESCE(substr(ended_at,1,16),'…') AS "Ended",
       COALESCE(notes,'') AS "Notes"
FROM scan_runs
ORDER BY id;
EOF
  fi

  # ── Files summary ────────────────────────────────────────────────────────
  printf '\n## File Inventory Summary\n\n'
  sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT
  COUNT(*) AS "Total files",
  SUM(CASE WHEN deleted=1 THEN 1 ELSE 0 END) AS "Deleted",
  SUM(CASE WHEN is_dir=1 THEN 1 ELSE 0 END) AS "Directories",
  printf('%.1f GB', SUM(COALESCE(size_bytes,0))/1073741824.0) AS "Total size"
FROM files;
EOF

  printf '\n### Files by extension (top 20)\n\n'
  sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT COALESCE(NULLIF(extension,''),'(none)') AS "Extension",
       COUNT(*) AS "Count",
       printf('%.1f MB', SUM(COALESCE(size_bytes,0))/1048576.0) AS "Total size"
FROM files
GROUP BY extension
ORDER BY COUNT(*) DESC
LIMIT 20;
EOF

  # ── Wallet candidates ────────────────────────────────────────────────────
  printf '\n## Wallet Candidates\n\n'
  wc_count="$(sqlite3 -noheader "$db_ro" "SELECT COUNT(*) FROM wallet_candidates;")"
  if [[ "$wc_count" -eq 0 ]]; then
    printf '_None found._\n'
  else
    printf '_Total: %s candidates. Top 30 by score:_\n\n' "$wc_count"
    sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT wc.score AS "Score",
       wc.reason AS "Reason",
       f.path AS "Path",
       COALESCE(f.size_bytes,'?') AS "Bytes",
       COALESCE(f.deleted,'?') AS "Del"
FROM wallet_candidates wc
JOIN files f ON f.id = wc.file_id
ORDER BY wc.score DESC, f.path
LIMIT 30;
EOF
  fi

  # ── Picture candidates ───────────────────────────────────────────────────
  printf '\n## Picture Candidates\n\n'
  pc_count="$(sqlite3 -noheader "$db_ro" "SELECT COUNT(*) FROM picture_candidates;")"
  if [[ "$pc_count" -eq 0 ]]; then
    printf '_None found._\n'
  else
    printf '_Total: %s candidates. Top 30 by score:_\n\n' "$pc_count"
    sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT pc.score AS "Score",
       pc.reason AS "Reason",
       f.path AS "Path",
       COALESCE(f.size_bytes,'?') AS "Bytes",
       COALESCE(f.deleted,'?') AS "Del"
FROM picture_candidates pc
JOIN files f ON f.id = pc.file_id
ORDER BY pc.score DESC, f.path
LIMIT 30;
EOF
  fi

  # ── Recovered artifacts ──────────────────────────────────────────────────
  printf '\n## Recovered Artifacts\n\n'
  art_count="$(sqlite3 -noheader "$db_ro" "SELECT COUNT(*) FROM recovered_artifacts;")"
  if [[ "$art_count" -eq 0 ]]; then
    printf '_No artifacts registered. Run carving or ext-recovery stages._\n'
  else
    printf '_Total: %s artifacts across all methods._\n\n' "$art_count"

    printf '### By method\n\n'
    sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT method AS "Method",
       COUNT(*) AS "Files",
       printf('%.1f MB', SUM(COALESCE(size_bytes,0))/1048576.0) AS "Total size"
FROM recovered_artifacts
GROUP BY method
ORDER BY COUNT(*) DESC;
EOF

    printf '\n### Output directories and disk usage\n\n'
    # Show output_dir per method from latest scan_run, with du -sh
    while IFS='|' read -r method outdir; do
      if [[ -n "$outdir" && -d "$outdir" ]]; then
        usage_str="$(du -sh "$outdir" 2>/dev/null | cut -f1)"
        printf '- **%s** → `%s` (%s on disk)\n' "$method" "$outdir" "${usage_str:-?}"
      elif [[ -n "$outdir" ]]; then
        printf '- **%s** → `%s` _(directory not found)_\n' "$method" "$outdir"
      fi
    done < <(sqlite3 -noheader -separator '|' "$db_ro" \
      "SELECT ra.method, MAX(sr.output_dir)
       FROM recovered_artifacts ra
       LEFT JOIN scan_runs sr ON sr.id = ra.source_run_id
       GROUP BY ra.method
       ORDER BY ra.method;")

    printf '\n### By MIME type (top 20)\n\n'
    sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT COALESCE(mime_type,'(unknown)') AS "MIME type",
       COUNT(*) AS "Files",
       printf('%.1f MB', SUM(COALESCE(size_bytes,0))/1048576.0) AS "Total size"
FROM recovered_artifacts
GROUP BY mime_type
ORDER BY COUNT(*) DESC
LIMIT 20;
EOF
  fi

  # ── Bulk extractor hits ─────────────────────────────────────────────────
  printf '\n## Bulk Extractor Hits\n\n'
  be_count="$(sqlite3 -noheader "$db_ro" "SELECT COUNT(*) FROM bulk_extractor_hits;")"
  if [[ "$be_count" -eq 0 ]]; then
    printf '_No hits imported. Run bulk-extractor stages._\n'
  else
    printf '_Total: %s rows imported._\n\n' "$be_count"
    sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT source_scope AS "Scope",
       feature_file AS "Feature file",
       COUNT(*) AS "Rows"
FROM bulk_extractor_hits
GROUP BY source_scope, feature_file
ORDER BY source_scope, COUNT(*) DESC;
EOF

    printf '\n### Sample crypto / email hits (up to 20)\n\n'
    sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT source_scope AS "Scope",
       feature_file AS "File",
       substr(value,1,80) AS "Value"
FROM bulk_extractor_hits
WHERE feature_file IN ('bitcoin.txt','ethereum.txt','url.txt','email.txt','domain.txt')
ORDER BY feature_file, source_scope
LIMIT 20;
EOF
  fi

  # ── Notes ───────────────────────────────────────────────────────────────
  note_count="$(sqlite3 -noheader "$db_ro" "SELECT COUNT(*) FROM notes;")"
  if [[ "$note_count" -gt 0 ]]; then
    printf '\n## Operator Notes\n\n'
    sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT substr(created_at,1,16) AS "When", note AS "Note" FROM notes ORDER BY id;
EOF
  fi

} > "$report_path"

printf '%s\n' "$report_path"
