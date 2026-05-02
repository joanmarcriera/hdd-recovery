#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
FIXTURE="${FIXTURE:-$ROOT_DIR/tests/fixtures/wallet/wallet-encrypted.dat}"
WORK_DIR="${WORK_DIR:-/tmp/hdd-recovery-t3-crack-wallet}"
DB="$WORK_DIR/t3.sqlite"
IMG="$WORK_DIR/t3.img"
EXPORT_ROOT="$WORK_DIR/export"
WORDLIST="$WORK_DIR/wordlist.txt"

cat <<'EOF'
T3 wallet cracking smoke test

Expected verification SQL:
  SELECT status, result_value FROM crack_tasks;
  SELECT source_tool, category, key, value FROM findings WHERE category='crack-result';
  SELECT source_method, encrypted, COUNT(*) FROM wallet_keys GROUP BY source_method, encrypted;
EOF

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$EXPORT_ROOT/logs" "$EXPORT_ROOT/hits" "$EXPORT_ROOT/state"
truncate -s 16M "$IMG"
printf 'test123\n' > "$WORDLIST"

if [[ ! -f "$FIXTURE" ]]; then
  cat >&2 <<EOF
Missing encrypted wallet fixture:
  $FIXTURE

Provide a tiny Bitcoin Core wallet.dat encrypted with password 'test123'.
EOF
  exit 2
fi

sqlite3 "$DB" < "$ROOT_DIR/sql/analysis-schema.sql" >/dev/null
sqlite3 "$DB" <<SQL
INSERT INTO image_info(id,image_path,image_name,image_basename,export_root,created_at,updated_at)
VALUES(1,'$IMG','t3.img','t3','$EXPORT_ROOT',datetime('now'),datetime('now'));
INSERT INTO recovered_artifacts(id,method,relative_path,full_path,created_at)
VALUES(1,'smoke','wallet-encrypted.dat','$FIXTURE',datetime('now'));
INSERT INTO wallet_keys(source_artifact_id,source_method,key_type,key_value,encrypted,notes,created_at)
VALUES(1,'pywallet','wallet.dat','$FIXTURE',1,'smoke encrypted wallet placeholder',datetime('now'));
SQL

args=("$ROOT_DIR/bin/image-crack-wallet.sh" "$DB" --wordlist "$WORDLIST" --run)
if [[ "${CPU_FALLBACK:-0}" == "1" ]]; then
  args+=(--cpu-fallback)
fi
"${args[@]}"

sqlite3 "$DB" "SELECT status, result_value FROM crack_tasks;"
sqlite3 "$DB" "SELECT source_tool, category, key, value FROM findings WHERE category='crack-result';"
sqlite3 "$DB" "SELECT source_method, encrypted, COUNT(*) FROM wallet_keys GROUP BY source_method, encrypted;"
sqlite3 "$DB" "SELECT CASE WHEN EXISTS(SELECT 1 FROM crack_tasks WHERE result_value='test123') THEN 'PASS' ELSE 'FAIL' END;"

if [[ "${TEST_PAUSE:-0}" == "1" ]]; then
  echo
  echo "Pause/resume check: interrupting a run and expecting a paused task."
  PAUSE_DB="$WORK_DIR/t3-pause.sqlite"
  cp "$DB" "$PAUSE_DB"
  sqlite3 "$PAUSE_DB" "DELETE FROM crack_tasks; DELETE FROM findings WHERE category='crack-result';"
  printf 'not-the-password\n' > "$WORK_DIR/nohit-wordlist.txt"
  set +e
  timeout -s INT "${PAUSE_TIMEOUT_SECONDS:-5}" \
    "$ROOT_DIR/bin/image-crack-wallet.sh" "$PAUSE_DB" --wordlist "$WORK_DIR/nohit-wordlist.txt" --run
  set -e
  sqlite3 "$PAUSE_DB" "SELECT id,status,checkpoint_path FROM crack_tasks;"
  sqlite3 "$PAUSE_DB" "SELECT CASE WHEN EXISTS(SELECT 1 FROM crack_tasks WHERE status='paused' AND checkpoint_path IS NOT NULL) THEN 'PAUSE-PASS' ELSE 'PAUSE-CHECK-MANUAL' END;"
fi
