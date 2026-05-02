#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
WORK_DIR="${WORK_DIR:-/tmp/hdd-recovery-t4-btcrecover}"
DB="$WORK_DIR/t4.sqlite"
IMG="$WORK_DIR/t4.img"
EXPORT_ROOT="$WORK_DIR/export"
CONFIG="$EXPORT_ROOT/state/btcrecover/bip39-missing-word.yml"

cat <<'EOF'
T4 btcrecover smoke test

Expected verification SQL:
  SELECT cracker,target_kind,status,result_value FROM crack_tasks WHERE cracker='btcrecover';
  SELECT source_method,key_type,key_value FROM wallet_keys WHERE source_method='btcrecover';
EOF

rm -rf "$WORK_DIR"
mkdir -p "$EXPORT_ROOT/logs" "$EXPORT_ROOT/hits" "$EXPORT_ROOT/state/btcrecover"
truncate -s 16M "$IMG"

sqlite3 "$DB" < "$ROOT_DIR/sql/analysis-schema.sql" >/dev/null
sqlite3 "$DB" <<SQL
INSERT INTO image_info(id,image_path,image_name,image_basename,export_root,created_at,updated_at)
VALUES(1,'$IMG','t4.img','t4','$EXPORT_ROOT',datetime('now'),datetime('now'));
SQL

cat > "$CONFIG" <<'YAML'
target_type: seed
task_name: bip39-missing-word
command: seedrecover.py --wallet-type bip39 --addrs 1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA --mnemonic "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon" --addr-limit 1
YAML

"$ROOT_DIR/bin/image-btcrecover.sh" "$DB" --config "$CONFIG" --run
sqlite3 "$DB" "SELECT cracker,target_kind,status,result_value FROM crack_tasks WHERE cracker='btcrecover';"
sqlite3 "$DB" "SELECT source_method,key_type,key_value FROM wallet_keys WHERE source_method='btcrecover';"
sqlite3 "$DB" "SELECT CASE WHEN EXISTS(SELECT 1 FROM crack_tasks WHERE cracker='btcrecover' AND status='cracked' AND result_value LIKE '%about%') THEN 'PASS' ELSE 'FAIL' END;"
