#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
WORK_DIR="${WORK_DIR:-/tmp/hdd-recovery-t9-quality}"
DB="$WORK_DIR/t9.sqlite"
IMG="$WORK_DIR/t9.img"
EXPORT_ROOT="$WORK_DIR/export"

cat <<'EOF'
T9 image quality smoke test

Optional:
  REAL_PHOTO=/path/to/photo.jpg  use an actual photo instead of the synthetic
  high-entropy fixture for the "real photo scores high" check.

Expected verification SQL:
  SELECT relative_path, quality_score FROM recovered_artifacts ORDER BY quality_score DESC;
EOF

command -v exiftool >/dev/null || { echo "exiftool missing" >&2; exit 2; }
python3 - <<'PY' || { echo "Pillow missing" >&2; exit 2; }
from PIL import Image
PY

rm -rf "$WORK_DIR"
mkdir -p "$EXPORT_ROOT/recovered" "$EXPORT_ROOT/logs" "$EXPORT_ROOT/hits"
truncate -s 16M "$IMG"

python3 - "$EXPORT_ROOT/recovered" "${REAL_PHOTO:-}" <<'PY'
from pathlib import Path
from PIL import Image
import random
import shutil
import sys

root = Path(sys.argv[1])
real_photo = sys.argv[2]
if real_photo:
    shutil.copy(real_photo, root / "real-photo.jpg")
else:
    rng = random.Random(123)
    noise = Image.new("RGB", (800, 600))
    noise.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(800 * 600)])
    noise.save(root / "realish-noise.jpg", quality=90)
Image.new("RGB", (800, 600), (255, 255, 255)).save(root / "blank.jpg", quality=90)
Image.new("RGB", (4, 4), (30, 120, 200)).save(root / "thumb.jpg", quality=90)
PY

sqlite3 "$DB" < "$ROOT_DIR/sql/analysis-schema.sql" >/dev/null
sqlite3 "$DB" <<SQL
INSERT INTO image_info(id,image_path,image_name,image_basename,export_root,created_at,updated_at)
VALUES(1,'$IMG','t9.img','t9','$EXPORT_ROOT',datetime('now'),datetime('now'));
SQL
for path in "$EXPORT_ROOT"/recovered/*.jpg; do
  name="$(basename "$path")"
  sqlite3 "$DB" <<SQL
INSERT INTO recovered_artifacts(method,relative_path,full_path,size_bytes,mime_type,created_at)
VALUES('smoke','$name','$path',$(stat -c %s "$path"),'image/jpeg',datetime('now'));
SQL
done

"$ROOT_DIR/bin/image-enrich-photos.sh" "$DB" --run
sqlite3 "$DB" "SELECT relative_path, quality_score FROM recovered_artifacts ORDER BY quality_score DESC;"
photo_name="realish-noise.jpg"
[[ -n "${REAL_PHOTO:-}" ]] && photo_name="real-photo.jpg"
sqlite3 "$DB" "SELECT CASE WHEN (SELECT quality_score FROM recovered_artifacts WHERE relative_path='$photo_name') > (SELECT quality_score FROM recovered_artifacts WHERE relative_path='blank.jpg') AND (SELECT quality_score FROM recovered_artifacts WHERE relative_path='$photo_name') > (SELECT quality_score FROM recovered_artifacts WHERE relative_path='thumb.jpg') THEN 'PASS' ELSE 'FAIL' END;"
