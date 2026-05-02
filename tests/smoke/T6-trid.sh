#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
WORK_DIR="${WORK_DIR:-/tmp/hdd-recovery-t6-trid}"
DB="$WORK_DIR/t6.sqlite"
IMG="$WORK_DIR/t6.img"
EXPORT_ROOT="$WORK_DIR/export"
SAMPLE="$EXPORT_ROOT/recovered/sample.txt"

cat <<'EOF'
T6 TrID enrichment smoke test

Expected verification SQL:
  SELECT trid_top_ext, trid_top_score, trid_top3_json FROM recovered_artifacts;
  SELECT full_path FROM recovered_artifacts;
EOF

command -v trid >/dev/null || { echo "trid missing" >&2; exit 2; }

rm -rf "$WORK_DIR"
mkdir -p "$EXPORT_ROOT/recovered" "$EXPORT_ROOT/logs" "$EXPORT_ROOT/hits"
truncate -s 16M "$IMG"
printf 'plain text sample\n' > "$SAMPLE"
touch "$WORK_DIR/marker"

sqlite3 "$DB" < "$ROOT_DIR/sql/analysis-schema.sql" >/dev/null
sqlite3 "$DB" <<SQL
INSERT INTO image_info(id,image_path,image_name,image_basename,export_root,created_at,updated_at)
VALUES(1,'$IMG','t6.img','t6','$EXPORT_ROOT',datetime('now'),datetime('now'));
INSERT INTO recovered_artifacts(method,relative_path,full_path,size_bytes,mime_type,created_at)
VALUES('smoke','sample.txt','$SAMPLE',$(stat -c %s "$SAMPLE"),'text/plain',datetime('now'));
SQL

before_path="$(sqlite3 -noheader "$DB" "SELECT full_path FROM recovered_artifacts WHERE id=1;")"
"$ROOT_DIR/bin/image-enrich-trid.sh" "$DB" --run
after_path="$(sqlite3 -noheader "$DB" "SELECT full_path FROM recovered_artifacts WHERE id=1;")"
[[ "$before_path" == "$after_path" ]] || { echo "full_path changed" >&2; exit 1; }
if find "$EXPORT_ROOT/recovered" -type f -newer "$WORK_DIR/marker" | grep -q .; then
  echo "recovered files were modified during TrID enrichment" >&2
  exit 1
fi
sqlite3 "$DB" "SELECT trid_top_ext, trid_top_score, trid_top3_json FROM recovered_artifacts;"
"$ROOT_DIR/bin/image-enrich-trid.sh" "$DB" --run
sqlite3 "$DB" "SELECT COUNT(*) FROM recovered_artifacts WHERE trid_top_ext IS NOT NULL;"
