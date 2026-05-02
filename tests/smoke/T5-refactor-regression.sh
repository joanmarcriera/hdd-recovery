#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
WORK_DIR="${WORK_DIR:-/tmp/hdd-recovery-t5-regression}"
PDF_FIXTURE="${PDF_FIXTURE:-}"
OCR_IMAGE_FIXTURE="${OCR_IMAGE_FIXTURE:-}"
WORDLIST="${WORDLIST:-}"

cat <<'EOF'
T5 refactor regression smoke test

Compares pre-refactor and current image-pdf-extract.sh / image-ocr-seed-scan.py
on fixed owner-provided fixtures.

Required environment:
  PDF_FIXTURE=/path/to/seed.pdf
  OCR_IMAGE_FIXTURE=/path/to/seed-image.png
  WORDLIST=/path/to/bip39-english.txt
EOF

command -v pdftotext >/dev/null || { echo "pdftotext missing" >&2; exit 2; }
command -v tesseract >/dev/null || { echo "tesseract missing" >&2; exit 2; }
[[ -f "$PDF_FIXTURE" && -f "$OCR_IMAGE_FIXTURE" && -f "$WORDLIST" ]] || {
  echo "PDF_FIXTURE, OCR_IMAGE_FIXTURE, and WORDLIST must point to files" >&2
  exit 2
}

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR/old" "$WORK_DIR/new"
git -C "$ROOT_DIR" show HEAD~1:bin/image-pdf-extract.sh > "$WORK_DIR/old/image-pdf-extract.sh"
git -C "$ROOT_DIR" show HEAD~1:bin/image-ocr-seed-scan.py > "$WORK_DIR/old/image-ocr-seed-scan.py"
chmod +x "$WORK_DIR/old/image-pdf-extract.sh" "$WORK_DIR/old/image-ocr-seed-scan.py"

setup_db() {
  local db="$1" export_root="$2"
  mkdir -p "$export_root/recovered" "$export_root/logs" "$export_root/hits"
  sqlite3 "$db" < "$ROOT_DIR/sql/analysis-schema.sql" >/dev/null
  sqlite3 "$db" <<SQL
INSERT INTO image_info(id,image_path,image_name,image_basename,export_root,created_at,updated_at)
VALUES(1,'$WORK_DIR/test.img','test.img','test','$export_root',datetime('now'),datetime('now'));
INSERT INTO recovered_artifacts(method,relative_path,full_path,mime_type,created_at)
VALUES
  ('smoke','seed.pdf','$PDF_FIXTURE','application/pdf',datetime('now')),
  ('smoke','seed.png','$OCR_IMAGE_FIXTURE','image/png',datetime('now'));
SQL
}

truncate -s 1M "$WORK_DIR/test.img"
setup_db "$WORK_DIR/old.sqlite" "$WORK_DIR/old/export"
setup_db "$WORK_DIR/new.sqlite" "$WORK_DIR/new/export"

"$WORK_DIR/old/image-pdf-extract.sh" "$WORK_DIR/old.sqlite" --wordlist "$WORDLIST"
"$ROOT_DIR/bin/image-pdf-extract.sh" "$WORK_DIR/new.sqlite" --wordlist "$WORDLIST"
"$WORK_DIR/old/image-ocr-seed-scan.py" "$WORK_DIR/old.sqlite" --wordlist "$WORDLIST"
"$ROOT_DIR/bin/image-ocr-seed-scan.py" "$WORK_DIR/new.sqlite" --wordlist "$WORDLIST"

old_pdf="$(find "$WORK_DIR/old/export/hits/pdf-seeds" -name hits.tsv -print -quit)"
new_pdf="$(find "$WORK_DIR/new/export/hits/pdf-seeds" -name hits.tsv -print -quit)"
old_ocr="$(find "$WORK_DIR/old/export/hits/ocr-seeds" -name hits.tsv -print -quit)"
new_ocr="$(find "$WORK_DIR/new/export/hits/ocr-seeds" -name hits.tsv -print -quit)"

diff -u "$old_pdf" "$new_pdf"
diff -u "$old_ocr" "$new_ocr"
echo "PASS: refactor outputs match on supplied fixtures"
