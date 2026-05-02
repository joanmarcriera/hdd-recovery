#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
SCRIPT="$ROOT_DIR/bin/image-wallet-inspect.sh"
FIXTURE_DIR="$ROOT_DIR/tests/fixtures/wallet"
WORK_DIR="${WORK_DIR:-/tmp/hdd-recovery-t1-pywallet}"
DB="$WORK_DIR/t1.sqlite"
IMG="$WORK_DIR/t1.img"
EXPORT_ROOT="$WORK_DIR/export"

cat <<'EOF'
T1 pywallet smoke test

Expected verification SQL:
  SELECT key_type, encrypted, COUNT(*) FROM wallet_keys GROUP BY key_type, encrypted;
  SELECT source_tool, category, key, value FROM findings WHERE source_tool='pywallet';
EOF

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$EXPORT_ROOT/logs" "$EXPORT_ROOT/hits" "$FIXTURE_DIR"
truncate -s 16M "$IMG"

if [[ ! -f "$FIXTURE_DIR/wallet-plain.dat" || ! -f "$FIXTURE_DIR/wallet-encrypted.dat" ]]; then
  cat >&2 <<EOF
Missing wallet fixtures:
  $FIXTURE_DIR/wallet-plain.dat
  $FIXTURE_DIR/wallet-encrypted.dat

Place known-good Bitcoin Core wallet.dat fixtures there before running this
owner verification. The plain fixture must expose at least one WIF key. The
encrypted fixture must be password protected.
EOF
  exit 2
fi

sqlite3 "$DB" < "$ROOT_DIR/sql/analysis-schema.sql" >/dev/null
sqlite3 "$DB" <<SQL
INSERT INTO image_info(id,image_path,image_name,image_basename,export_root,created_at,updated_at)
VALUES(1,'$IMG','t1.img','t1','$EXPORT_ROOT',datetime('now'),datetime('now'));
INSERT INTO partitions(id,start_sector,sector_size,source) VALUES(1,0,512,'smoke');
INSERT INTO files(id,partition_id,source_tool,inode,path,name,is_dir)
VALUES
  (1,1,'smoke','1','$FIXTURE_DIR/wallet-plain.dat','wallet.dat',0),
  (2,1,'smoke','2','$FIXTURE_DIR/wallet-encrypted.dat','wallet.dat',0);
INSERT INTO wallet_candidates(file_id,source_stage,score,reason,created_at)
VALUES
  (1,'smoke',95,'wallet-dat-filename',datetime('now')),
  (2,'smoke',95,'wallet-dat-filename',datetime('now'));
INSERT INTO recovered_artifacts(method,relative_path,full_path,created_at)
VALUES
  ('smoke','wallet-plain.dat','$FIXTURE_DIR/wallet-plain.dat',datetime('now')),
  ('smoke','wallet-encrypted.dat','$FIXTURE_DIR/wallet-encrypted.dat',datetime('now'));
SQL

"$SCRIPT" "$DB" --run
sqlite3 "$DB" "SELECT key_type, encrypted, COUNT(*) FROM wallet_keys GROUP BY key_type, encrypted;"
sqlite3 "$DB" "SELECT source_tool, category, key, value FROM findings WHERE source_tool='pywallet';"

truncate -s 16M "$WORK_DIR/blank.img"
sqlite3 "$DB" "UPDATE image_info SET image_path='$WORK_DIR/blank.img';"
"$SCRIPT" "$DB" --scan-raw --run || {
  echo "raw loop-device flow failed" >&2
  exit 1
}
losetup -l | grep -F "$WORK_DIR/blank.img" && {
  echo "loop device still attached after raw scan" >&2
  exit 1
}
