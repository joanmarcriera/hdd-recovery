#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
WORK_DIR="${WORK_DIR:-/tmp/hdd-recovery-t10-keepass}"
DB="$WORK_DIR/t10.sqlite"
IMG="$WORK_DIR/t10.img"
EXPORT_ROOT="$WORK_DIR/export"
KDBX3_FIXTURE="${KDBX3_FIXTURE:-$ROOT_DIR/tests/fixtures/keepass/test123-kdbx3.kdbx}"
KDBX4_FIXTURE="${KDBX4_FIXTURE:-}"
WORDLIST="$WORK_DIR/wordlist.txt"

cat <<'EOF'
T10 KeePass cracking smoke test

Expected verification SQL:
  SELECT cracker, hash_mode, status, result_value FROM crack_tasks;
  SELECT source_tool, category, key, value FROM findings WHERE category='crack-result';
EOF

[[ -f "$KDBX3_FIXTURE" ]] || {
  echo "Missing KDBX3 fixture encrypted with password test123: $KDBX3_FIXTURE" >&2
  exit 2
}

rm -rf "$WORK_DIR"
mkdir -p "$EXPORT_ROOT/recovered" "$EXPORT_ROOT/logs" "$EXPORT_ROOT/hits"
truncate -s 16M "$IMG"
printf 'test123\n' > "$WORDLIST"
cp "$KDBX3_FIXTURE" "$EXPORT_ROOT/recovered/test123-kdbx3.kdbx"
if [[ -n "$KDBX4_FIXTURE" && -f "$KDBX4_FIXTURE" ]]; then
  cp "$KDBX4_FIXTURE" "$EXPORT_ROOT/recovered/kdbx4.kdbx"
fi

sqlite3 "$DB" < "$ROOT_DIR/sql/analysis-schema.sql" >/dev/null
sqlite3 "$DB" <<SQL
INSERT INTO image_info(id,image_path,image_name,image_basename,export_root,created_at,updated_at)
VALUES(1,'$IMG','t10.img','t10','$EXPORT_ROOT',datetime('now'),datetime('now'));
INSERT INTO recovered_artifacts(method,relative_path,full_path,size_bytes,mime_type,created_at)
VALUES('smoke','test123-kdbx3.kdbx','$EXPORT_ROOT/recovered/test123-kdbx3.kdbx',$(stat -c %s "$EXPORT_ROOT/recovered/test123-kdbx3.kdbx"),'application/x-keepass2',datetime('now'));
SQL
if [[ -f "$EXPORT_ROOT/recovered/kdbx4.kdbx" ]]; then
  sqlite3 "$DB" <<SQL
INSERT INTO recovered_artifacts(method,relative_path,full_path,size_bytes,mime_type,created_at)
VALUES('smoke','kdbx4.kdbx','$EXPORT_ROOT/recovered/kdbx4.kdbx',$(stat -c %s "$EXPORT_ROOT/recovered/kdbx4.kdbx"),'application/x-keepass2',datetime('now'));
SQL
fi

"$ROOT_DIR/bin/image-crack-keepass.sh" "$DB" --wordlist "$WORDLIST" --run
sqlite3 "$DB" "SELECT cracker, hash_mode, status, result_value FROM crack_tasks;"
sqlite3 "$DB" "SELECT source_tool, category, key, value FROM findings WHERE category='crack-result';"
sqlite3 "$DB" "SELECT CASE WHEN EXISTS(SELECT 1 FROM crack_tasks WHERE hash_mode='13400' AND result_value='test123') THEN 'PASS' ELSE 'FAIL' END;"
