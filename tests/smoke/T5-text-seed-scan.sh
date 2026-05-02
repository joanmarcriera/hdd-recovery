#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
WORK_DIR="${WORK_DIR:-/tmp/hdd-recovery-t5-text-seed}"
DB="$WORK_DIR/t5.sqlite"
IMG="$WORK_DIR/t5.img"
EXPORT_ROOT="$WORK_DIR/export"
WORDLIST="$WORK_DIR/bip39-test-wordlist.txt"
NOTE="$EXPORT_ROOT/recovered/note.txt"

cat <<'EOF'
T5 text seed scanner smoke test

Expected verification SQL:
  SELECT source_tool, category, score, value FROM findings WHERE source_tool='text-seed-scan';
  SELECT note FROM notes WHERE note LIKE 'text-seed-scan:%';
EOF

rm -rf "$WORK_DIR"
mkdir -p "$EXPORT_ROOT/recovered" "$EXPORT_ROOT/logs" "$EXPORT_ROOT/hits"
truncate -s 16M "$IMG"

{
  printf '%s\n' abandon ability able about above absent absorb abstract absurd abuse access accident
  seq 1 2036 | sed 's/^/dummy/'
} > "$WORDLIST"

printf 'random text\nabandon ability able about above absent absorb abstract absurd abuse access accident\nmore text\n' > "$NOTE"

sqlite3 "$DB" < "$ROOT_DIR/sql/analysis-schema.sql" >/dev/null
sqlite3 "$DB" <<SQL
INSERT INTO image_info(id,image_path,image_name,image_basename,export_root,created_at,updated_at)
VALUES(1,'$IMG','t5.img','t5','$EXPORT_ROOT',datetime('now'),datetime('now'));
INSERT INTO recovered_artifacts(method,relative_path,full_path,size_bytes,mime_type,created_at)
VALUES('smoke','note.txt','$NOTE',$(stat -c %s "$NOTE"),'text/plain',datetime('now'));
SQL

"$ROOT_DIR/bin/image-text-seed-scan.sh" "$DB" --wordlist "$WORDLIST" --run
sqlite3 "$DB" "SELECT source_tool, category, score, value FROM findings WHERE source_tool='text-seed-scan';"
sqlite3 "$DB" "SELECT note FROM notes WHERE note LIKE 'text-seed-scan:%';"
sqlite3 "$DB" "SELECT CASE WHEN EXISTS(SELECT 1 FROM findings WHERE source_tool='text-seed-scan' AND category='seed_phrase' AND score=95) THEN 'PASS' ELSE 'FAIL' END;"
