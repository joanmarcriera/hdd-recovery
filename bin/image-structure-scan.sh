#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-structure-scan.sh <db-path> [--force]

Runs read-only structure discovery against the image recorded in the database.
Writes raw tool outputs under the per-image structure directory and updates
partitions/filesystems in SQLite.
EOF
}

db="${1:-}"
force=0
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) force=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

need_cmd fdisk
need_cmd parted
need_cmd mmls
need_cmd img_stat
need_cmd blkid

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
structure_dir="$export_root/structure"
log_path="$export_root/logs/structure-scan.log"
mkdir -p "$structure_dir"

run_id="$(record_scan_start "$db" "structure-scan" "$0 $db" "$log_path" "$structure_dir")"

existing_files="$(db_value "$db" "SELECT COUNT(*) FROM files WHERE partition_id IS NOT NULL;")"
if [[ "${existing_files:-0}" -gt 0 && "$force" -ne 1 ]]; then
  record_scan_end "$db" "$run_id" "skipped" "refused to rescan structure because indexed files still reference partitions; rerun with --force if you want to rebuild partition provenance"
  die "structure scan would orphan partition provenance for $existing_files indexed file rows; rerun with --force if intentional"
fi

{
  log "Scanning structure for $image"
  fdisk -l "$image" > "$structure_dir/fdisk.txt" 2>&1 || true
  sfdisk -d "$image" > "$structure_dir/sfdisk.txt" 2>&1 || true
  parted -s -m "$image" unit s print > "$structure_dir/parted-machine.txt" 2>&1 || true
  parted -s "$image" unit s print > "$structure_dir/parted-human.txt" 2>&1 || true
  mmls "$image" > "$structure_dir/mmls.txt" 2>&1 || true
  img_stat "$image" > "$structure_dir/img_stat.txt" 2>&1 || true
  blkid -p -o export "$image" > "$structure_dir/blkid-export.txt" 2>&1 || true
} 2>&1 | tee "$log_path"

python3 - "$db" "$structure_dir" <<'PY'
import sqlite3
import sys

db_path, structure_dir = sys.argv[1:]
conn = sqlite3.connect(db_path)
conn.execute("DELETE FROM filesystems")
conn.execute("DELETE FROM partitions")

sector_size = 512
table_type = ""

parted_machine = f"{structure_dir}/parted-machine.txt"
try:
    with open(parted_machine, "r", encoding="utf-8", errors="replace") as f:
        lines = [line.strip() for line in f if line.strip()]
    if len(lines) >= 2 and lines[0] == "BYT;":
        disk_parts = lines[1].split(":")
        if len(disk_parts) >= 6:
            try:
                sector_size = int(disk_parts[3])
            except ValueError:
                sector_size = 512
            table_type = disk_parts[5]
        for line in lines[2:]:
            cols = line.split(":")
            if len(cols) < 5:
                continue
            slot = cols[0]
            start = cols[1].rstrip("s")
            end = cols[2].rstrip("s")
            length = cols[3].rstrip("s")
            fs_hint = cols[4] or None
            description = cols[5] if len(cols) > 5 else None
            conn.execute(
                """
                INSERT INTO partitions(slot,start_sector,end_sector,length_sectors,sector_size,table_type,description,filesystem_hint)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (slot, int(start), int(end), int(length), sector_size, table_type, description, fs_hint),
            )
except FileNotFoundError:
    pass

if conn.execute("SELECT COUNT(*) FROM partitions").fetchone()[0] == 0:
    fs_hint = None
    label = None
    blkid_export = f"{structure_dir}/blkid-export.txt"
    try:
        with open(blkid_export, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("TYPE="):
                    fs_hint = line.split("=", 1)[1].strip()
                elif line.startswith("LABEL="):
                    label = line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    conn.execute(
        """
        INSERT INTO partitions(slot,start_sector,end_sector,length_sectors,sector_size,table_type,description,filesystem_hint,mount_role)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        ("whole-image", 0, None, None, sector_size, "unknown", "single filesystem or unparsed image", fs_hint, "whole-image"),
    )
    part_id = conn.execute("SELECT id FROM partitions ORDER BY id DESC LIMIT 1").fetchone()[0]
    if fs_hint or label:
        conn.execute(
            "INSERT INTO filesystems(partition_id,fs_type,label,block_size,offset_sectors,source,notes) VALUES(?,?,?,?,?,?,?)",
            (part_id, fs_hint, label, None, 0, "structure-scan", "Detected from blkid on whole image"),
        )

for row in conn.execute("SELECT id, start_sector, filesystem_hint FROM partitions ORDER BY id"):
    part_id, start_sector, fs_hint = row
    conn.execute(
        """
        INSERT INTO filesystems(partition_id,fs_type,label,block_size,offset_sectors,source,notes)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT DO NOTHING
        """,
        (part_id, fs_hint, None, None, start_sector or 0, "structure-scan", "Seeded from partition layout"),
    )

conn.commit()
conn.close()
PY

record_scan_end "$db" "$run_id" "ok"
printf 'Structure outputs: %s\n' "$structure_dir"
