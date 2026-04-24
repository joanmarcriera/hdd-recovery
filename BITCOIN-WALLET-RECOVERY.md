# Bitcoin Wallet Recovery

Goal: maximize practical recovery of wallet-related artifacts while preserving provenance.

## Architecture note

Imaging runs on the Optiplex (Kali Linux). After ddrescue completes, run `bin/image-analysis-init.sh` and `bin/image-structure-scan.sh` on the Optiplex, then transfer to TrueNAS with `bin/send-image-to-truenas.sh`. All stages below run inside the Docker container on TrueNAS (12 CPU, 96 GB RAM, RTX 4060). Access via browser terminal at `http://<truenas-ip>:7681` or `docker exec -it hdd-forensics bash`.

## Detection order

1. Filesystem-aware inventory (run on Optiplex before transfer, or in container)
   - look for `wallet.dat`
   - look for wallet-related directory names such as `.bitcoin`, `Electrum`, `Armory`, `MultiBit`, `keystore`
   - look for likely extensions such as `.dat`, `.json`, `.db`, `.sqlite`

2. Raw-image triage (run in TrueNAS container)
   - run `image-bulk-extractor.sh <db> --scope raw`
   - review feature files for address-like strings, URLs, emails, JSON fragments, and text clues

3. Ext-specific deleted-file recovery (run in TrueNAS container)
   - run `image-ext-recover.sh <db>` for ext3/ext4 partitions
   - surfaces deleted files and metadata not visible in the current filesystem view

4. Carving (run in TrueNAS container)
   - run carving when intact paths are missing or the filesystem is badly damaged
   - `image-carve.sh <db> --method foremost`
   - `image-carve.sh <db> --method scalpel`

5. Recovered-corpus triage (run in TrueNAS container)
   - run `image-bulk-extractor.sh <db> --scope recovered` after carving/ext recovery
   - indexes text artifacts across all recovered files

6. OCR seed phrase scan (run in TrueNAS container)
   - run `bin/image-ocr-seed-scan.py <db>` after recovered artifacts are registered
   - OCRs all recovered images and scans for consecutive BIP-39 words
   - flags any image with >= 6 consecutive BIP-39 words (configurable via `--min-words`)
   - high-confidence hits (>= 12 words) are also written to the `notes` table in the DB
   - BIP-39 wordlist is at `/usr/local/share/bip39-english.txt` inside the container
   - results written to `<export_root>/hits/ocr-seeds/<timestamp>/hits.tsv` and `summary.txt`

7. Targeted export
   - use `image-query.sh <db> wallets`
   - export specific file ids with `image-export.sh`

## Important caveats

- Filename/path hits are candidates, not proof.
- Many false positives are expected around generic terms like `wallet`, `seed`, or `backup`.
- Terms like `seeds` can belong to unrelated applications such as eMule/aMule peer-source metadata.
- Seed phrase recovery is often content-driven, not filename-driven. That is why `bulk_extractor` and optional Recoll indexing matter.
- Do not run password-cracking tools by default. Treat `bruteforce-wallet` as a separate deliberate step after extraction.
