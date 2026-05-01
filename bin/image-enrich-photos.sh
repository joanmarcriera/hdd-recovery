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
  image-enrich-photos.sh <db-path>

Reads recovered_artifacts rows with image MIME types, runs exiftool -json
on each physical file, and:
  • Updates picture_candidates.camera_model / taken_at / width / height
  • Writes GPS coordinates to the findings table (category=gps)
  • Writes other key EXIF fields to the findings table (category=metadata)

Requires: exiftool  (libimage-exiftool-perl)
Homepage: https://exiftool.org
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd exiftool
need_cmd python3

export_root="$(db_image_export_root "$db")"
out_dir="$export_root/hits/exiftool"
log_path="$export_root/logs/enrich-photos.log"
mkdir -p "$out_dir"

run_id="$(record_scan_start "$db" "enrich-photos" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

{
python3 - "$db" "$out_dir" <<'PY'
import csv, json, os, sqlite3, subprocess, sys
from datetime import datetime, timezone

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
findings   = []

for art_id, full_path, sha256, rel_path in rows:
    if not full_path or not os.path.isfile(full_path):
        continue

    meta = run_exiftool(full_path)
    if not meta:
        continue

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
conn.close()

# Write GPS summary TSV
if gps_rows:
    with open(os.path.join(out_dir, 'gps_hits.tsv'), 'w', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['artifact_id', 'filename', 'lat_lon', 'full_path'])
        w.writerows(gps_rows)
    print(f"GPS summary → {out_dir}/gps_hits.tsv")

print(f"Done. metadata_updated={meta_count}  gps_hits={len(gps_rows)}  "
      f"total_findings={len(findings)}")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="exiftool enrichment failed or incomplete; check log"
}

record_scan_end "$db" "$run_id" "$status" "$notes"
