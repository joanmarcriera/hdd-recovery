# Bitcoin Wallet Recovery

Goal: maximize practical recovery of wallet-related artifacts while preserving provenance.

## Detection order

1. Filesystem-aware inventory
   - look for `wallet.dat`
   - look for wallet-related directory names such as `.bitcoin`, `Electrum`, `Armory`, `MultiBit`, `keystore`
   - look for likely extensions such as `.dat`, `.json`, `.db`, `.sqlite`

2. Raw-image triage
   - run `image-bulk-extractor.sh <db> --scope raw`
   - review feature files for address-like strings, URLs, emails, JSON fragments, and text clues

3. Carving
   - run carving when intact paths are missing or the filesystem is badly damaged

4. Targeted export
   - use `image-query.sh <db> wallets`
   - export specific file ids with `image-export.sh`

## Important caveats

- Filename/path hits are candidates, not proof.
- Many false positives are expected around generic terms like `wallet`, `seed`, or `backup`.
- Terms like `seeds` can belong to unrelated applications such as eMule/aMule peer-source metadata.
- Seed phrase recovery is often content-driven, not filename-driven. That is why `bulk_extractor` and optional Recoll indexing matter.
- Do not run password-cracking tools by default. Treat `bruteforce-wallet` as a separate deliberate step after extraction.
