#!/usr/bin/env python3
"""
image-ocr-seed-scan.py - OCR recovered images and detect BIP39 seed phrases.

Usage:
  image-ocr-seed-scan.py <db> [--dir <dir>] [--min-words <n>] [--wordlist <path>]

Reads picture_candidates from the SQLite DB (joined with recovered_artifacts
to get actual file paths), runs Tesseract OCR on each image, and flags any
image whose OCR text contains a run of >= min-words consecutive BIP39 words.

Results are written to:
  <export_root>/hits/ocr-seeds/<timestamp>/hits.tsv
  <export_root>/hits/ocr-seeds/<timestamp>/summary.txt

A scan_runs row is written for tracking, and high-confidence hits (>= 12 words)
are appended to the notes table.
"""

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from lib.seed_scan import best_match, load_wordlist, tokenize_text  # noqa: E402
from lib.timestamp import utc_now  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────

def ocr_image(image_path: str) -> str:
    """Return plain-text OCR output from tesseract. Empty string on failure."""
    try:
        result = subprocess.run(
            ["tesseract", image_path, "stdout", "--psm", "6"],
            capture_output=True, text=True, timeout=60,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def best_bip39_run(text: str, bip39: set[str]) -> tuple[int, list[str]]:
    """Return (max_consecutive_bip39_words, word_list_for_best_run)."""
    match = best_match(text, min_words=1, wordlist=bip39)
    if not match:
        return 0, []
    return match.run_len, match.words


# ── Database helpers ──────────────────────────────────────────────────────────

def record_scan_start(conn: sqlite3.Connection, cmdline: str, log_path: str, output_dir: str) -> int:
    cur = conn.execute(
        "INSERT INTO scan_runs(stage,status,started_at,command_line,log_path,output_dir) "
        "VALUES('ocr-seed-scan','running',?,?,?,?)",
        (utc_now(), cmdline, log_path, output_dir),
    )
    conn.commit()
    return cur.lastrowid


def record_scan_end(conn: sqlite3.Connection, run_id: int, status: str, notes: str = "") -> None:
    conn.execute(
        "UPDATE scan_runs SET status=?, ended_at=?, notes=? WHERE id=?",
        (status, utc_now(), notes, run_id),
    )
    conn.commit()


def add_note(conn: sqlite3.Connection, note: str) -> None:
    conn.execute("INSERT INTO notes(created_at, note) VALUES(?,?)", (utc_now(), note))
    conn.commit()


# ── Image source ──────────────────────────────────────────────────────────────

def images_from_db(conn: sqlite3.Connection) -> list[str]:
    """
    Return full_path for all recovered_artifacts that look like images,
    ordered by size ascending (small files first — faster to OCR).
    """
    rows = conn.execute(
        """
        SELECT ra.full_path
        FROM recovered_artifacts ra
        WHERE (
            lower(ra.mime_type) LIKE 'image/%'
            OR lower(ra.relative_path) LIKE '%.jpg'
            OR lower(ra.relative_path) LIKE '%.jpeg'
            OR lower(ra.relative_path) LIKE '%.png'
            OR lower(ra.relative_path) LIKE '%.gif'
            OR lower(ra.relative_path) LIKE '%.webp'
            OR lower(ra.relative_path) LIKE '%.bmp'
            OR lower(ra.relative_path) LIKE '%.tif'
            OR lower(ra.relative_path) LIKE '%.tiff'
            OR lower(ra.relative_path) LIKE '%.heic'
        )
        ORDER BY ra.size_bytes ASC NULLS LAST
        """
    ).fetchall()
    return [r[0] for r in rows if r[0] and os.path.isfile(r[0])]


def images_from_dir(directory: str) -> list[str]:
    exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic"}
    paths = []
    for root, _, files in os.walk(directory):
        for name in files:
            if Path(name).suffix.lower() in exts:
                paths.append(os.path.join(root, name))
    return sorted(paths)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", help="Path to the image's .analysis.sqlite database")
    ap.add_argument("--dir", help="Scan this directory instead of DB picture_candidates")
    ap.add_argument("--min-words", type=int, default=6,
                    help="Minimum consecutive BIP39 words to flag (default: 6)")
    ap.add_argument("--wordlist", help="Path to BIP39 wordlist (one word per line)")
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        sys.exit(f"database not found: {args.db}")

    bip39 = load_wordlist(args.wordlist)
    if not bip39:
        sys.exit("BIP39 wordlist not found. Run inside the Docker container or pass --wordlist.")
    conn = sqlite3.connect(args.db)

    export_root = conn.execute(
        "SELECT export_root FROM image_info WHERE id=1"
    ).fetchone()
    if not export_root:
        sys.exit("image_info row not found — run image-analysis-init.sh first")
    export_root = export_root[0]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = os.path.join(export_root, "hits", "ocr-seeds", timestamp)
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(export_root, "logs", f"ocr-seed-scan-{timestamp}.log")
    hits_tsv = os.path.join(out_dir, "hits.tsv")
    summary_txt = os.path.join(out_dir, "summary.txt")

    cmdline = " ".join(sys.argv)
    run_id = record_scan_start(conn, cmdline, log_path, out_dir)

    if args.dir:
        images = images_from_dir(args.dir)
        print(f"Scanning {len(images)} images from {args.dir}")
    else:
        images = images_from_db(conn)
        print(f"Scanning {len(images)} images from recovered_artifacts")

    total = len(images)
    hits: list[dict] = []
    status = "ok"

    with open(hits_tsv, "w") as tsv, open(log_path, "w") as log:
        tsv.write("score\tmax_run\twords_found\timage_path\tbest_sequence\n")

        for i, img_path in enumerate(images, 1):
            print(f"[{i}/{total}] {img_path}", file=log, flush=True)
            print(f"[{i}/{total}] {os.path.basename(img_path)}", flush=True)

            text = ocr_image(img_path)
            if not text.strip():
                continue

            run_len, run_words = best_bip39_run(text, bip39)
            if run_len < args.min_words:
                continue

            score = min(100, run_len * 4)
            entry = {
                "score": score,
                "max_run": run_len,
                "words_found": len([t.value for t in tokenize_text(text) if t.value in bip39]),
                "image_path": img_path,
                "best_sequence": " ".join(run_words),
            }
            hits.append(entry)
            tsv.write(
                f"{score}\t{run_len}\t{entry['words_found']}\t{img_path}\t{entry['best_sequence']}\n"
            )
            tsv.flush()

            msg = f"HIT score={score} run={run_len}: {img_path} → {entry['best_sequence']}"
            print(msg, file=log, flush=True)
            print(msg, flush=True)

            if run_len >= 12:
                add_note(conn, f"ocr-seed-scan: probable seed phrase (run={run_len}) in {img_path}: {entry['best_sequence']}")

    # ── Summary ───────────────────────────────────────────────────────────────
    hits.sort(key=lambda h: h["score"], reverse=True)
    with open(summary_txt, "w") as f:
        f.write(f"OCR seed scan — {timestamp}\n")
        f.write(f"Images scanned : {total}\n")
        f.write(f"Hits (>= {args.min_words} BIP39 words) : {len(hits)}\n")
        f.write(f"High-confidence (>= 12 words) : {sum(1 for h in hits if h['max_run'] >= 12)}\n\n")
        for h in hits[:50]:
            f.write(f"  [{h['score']:3d}] run={h['max_run']} {h['image_path']}\n")
            f.write(f"         {h['best_sequence']}\n")

    print(f"\nDone. {len(hits)} hits out of {total} images.")
    print(f"Results: {hits_tsv}")
    print(f"Summary: {summary_txt}")

    record_scan_end(conn, run_id, status,
                    f"{len(hits)} hits from {total} images; min_words={args.min_words}")
    conn.close()


if __name__ == "__main__":
    main()
