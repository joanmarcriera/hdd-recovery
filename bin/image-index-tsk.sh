#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-index-tsk.sh <db-path>

Runs fiwalk in metadata-first mode and imports the inventory into SQLite.
This stage preserves filesystem context better than carving and should run first.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

need_cmd fiwalk
need_cmd python3

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
index_dir="$export_root/indexes/tsk"
log_path="$export_root/logs/index-tsk.log"
xml_path="$index_dir/fiwalk.xml"
mkdir -p "$index_dir"

run_id="$(record_scan_start "$db" "index-tsk" "$0 $db" "$log_path" "$index_dir")"

{
  log "Running fiwalk metadata inventory for $image"
  fiwalk -g -z -x "$image" > "$xml_path"
} 2>&1 | tee "$log_path"

python3 - "$db" "$xml_path" <<'PY'
import sqlite3
import sys
import xml.etree.ElementTree as ET

db_path, xml_path = sys.argv[1:]
conn = sqlite3.connect(db_path)
conn.execute("DELETE FROM files WHERE source_tool='fiwalk'")

def local_name(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag

def txt(node, name):
    for child in list(node):
        if local_name(child.tag) == name:
            return child.text
    return None

def yesno(value):
    if value is None:
        return None
    v = value.strip().lower()
    if v in {"1", "y", "yes", "true"}:
        return 1
    if v in {"0", "n", "no", "false"}:
        return 0
    return None

tree = ET.parse(xml_path)
root = tree.getroot()

partition_map = {0: None}
for idx, row in enumerate(conn.execute("SELECT id, start_sector FROM partitions ORDER BY start_sector IS NULL, start_sector, id"), start=1):
    part_id, _ = row
    partition_map[idx] = part_id

for fileobj in root.iter():
    if local_name(fileobj.tag) != "fileobject":
        continue
    path = txt(fileobj, "filename")
    if not path:
        continue
    inode = txt(fileobj, "inode")
    size = txt(fileobj, "filesize")
    alloc = yesno(txt(fileobj, "alloc"))
    orphan = yesno(txt(fileobj, "orphan"))
    deleted = 1 if orphan == 1 else (0 if alloc == 1 else None)
    meta_type = txt(fileobj, "meta_type") or ""
    name_type = txt(fileobj, "name_type") or ""
    is_dir = 1 if ("dir" in meta_type.lower() or name_type.lower() == "d") else 0
    partition_hint = txt(fileobj, "partition")
    try:
        partition_id = partition_map.get(int(partition_hint), None) if partition_hint else None
    except ValueError:
        partition_id = None

    name = path.rstrip("/").split("/")[-1] if path else None
    ext = name.rsplit(".", 1)[1].lower() if name and "." in name else None

    conn.execute(
        """
        INSERT OR REPLACE INTO files(
          partition_id, source_tool, inode, path, name, extension,
          allocated, deleted, is_dir, size_bytes, uid, gid, mode,
          atime, mtime, ctime, crtime, md5, sha1, mime_type, notes
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
          partition_id,
          "fiwalk",
          inode,
          path,
          name,
          ext,
          alloc,
          deleted,
          is_dir,
          int(size) if size and size.isdigit() else None,
          txt(fileobj, "uid"),
          txt(fileobj, "gid"),
          txt(fileobj, "mode"),
          txt(fileobj, "atime"),
          txt(fileobj, "mtime"),
          txt(fileobj, "ctime"),
          txt(fileobj, "crtime"),
          txt(fileobj, "md5"),
          txt(fileobj, "sha1"),
          txt(fileobj, "libmagic"),
          "Imported from fiwalk metadata inventory",
        ),
    )

conn.commit()
conn.close()
PY

record_scan_end "$db" "$run_id" "ok"
printf 'TSK index XML: %s\n' "$xml_path"
