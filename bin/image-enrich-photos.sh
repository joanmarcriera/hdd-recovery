#!/usr/bin/env bash
# Run exiftool on recovered image artifacts. Populates picture_candidates
# metadata columns (camera_model, taken_at, width, height) and writes GPS
# coordinates and other EXIF fields to the findings table.
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-enrich-photos.sh <db-path> [--run]

Reads recovered_artifacts rows with image MIME types, runs exiftool -json
on each physical file, and:
  • Updates picture_candidates.camera_model / taken_at / width / height
  • Writes GPS coordinates to the findings table (category=gps)
  • Writes other key EXIF fields to the findings table (category=metadata)
  • Updates recovered_artifacts.quality_score with a 0-100 heuristic

Default is dry-run. Pass --run to execute.

Requires: exiftool  (libimage-exiftool-perl), Pillow
Homepage: https://exiftool.org
EOF
}

db="${1:-}"
run=0
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run) run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd python3

export_root="$(db_image_export_root "$db")"
out_dir="$export_root/hits/exiftool"
log_path="$export_root/logs/enrich-photos.log"
mkdir -p "$out_dir"

if [[ "$run" -ne 1 ]]; then
  count="$(sqlite3 -noheader "$db" "
    SELECT COUNT(*)
    FROM recovered_artifacts
    WHERE (
      lower(COALESCE(mime_type,'')) LIKE 'image/%'
      OR lower(relative_path) GLOB '*.jpg'
      OR lower(relative_path) GLOB '*.jpeg'
      OR lower(relative_path) GLOB '*.png'
      OR lower(relative_path) GLOB '*.gif'
      OR lower(relative_path) GLOB '*.bmp'
      OR lower(relative_path) GLOB '*.webp'
      OR lower(relative_path) GLOB '*.tif'
      OR lower(relative_path) GLOB '*.tiff'
      OR lower(relative_path) GLOB '*.heic'
    );
  ")"
  printf 'DRY RUN: photo enrichment only. Re-run with --run to execute.\n'
  printf 'Image artifacts: %s\n' "$count"
  printf 'Will update EXIF findings and recovered_artifacts.quality_score.\n'
  exit 0
fi

need_cmd exiftool
run_id="$(record_scan_start "$db" "enrich-photos" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

{
PYTHONPATH="$ROOT_DIR" python3 - "$db" "$out_dir" <<'PY'
import csv, json, os, sqlite3, subprocess, sys
from datetime import datetime, timezone

try:
    from PIL import Image
except Exception:
    Image = None

db_path, out_dir = sys.argv[1:3]
conn = sqlite3.connect(db_path)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

IMAGE_EXTS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    '.tif', '.tiff', '.heic', '.cr2', '.nef', '.arw',
    '.dng', '.raf', '.orf',
}

def run_exiftool(path):
    try:
        r = subprocess.run(
            ['exiftool', '-json', '-n',
             '-GPSLatitude', '-GPSLongitude', '-GPSLatitudeRef', '-GPSLongitudeRef',
             '-DateTimeOriginal', '-CreateDate',
             '-ImageWidth', '-ImageHeight', '-Model', '-Make',
             path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(r.stdout)
        return data[0] if data else {}
    except Exception:
        return {}

def parse_gps(val, ref):
    if val is None:
        return None
    try:
        v = float(val)
        return -v if ref in ('S', 'W') else v
    except Exception:
        return None

def quality_score(path):
    if Image is None:
        return None
    try:
        with Image.open(path) as img:
            width, height = img.size
            gray = img.convert('L')
            entropy = float(gray.entropy())
    except Exception:
        return 0.0

    pixels = max(1, width * height)
    entropy_component = max(0.0, min(60.0, (entropy / 8.0) * 60.0))
    size_component = max(0.0, min(35.0, (pixels / (1600 * 1200)) * 35.0))
    min_dim = min(width, height)
    small_penalty = 35.0 if min_dim < 64 else (20.0 if min_dim < 256 else 0.0)
    score = entropy_component + size_component + 5.0 - small_penalty
    return round(max(0.0, min(100.0, score)), 2)

rows = conn.execute("""
    SELECT ra.id, ra.full_path, ra.sha256, ra.relative_path
    FROM   recovered_artifacts ra
    WHERE  (
        lower(ra.mime_type) LIKE 'image/%'
        OR lower(substr(ra.relative_path, instr(ra.relative_path,'.'))) IN
           ('.jpg','.jpeg','.png','.gif','.bmp','.webp',
            '.tif','.tiff','.heic','.cr2','.nef','.arw','.dng','.raf','.orf')
    )
    ORDER  BY ra.size_bytes ASC NULLS LAST
""").fetchall()

print(f"Processing {len(rows)} image artifacts with exiftool...")

gps_rows  = []
meta_count = 0
quality_count = 0
findings   = []

for art_id, full_path, sha256, rel_path in rows:
    if not full_path or not os.path.isfile(full_path):
        continue

    meta = run_exiftool(full_path)
    qscore = quality_score(full_path)

    camera = (meta.get('Model') or meta.get('Make') or '').strip()
    taken_raw = meta.get('DateTimeOriginal') or meta.get('CreateDate', '')
    width  = meta.get('ImageWidth')
    height = meta.get('ImageHeight')
    lat    = parse_gps(meta.get('GPSLatitude'),  meta.get('GPSLatitudeRef',  'N'))
    lon    = parse_gps(meta.get('GPSLongitude'), meta.get('GPSLongitudeRef', 'E'))

    taken_at = None
    if taken_raw:
        try:
            taken_at = datetime.strptime(str(taken_raw)[:19], "%Y:%m:%d %H:%M:%S").isoformat() + 'Z'
        except Exception:
            taken_at = str(taken_raw)[:32]

    # Update picture_candidates that share the same SHA256 or path fragment
    upd, params = [], []
    if camera:
        upd.append("camera_model = ?"); params.append(camera[:255])
    if taken_at:
        upd.append("taken_at = ?");     params.append(taken_at)
    if width and str(width).isdigit():
        upd.append("width = ?");        params.append(int(width))
    if height and str(height).isdigit():
        upd.append("height = ?");       params.append(int(height))

    if upd and sha256:
        # Match via files table SHA1/MD5 columns (closest available proxy)
        fids = [r[0] for r in conn.execute(
            "SELECT id FROM files WHERE sha1 = ? OR md5 = ?",
            (sha256[:40], sha256[:32]),
        ).fetchall()]
        for fid in fids:
            conn.execute(
                f"UPDATE picture_candidates SET {', '.join(upd)} WHERE file_id = ?",
                params + [fid],
            )
        meta_count += 1

    if qscore is not None:
        conn.execute(
            "UPDATE recovered_artifacts SET quality_score = ? WHERE id = ?",
            (qscore, art_id),
        )
        quality_count += 1

    # GPS findings (high interest)
    if lat is not None and lon is not None:
        combo = f"{lat:.6f},{lon:.6f}"
        findings += [
            (art_id, full_path, 'gps',      'gps_lat',      str(lat),  70),
            (art_id, full_path, 'gps',      'gps_lon',      str(lon),  70),
            (art_id, full_path, 'gps',      'gps_combined', combo,     70),
        ]
        gps_rows.append([art_id, os.path.basename(full_path), combo, full_path])
        print(f"  GPS {combo}  ← {full_path}")

    # Metadata findings (informational)
    if camera:
        findings.append((art_id, full_path, 'metadata', 'camera_model', camera, 30))
    if taken_at:
        findings.append((art_id, full_path, 'metadata', 'taken_at', taken_at, 20))

# Bulk-insert findings
for art_id, path, cat, key, value, score in findings:
    conn.execute("""
        INSERT OR IGNORE INTO findings
            (source_tool, category, artifact_id, path, key, value, score, created_at)
        VALUES ('exiftool', ?, ?, ?, ?, ?, ?, ?)
    """, (cat, art_id, path, key, value, score, now))

conn.commit()

# Write GPS summary TSV
if gps_rows:
    with open(os.path.join(out_dir, 'gps_hits.tsv'), 'w', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['artifact_id', 'filename', 'lat_lon', 'full_path'])
        w.writerows(gps_rows)
    print(f"GPS summary → {out_dir}/gps_hits.tsv")

quality_path = os.path.join(out_dir, 'quality_scores.tsv')
with open(quality_path, 'w', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['artifact_id', 'quality_score', 'full_path'])
    for row in conn.execute("""
        SELECT id, quality_score, full_path
        FROM recovered_artifacts
        WHERE quality_score IS NOT NULL
        ORDER BY quality_score DESC
    """):
        w.writerow(row)

conn.close()

print(f"Done. metadata_updated={meta_count}  gps_hits={len(gps_rows)}  "
      f"quality_scores={quality_count}  total_findings={len(findings)}")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="exiftool enrichment failed or incomplete; check log"
}

record_scan_end "$db" "$run_id" "$status" "$notes"
