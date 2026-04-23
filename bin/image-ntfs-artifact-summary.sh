#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-ntfs-artifact-summary.sh <db-path>

Builds a reviewable NTFS/Windows artifact bundle from bulk_extractor raw output.
This does not delete or alter the bulk_extractor source files.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

need_cmd python3

export_root="$(db_image_export_root "$db")"
src="$export_root/indexes/bulk_extractor_raw"
[[ -d "$src" ]] || die "raw bulk_extractor output not found: $src"

out_dir="$export_root/recovered/ntfs-artifacts"
log_path="$export_root/logs/ntfs-artifact-summary.log"
mkdir -p "$out_dir"

run_id="$(record_scan_start "$db" "ntfs-artifact-summary" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

{
  python3 - "$src" "$out_dir" <<'PY'
import csv
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

src = Path(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)

feature_names = [
    "ntfsmft_carved.txt",
    "ntfsindx_carved.txt",
    "ntfslogfile_carved.txt",
    "ntfsusn_carved.txt",
    "windirs.txt",
    "winlnk.txt",
    "winpe.txt",
    "winpe_carved.txt",
    "winprefetch.txt",
]

interest_re = re.compile(
    r"(?i)(wallet|bitcoin|btc|electrum|armory|multibit|seed|mnemonic|key|private|"
    r"photo|picture|image|jpg|jpeg|png|gif|bmp|tif|heic|camera|iphone|android|"
    r"document|invoice|tax|bank|passport|backup|desktop|downloads|pictures|users)"
)
path_re = re.compile(r"(?i)([A-Z]:\\\\[^\\t\\r\\n]+|\\\\Users\\\\[^\\t\\r\\n]+|Users\\\\[^\\t\\r\\n]+)")

summary_rows = []
all_hits_path = out / "interesting-lines.tsv"
paths_path = out / "possible-windows-paths.tsv"

with all_hits_path.open("w", encoding="utf-8", newline="") as hit_f, paths_path.open("w", encoding="utf-8", newline="") as path_f:
    hit_w = csv.writer(hit_f, delimiter="\t")
    path_w = csv.writer(path_f, delimiter="\t")
    hit_w.writerow(["feature_file", "line_number", "offset_ref", "value", "context"])
    path_w.writerow(["feature_file", "line_number", "path_fragment"])

    for name in feature_names:
        path = src / name
        if not path.exists():
            summary_rows.append([name, "missing", 0, 0, 0])
            continue

        shutil.copy2(path, out / name)
        total = interesting = path_hits = 0
        top_values = Counter()

        with path.open("r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                if line.startswith("#"):
                    continue
                total += 1
                parts = line.rstrip("\n").split("\t")
                offset = parts[0] if parts else ""
                value = parts[1] if len(parts) > 1 else ""
                context = parts[2] if len(parts) > 2 else ""
                if value:
                    top_values[value[:300]] += 1
                text = line.rstrip("\n")
                if interest_re.search(text):
                    interesting += 1
                    hit_w.writerow([name, lineno, offset, value, context])
                for match in path_re.finditer(text):
                    path_hits += 1
                    path_w.writerow([name, lineno, match.group(0)])

        with (out / f"{name}.top-values.tsv").open("w", encoding="utf-8", newline="") as top_f:
            top_w = csv.writer(top_f, delimiter="\t")
            top_w.writerow(["count", "value_prefix"])
            for value, count in top_values.most_common(200):
                top_w.writerow([count, value])

        summary_rows.append([name, "present", total, interesting, path_hits])

with (out / "summary.tsv").open("w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["feature_file", "status", "data_lines", "interesting_lines", "path_fragments"])
    w.writerows(summary_rows)

with (out / "README.md").open("w", encoding="utf-8") as f:
    f.write("# NTFS / Windows Artifact Summary\n\n")
    f.write("Source: bulk_extractor raw feature files.\n\n")
    f.write("Start with:\n")
    f.write("- summary.tsv\n")
    f.write("- interesting-lines.tsv\n")
    f.write("- possible-windows-paths.tsv\n")
    f.write("- ntfsusn_carved.txt and ntfsindx_carved.txt for prior Windows filenames/history\n")
    f.write("- winprefetch.txt and winlnk.txt for application execution and shortcut residue\n")

print(f"Wrote NTFS artifact summary to {out}")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="NTFS artifact summary failed; review log"
}

register_artifacts_from_dir "$db" "ntfs-artifacts" "$out_dir" "$run_id" 2>&1 | tee -a "$log_path" || {
  status="partial"
  notes="${notes:+$notes; }artifact registration failed after NTFS summary output was created"
}

record_scan_end "$db" "$run_id" "$status" "$notes"
