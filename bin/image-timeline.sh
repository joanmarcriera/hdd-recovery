#!/usr/bin/env bash
# Generate a unified timeline from a recovery SQLite database.
# Combines scan stage events, filesystem file timestamps, and artifact discovery.
# Output: JSON (default), CSV (--csv), or human-readable table (--table)
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-timeline.sh <db-path> [options]

Options:
  --format json|csv|table   Output format (default: json)
  --out FILE                Write to FILE instead of stdout
  --since DATETIME          Filter events on or after DATETIME (ISO 8601)
  --until DATETIME          Filter events before or on DATETIME (ISO 8601)
  -h, --help                Show this help

Sources combined into the timeline:
  scan_runs       — stage start/end events (when each analysis step ran)
  files           — filesystem file timestamps (mtime, crtime) from TSK index
  recovered_artifacts — when each artifact was registered into the DB
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
shift

FORMAT="json"
OUTFILE=""
SINCE=""
UNTIL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --format) FORMAT="$2"; shift 2 ;;
    --out)    OUTFILE="$2"; shift 2 ;;
    --since)  SINCE="$2";  shift 2 ;;
    --until)  UNTIL="$2";  shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$FORMAT" in
  json|csv|table) ;;
  *) die "unknown format: $FORMAT (use json, csv, or table)" ;;
esac

need_cmd python3

python3 - "$db" "$FORMAT" "$OUTFILE" "$SINCE" "$UNTIL" <<'PYEOF'
import sys, sqlite3, json, csv, io, os
from datetime import datetime, timezone

db_path, fmt, outfile, since_str, until_str = sys.argv[1:6]

def parse_dt(s):
    if not s:
        return None
    for pat in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, pat).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise SystemExit(f"Cannot parse datetime: {s!r}")

since_dt = parse_dt(since_str) if since_str else None
until_dt = parse_dt(until_str) if until_str else None

def ts_to_iso(ts):
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OSError, OverflowError, ValueError):
            return None
    s = str(ts).strip()
    if not s:
        return None
    # already ISO-ish string
    return s if "T" in s or "-" in s else None

def in_range(iso_str):
    if iso_str is None:
        return False
    try:
        dt = datetime.fromisoformat(iso_str.rstrip("Z")).replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    if since_dt and dt < since_dt:
        return False
    if until_dt and dt > until_dt:
        return False
    return True

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

events = []

# — scan_runs: stage start and end events —
try:
    for row in conn.execute("""
        SELECT stage, status, started_at, ended_at, command_line, log_path, output_dir
        FROM scan_runs ORDER BY started_at
    """):
        for ts, ev_type in [(row["started_at"], "stage_start"), (row["ended_at"], "stage_end")]:
            iso = ts_to_iso(ts)
            if iso is None:
                continue
            if not in_range(iso):
                continue
            events.append({
                "timestamp": iso,
                "event_type": ev_type,
                "source": "scan_runs",
                "label": f"{ev_type.replace('_', ' ')}: {row['stage']}",
                "detail": {
                    "stage": row["stage"],
                    "status": row["status"],
                    "command": row["command_line"],
                    "log": row["log_path"],
                    "output_dir": row["output_dir"],
                },
            })
except sqlite3.OperationalError:
    pass  # scan_runs not yet created

# — files: mtime and crtime from TSK index —
try:
    for row in conn.execute("""
        SELECT name, path, size, mtime, crtime, fs_type, alloc
        FROM files
        WHERE mtime IS NOT NULL OR crtime IS NOT NULL
        LIMIT 50000
    """):
        for ts_val, ts_label in [(row["mtime"], "file_mtime"), (row["crtime"], "file_crtime")]:
            iso = ts_to_iso(ts_val)
            if iso is None:
                continue
            if not in_range(iso):
                continue
            events.append({
                "timestamp": iso,
                "event_type": ts_label,
                "source": "files",
                "label": f"{ts_label}: {row['path'] or row['name']}",
                "detail": {
                    "name": row["name"],
                    "path": row["path"],
                    "size": row["size"],
                    "fs_type": row["fs_type"],
                    "alloc": row["alloc"],
                },
            })
except sqlite3.OperationalError:
    pass  # files table not yet created

# — recovered_artifacts: when each artifact was registered —
try:
    for row in conn.execute("""
        SELECT method, original_path, sha256, size_bytes, mime_type, created_at
        FROM recovered_artifacts
        WHERE created_at IS NOT NULL
        ORDER BY created_at
    """):
        iso = ts_to_iso(row["created_at"])
        if iso is None:
            continue
        if not in_range(iso):
            continue
        events.append({
            "timestamp": iso,
            "event_type": "artifact_registered",
            "source": "recovered_artifacts",
            "label": f"artifact: {os.path.basename(row['original_path'] or '')} [{row['method']}]",
            "detail": {
                "method": row["method"],
                "path": row["original_path"],
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
                "mime_type": row["mime_type"],
            },
        })
except sqlite3.OperationalError:
    pass

conn.close()

events.sort(key=lambda e: e["timestamp"] or "")

# — output —
out = sys.stdout if not outfile else open(outfile, "w")

if fmt == "json":
    json.dump(events, out, indent=2)
    out.write("\n")

elif fmt == "csv":
    writer = csv.writer(out)
    writer.writerow(["timestamp", "event_type", "source", "label"])
    for e in events:
        writer.writerow([e["timestamp"], e["event_type"], e["source"], e["label"]])

elif fmt == "table":
    col_ts   = max((len(e["timestamp"]) for e in events), default=19)
    col_type = max((len(e["event_type"]) for e in events), default=10)
    col_src  = max((len(e["source"]) for e in events), default=6)
    fmt_line = f"{{:<{col_ts}}}  {{:<{col_type}}}  {{:<{col_src}}}  {{}}"
    out.write(fmt_line.format("TIMESTAMP", "EVENT_TYPE", "SOURCE", "LABEL") + "\n")
    out.write("-" * (col_ts + col_type + col_src + len("LABEL") + 6) + "\n")
    for e in events:
        out.write(fmt_line.format(
            e["timestamp"], e["event_type"], e["source"],
            e["label"][:100]
        ) + "\n")

if outfile and out is not sys.stdout:
    out.close()

sys.exit(0)
PYEOF
