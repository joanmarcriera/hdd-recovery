# Tool Selection For Image Analysis

## Start here

- `img_stat`, `parted`, `fdisk`, `mmls`
  Use first to understand whether the image is a whole disk, a single filesystem, or something damaged.

- `fiwalk`, `fls`, `icat`, `fsstat`
  Use next when you want filesystem-aware analysis and extraction with path and timestamp context.

Recommended sequence:
1. structure tools
2. filesystem-aware tools
3. wallet/picture detection
4. ext-specific recovery if applicable
5. carving
6. raw-image triage and optional full-text indexing

## Read-only exposure

- `losetup -r --show -fP`
  Use when you need the kernel to expose partitions.

- `mount -o ro,...`
  Use only when mounting is genuinely the simplest way to inspect something. The baseline workflow does not depend on mounts.

- `qemu-nbd`
  Useful alternative when loop handling is inconvenient.

## Deleted-file and damaged-filesystem stages

- `extundelete`, `ext4magic`
  Targeted ext3/ext4 recovery passes when journalling metadata may still help.

- `testdisk`
  Use when partition structure itself is damaged or ambiguous.

## Carving

- `photorec`
  Broad and powerful. Good when the filesystem is badly damaged. Weak at preserving original filenames and paths.

- `scalpel`
  Better when you want a tighter signature set and more controlled carve scope.

- `foremost`
  Straightforward broad carve. Good complementary pass.

## Raw-image triage

- `bulk_extractor`
  Use on the raw image when you want strings and feature extraction independent of the filesystem.

## Search and review

- SQLite per-image catalog
  Primary query layer for this workflow.

- `recoll`
  Optional full-text layer over recovered outputs, useful after you already have a recovered corpus.

- `Autopsy`
  Optional/manual only. Do not make the core workflow depend on Kali's packaged version.
