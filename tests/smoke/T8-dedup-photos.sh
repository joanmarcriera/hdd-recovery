#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
WORK_DIR="${WORK_DIR:-/tmp/hdd-recovery-t8-dedup}"
DB="$WORK_DIR/t8.sqlite"
IMG="$WORK_DIR/t8.img"
EXPORT_ROOT="$WORK_DIR/export"

cat <<'EOF'
T8 photo dedup smoke test

Expected verification SQL:
  SELECT dedup_cluster_id, COUNT(*), SUM(is_cluster_primary) FROM recovered_artifacts GROUP BY dedup_cluster_id;
EOF

python3 - <<'PY' || { echo "Pillow/imagehash missing" >&2; exit 2; }
import imagehash
from PIL import Image
PY

rm -rf "$WORK_DIR"
mkdir -p "$EXPORT_ROOT/recovered" "$EXPORT_ROOT/logs" "$EXPORT_ROOT/hits"
truncate -s 16M "$IMG"

python3 - "$EXPORT_ROOT/recovered" <<'PY'
from pathlib import Path
from PIL import Image
import sys

root = Path(sys.argv[1])
base = Image.new("RGB", (64, 64), (200, 40, 40))
for i in range(5):
    base.save(root / f"copy-{i}.jpg", quality=90)
Image.new("RGB", (64, 64), (20, 80, 200)).save(root / "other.jpg", quality=90)
PY

sqlite3 "$DB" < "$ROOT_DIR/sql/analysis-schema.sql" >/dev/null
sqlite3 "$DB" <<SQL
INSERT INTO image_info(id,image_path,image_name,image_basename,export_root,created_at,updated_at)
VALUES(1,'$IMG','t8.img','t8','$EXPORT_ROOT',datetime('now'),datetime('now'));
SQL
for path in "$EXPORT_ROOT"/recovered/*.jpg; do
  name="$(basename "$path")"
  sqlite3 "$DB" <<SQL
INSERT INTO recovered_artifacts(method,relative_path,full_path,size_bytes,mime_type,created_at)
VALUES('smoke','$name','$path',$(stat -c %s "$path"),'image/jpeg',datetime('now'));
SQL
done

"$ROOT_DIR/bin/image-dedup-photos.sh" "$DB" --run
sqlite3 "$DB" "SELECT dedup_cluster_id, COUNT(*), SUM(is_cluster_primary) FROM recovered_artifacts GROUP BY dedup_cluster_id ORDER BY COUNT(*) DESC;"
sqlite3 "$DB" "SELECT CASE WHEN (SELECT COUNT(DISTINCT dedup_cluster_id) FROM recovered_artifacts)=2 AND NOT EXISTS(SELECT 1 FROM recovered_artifacts GROUP BY dedup_cluster_id HAVING SUM(is_cluster_primary)<>1) THEN 'PASS' ELSE 'FAIL' END;"
