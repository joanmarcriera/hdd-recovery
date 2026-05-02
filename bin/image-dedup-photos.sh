#!/usr/bin/env bash
# Group near-duplicate recovered photos by perceptual hash. This only updates
# recovered_artifacts metadata and findings; it never deletes or moves files.
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-dedup-photos.sh <db-path> [--distance <n>] [--force] [--run]

Default is dry-run. Pass --run to compute pHash clusters and update SQLite.

Updates recovered_artifacts:
  dedup_cluster_id
  is_cluster_primary

Cluster primary selection:
  largest size_bytes, then highest quality_score, then lowest id.
EOF
}

db="${1:-}"
distance=10
force=0
run=0
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --distance) distance="$2"; shift 2 ;;
    --force) force=1; shift ;;
    --run) run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd python3

export_root="$(db_image_export_root "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="$export_root/hits/dedup-photos/$timestamp"
log_path="$export_root/logs/dedup-photos-$timestamp.log"
mkdir -p "$out_dir" "$(dirname "$log_path")"

candidate_count="$(sqlite3 -noheader "$db" "
SELECT COUNT(*)
FROM recovered_artifacts
WHERE (
    lower(COALESCE(mime_type,'')) LIKE 'image/%'
    OR lower(relative_path) GLOB '*.jpg'
    OR lower(relative_path) GLOB '*.jpeg'
    OR lower(relative_path) GLOB '*.png'
    OR lower(relative_path) GLOB '*.webp'
    OR lower(relative_path) GLOB '*.bmp'
    OR lower(relative_path) GLOB '*.tif'
    OR lower(relative_path) GLOB '*.tiff'
  )
  AND ($force = 1 OR dedup_cluster_id IS NULL);
")"

if [[ "$run" -ne 1 ]]; then
  printf 'DRY RUN: photo dedup only. Re-run with --run to execute.\n'
  printf 'Candidates: %s\n' "$candidate_count"
  printf 'Distance: %s\n' "$distance"
  printf 'Force: %s\n' "$force"
  exit 0
fi

run_id="$(record_scan_start "$db" "dedup-photos" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

{
PYTHONPATH="$ROOT_DIR" python3 - "$db" "$out_dir" "$distance" "$force" <<'PY'
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone

from PIL import Image
import imagehash

db_path, out_dir, distance_raw, force_raw = sys.argv[1:5]
distance = int(distance_raw)
force = force_raw == "1"
conn = sqlite3.connect(db_path)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

where = """
WHERE (
    lower(COALESCE(mime_type,'')) LIKE 'image/%'
    OR lower(relative_path) GLOB '*.jpg'
    OR lower(relative_path) GLOB '*.jpeg'
    OR lower(relative_path) GLOB '*.png'
    OR lower(relative_path) GLOB '*.webp'
    OR lower(relative_path) GLOB '*.bmp'
    OR lower(relative_path) GLOB '*.tif'
    OR lower(relative_path) GLOB '*.tiff'
)
"""
if not force:
    where += " AND dedup_cluster_id IS NULL"
rows = conn.execute(
    f"""
    SELECT id, full_path, COALESCE(size_bytes, 0), COALESCE(quality_score, 0)
    FROM recovered_artifacts
    {where}
    ORDER BY id
    """
).fetchall()
print(f"Image candidates: {len(rows)} distance={distance} force={force}")

if force:
    conn.execute(
        """
        UPDATE recovered_artifacts
        SET dedup_cluster_id = NULL, is_cluster_primary = 0
        WHERE lower(COALESCE(mime_type,'')) LIKE 'image/%'
           OR lower(relative_path) GLOB '*.jpg'
           OR lower(relative_path) GLOB '*.jpeg'
           OR lower(relative_path) GLOB '*.png'
           OR lower(relative_path) GLOB '*.webp'
           OR lower(relative_path) GLOB '*.bmp'
           OR lower(relative_path) GLOB '*.tif'
           OR lower(relative_path) GLOB '*.tiff'
        """
    )

items = []
for artifact_id, path, size_bytes, quality_score in rows:
    if not path or not os.path.isfile(path):
        continue
    try:
        with Image.open(path) as img:
            phash = imagehash.phash(img)
    except Exception as exc:
        print(f"skip artifact_id={artifact_id} path={path}: {exc}")
        continue
    items.append(
        {
            "id": artifact_id,
            "path": path,
            "size": int(size_bytes or 0),
            "quality": float(quality_score or 0),
            "hash": phash,
        }
    )

clusters: list[list[dict]] = []
for item in items:
    for cluster in clusters:
        if min(item["hash"] - other["hash"] for other in cluster) < distance:
            cluster.append(item)
            break
    else:
        clusters.append([item])

start_cluster = conn.execute("SELECT COALESCE(MAX(dedup_cluster_id), 0) + 1 FROM recovered_artifacts").fetchone()[0]
clusters_tsv = os.path.join(out_dir, "clusters.tsv")
with open(clusters_tsv, "w", newline="") as f:
    writer = csv.writer(f, delimiter="\t")
    writer.writerow(["cluster_id", "artifact_id", "is_primary", "size_bytes", "quality_score", "path"])
    for offset, cluster in enumerate(clusters):
        cluster_id = start_cluster + offset
        primary = sorted(cluster, key=lambda x: (-x["size"], -x["quality"], x["id"]))[0]
        for item in cluster:
            is_primary = 1 if item["id"] == primary["id"] else 0
            conn.execute(
                """
                UPDATE recovered_artifacts
                SET dedup_cluster_id = ?, is_cluster_primary = ?
                WHERE id = ?
                """,
                (cluster_id, is_primary, item["id"]),
            )
            writer.writerow([cluster_id, item["id"], is_primary, item["size"], item["quality"], item["path"]])
        conn.execute(
            """
            INSERT INTO findings(source_tool, category, key, value, score, notes, created_at)
            VALUES('imagehash', 'dedup', 'cluster_size', ?, 0, ?, ?)
            """,
            (str(len(cluster)), f"dedup_cluster_id={cluster_id} primary_artifact_id={primary['id']}", now),
        )

conn.commit()
conn.close()
print(f"Clusters: {len(clusters)}")
print(f"Results: {clusters_tsv}")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="photo dedup failed or incomplete; check log"
}

if [[ -z "$notes" ]]; then
  assigned="$(sqlite3 -noheader "$db" "SELECT COUNT(*) FROM recovered_artifacts WHERE dedup_cluster_id IS NOT NULL;")"
  notes="dedup assigned=$assigned distance=$distance force=$force"
fi
record_scan_end "$db" "$run_id" "$status" "$notes"
