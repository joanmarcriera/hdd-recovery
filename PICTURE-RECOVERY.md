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

5. Optional enrichment
   - run `exiftool` or `identify` on exported or carved images
   - build thumbnails or contact sheets later if needed

## Notes

- Filesystem-aware recovery preserves filenames and directory structure.
- Carving is more likely to find deleted images but usually loses original context.
- Expect duplicates across methods. Keep provenance and dedupe later.
- PhotoRec is usually the highest-value broad picture/document carver. Its
  output is intentionally separate under `recovered/photorec/<profile>-<timestamp>`.
