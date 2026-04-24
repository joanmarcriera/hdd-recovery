# Picture Recovery

Goal: recover intact pictures first, then carve for deleted or pathless media.

## Default order

1. Filesystem-aware inventory
   - `image-index-tsk.sh <db>`
   - `image-detect-pictures.sh <db>`

2. Review likely photo paths
   - `image-query.sh <db> pictures`
   - prioritize paths under `DCIM`, `Pictures`, `Photos`, and camera import directories

3. Export targeted hits
   - `image-export.sh <db> --file-id <id>`

4. Run carving when needed
   - `image-carve.sh <db> --method foremost`
   - `image-carve.sh <db> --method scalpel`
   - `image-photorec-run.sh <db> --profile broad`

5. Run OCR seed phrase scan on recovered images (run in TrueNAS container)
   - `bin/image-ocr-seed-scan.py <db>`
   - Run after carving so that carved images are registered in `recovered_artifacts`.
   - OCRs all recovered images in the DB and scans for runs of BIP-39 words.
   - Flags any image with >= 6 consecutive BIP-39 words; high-confidence hits (>= 12) are written to `notes`.
   - BIP-39 wordlist is at `/usr/local/share/bip39-english.txt` inside the container.
   - Results land in `<export_root>/hits/ocr-seeds/<timestamp>/hits.tsv` and `summary.txt`.
   - Use `--dir <path>` to scan a specific directory instead of the DB.

6. Optional enrichment
   - run `exiftool` or `identify` on exported or carved images
   - build thumbnails or contact sheets later if needed

## Notes

- Filesystem-aware recovery preserves filenames and directory structure.
- Carving is more likely to find deleted images but usually loses original context.
- Expect duplicates across methods. Keep provenance and dedupe later.
- PhotoRec is usually the highest-value broad picture/document carver. Its
  output is intentionally separate under `recovered/photorec/<profile>-<timestamp>`.
