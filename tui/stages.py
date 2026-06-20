"""Stage definitions — one entry per workflow step."""
import re
from dataclasses import dataclass, field
from typing import Optional

# Textual 8.x markup parser rejects [link=https://...] because :// confuses it.
# Replace with "display_text [dim]url[/dim]" at render time.
_LINK_RE = re.compile(r'\[link=([^\]]+)\]([^\[]*)\[/link\]')


def clean_markup(text: str) -> str:
    """Strip [link=URL]text[/link] → 'text [dim]URL[/dim]' for Textual 8.x."""
    return _LINK_RE.sub(r'\2 [dim]\1[/dim]', text)


@dataclass
class StageDef:
    key: str
    number: int
    name: str
    description: str
    script: Optional[str]           # filename inside bin/; None = manual step
    args_template: list[str]        # supports {image} {db} {mapfile} {conf} {export_root}
    scan_run_key: Optional[str]     # matches scan_runs.stage; None = not DB-tracked
    pgrep_pattern: Optional[str]    # pattern(s) joined by | for pgrep -fa
    runtime_hint: str
    rerunnable: bool
    requires_db_write: bool
    warning: str = ""
    is_optional: bool = False
    is_manual: bool = False         # operator must act; TUI shows instructions, no exec
    is_view_only: bool = False      # read-only command; no DB/disk changes
    has_config_screen: bool = False # opens a config modal instead of ConfirmScreen
    requires_prior: list[str] = field(default_factory=list)
    auto_stdin: bytes = b""         # written to stdin after process starts (ddrescue --ask)


STAGES: list[StageDef] = [
    # ── Acquisition setup ─────────────────────────────────────────────────
    StageDef(
        key="identify-source",
        number=1,
        name="Identify Source Disk",
        description=(
            "Verify the source disk before touching anything.\n\n"
            "Run:\n"
            "  lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT,MODEL,SERIAL,ROTA,TRAN,STATE\n"
            "  ls -l /dev/disk/by-path\n\n"
            "Confirm: model, serial, size match expectation. "
            "Destination /mnt/recovery16tb is still mounted. "
            "No source partition is mounted. "
            "If anything is ambiguous, stop."
        ),
        script=None,
        args_template=[],
        scan_run_key=None,
        pgrep_pattern=None,
        runtime_hint="< 2 min",
        rerunnable=True,
        requires_db_write=False,
        is_manual=True,
        is_view_only=True,
    ),
    StageDef(
        key="create-job-config",
        number=2,
        name="Create Job Config",
        description=(
            "Copy the job template and fill in disk-specific values:\n\n"
            "  cp /root/hdd-recovery/bin/ddrescue-job-template.conf \\\n"
            "     /root/hdd-recovery/jobs/<name>.conf\n\n"
            "Required fields: JOB_NAME, SOURCE_DEV, SOURCE_BY_PATH, "
            "SOURCE_MODEL, SOURCE_SERIAL, SOURCE_SIZE_BYTES, BASENAME.\n\n"
            "Naming convention: YYYYMMDD_<Model>_<Serial>_<devname>"
        ),
        script=None,
        args_template=[],
        scan_run_key=None,
        pgrep_pattern=None,
        runtime_hint="< 5 min",
        rerunnable=True,
        requires_db_write=False,
        is_manual=True,
    ),
    # ── ddrescue imaging ──────────────────────────────────────────────────
    StageDef(
        key="ddrescue-preview",
        number=3,
        name="Preview ddrescue Command",
        description=(
            "Run ddrescue-run.sh in plan mode to confirm source device, "
            "image path, and map path before any real imaging starts. "
            "Nothing is written to disk.\n\n"
            "[link=https://www.gnu.org/software/ddrescue/]GNU ddrescue homepage[/link]"
        ),
        script="ddrescue-run.sh",
        args_template=["{conf}", "plan"],
        scan_run_key=None,
        pgrep_pattern=None,
        runtime_hint="< 5 sec",
        rerunnable=True,
        requires_db_write=False,
        is_view_only=True,
        requires_prior=["create-job-config"],
    ),
    StageDef(
        key="ddrescue-first",
        number=4,
        name="ddrescue First Pass",
        description=(
            "Easy-data-first imaging pass (ddrescue -n). "
            "Reads all easily accessible sectors without retry work. "
            "This is the primary imaging step — can take 2–24 hours "
            "depending on disk size and health.\n\n"
            "The TUI will automatically confirm the ddrescue --ask prompt.\n\n"
            "[link=https://www.gnu.org/software/ddrescue/]GNU ddrescue homepage[/link]"
        ),
        script="ddrescue-run.sh",
        args_template=["{conf}", "first", "--run"],
        scan_run_key=None,
        pgrep_pattern="ddrescue",
        runtime_hint="2 – 24 h",
        rerunnable=True,
        requires_db_write=False,
        warning="LONG-RUNNING. Do NOT run SMART self-tests during imaging. Prefer running inside tmux.",
        requires_prior=["create-job-config"],
        auto_stdin=b"y\n",
    ),
    StageDef(
        key="ddrescue-map-status",
        number=5,
        name="Check Map / Coverage",
        description=(
            "Display the current ddrescue map: total rescued, what remains unread. "
            "Use this to decide whether retry passes are needed.\n\n"
            "[link=https://www.gnu.org/software/ddrescue/]GNU ddrescue homepage[/link]"
        ),
        script="ddrescue-status.sh",
        args_template=["{mapfile}"],
        scan_run_key=None,
        pgrep_pattern=None,
        runtime_hint="< 5 sec",
        rerunnable=True,
        requires_db_write=False,
        is_view_only=True,
        requires_prior=["ddrescue-first"],
    ),
    StageDef(
        key="ddrescue-mapview",
        number=6,
        name="Detailed Map View",
        description=(
            "Detailed text breakdown of the ddrescue map: block regions, "
            "rescued vs unrescued sectors, error clusters.\n\n"
            "For a graphical view, run in a separate terminal:\n"
            "  ddrescueview <mapfile>\n\n"
            "[link=https://www.gnu.org/software/ddrescue/]GNU ddrescue homepage[/link]"
        ),
        script="image-mapview.sh",
        args_template=["{mapfile}"],
        scan_run_key=None,
        pgrep_pattern=None,
        runtime_hint="< 5 sec",
        rerunnable=True,
        requires_db_write=False,
        is_view_only=True,
        is_optional=True,
        requires_prior=["ddrescue-first"],
    ),
    StageDef(
        key="ddrescue-retry",
        number=7,
        name="ddrescue Retry Pass (optional)",
        description=(
            "Retry unread sectors using direct I/O and up to 3 attempts per block. "
            "Only useful if the first pass left unread areas (map coverage < 100%).\n\n"
            "[link=https://www.gnu.org/software/ddrescue/]GNU ddrescue homepage[/link]"
        ),
        script="ddrescue-run.sh",
        args_template=["{conf}", "retry", "--run"],
        scan_run_key=None,
        pgrep_pattern="ddrescue",
        runtime_hint="1 – 8 h",
        rerunnable=True,
        requires_db_write=False,
        warning="Only run if unread areas remain after the first pass.",
        is_optional=True,
        requires_prior=["ddrescue-first"],
        auto_stdin=b"y\n",
    ),
    StageDef(
        key="ddrescue-reverse",
        number=8,
        name="ddrescue Reverse Pass (optional)",
        description=(
            "Approach remaining bad sectors from the opposite direction.\n\n"
            "[link=https://www.gnu.org/software/ddrescue/]GNU ddrescue homepage[/link]"
        ),
        script="ddrescue-run.sh",
        args_template=["{conf}", "reverse", "--run"],
        scan_run_key=None,
        pgrep_pattern="ddrescue",
        runtime_hint="1 – 8 h",
        rerunnable=True,
        requires_db_write=False,
        is_optional=True,
        requires_prior=["ddrescue-retry"],
        auto_stdin=b"y\n",
    ),
    StageDef(
        key="ddrescue-retrim",
        number=9,
        name="ddrescue Retrim Pass (optional)",
        description=(
            "Mark failed blocks for retrim and make one final conservative attempt.\n\n"
            "[link=https://www.gnu.org/software/ddrescue/]GNU ddrescue homepage[/link]"
        ),
        script="ddrescue-run.sh",
        args_template=["{conf}", "retrim", "--run"],
        scan_run_key=None,
        pgrep_pattern="ddrescue",
        runtime_hint="1 – 4 h",
        rerunnable=True,
        requires_db_write=False,
        is_optional=True,
        requires_prior=["ddrescue-first"],
        auto_stdin=b"y\n",
    ),
    # ── safecopy (alternative imager) ─────────────────────────────────────
    StageDef(
        key="safecopy-stage1",
        number=10,
        name="Safecopy Stage 1 (optional)",
        description=(
            "Alternative imager for disks ddrescue cannot handle.\n\n"
            "Stage 1: fast pass — rescue most data, no retries, skip bad areas. "
            "Writes a badblocks file for stage 2.\n\n"
            "Use safecopy INSTEAD OF ddrescue when ddrescue exits immediately or "
            "makes no forward progress. Do not mix both on the same image.\n\n"
            "[link=http://safecopy.sourceforge.net/]safecopy homepage[/link]"
        ),
        script="image-safecopy-run.sh",
        args_template=["{conf}", "stage1", "--run"],
        scan_run_key=None,
        pgrep_pattern="safecopy",
        runtime_hint="1 – 12 h",
        rerunnable=True,
        requires_db_write=False,
        warning="Alternative to ddrescue — use when ddrescue makes no progress. Do NOT mix both.",
        is_optional=True,
        requires_prior=["create-job-config"],
    ),
    StageDef(
        key="safecopy-stage2",
        number=11,
        name="Safecopy Stage 2 (optional)",
        description=(
            "Stage 2: search for exact boundaries of bad areas identified in stage 1. "
            "Slower but recovers data around bad regions.\n\n"
            "[link=http://safecopy.sourceforge.net/]safecopy homepage[/link]"
        ),
        script="image-safecopy-run.sh",
        args_template=["{conf}", "stage2", "--run"],
        scan_run_key=None,
        pgrep_pattern="safecopy",
        runtime_hint="1 – 6 h",
        rerunnable=True,
        requires_db_write=False,
        is_optional=True,
        requires_prior=["safecopy-stage1"],
    ),
    StageDef(
        key="safecopy-stage3",
        number=12,
        name="Safecopy Stage 3 (optional)",
        description=(
            "Stage 3: maximum retries with head realignment tricks. "
            "Highest stress on the hardware — last resort.\n\n"
            "[link=http://safecopy.sourceforge.net/]safecopy homepage[/link]"
        ),
        script="image-safecopy-run.sh",
        args_template=["{conf}", "stage3", "--run"],
        scan_run_key=None,
        pgrep_pattern="safecopy",
        runtime_hint="1 – 8 h",
        rerunnable=True,
        requires_db_write=False,
        warning="Maximum disk stress — only run when stages 1 and 2 leave significant unread areas.",
        is_optional=True,
        requires_prior=["safecopy-stage2"],
    ),
    # ── Analysis ─────────────────────────────────────────────────────────
    StageDef(
        key="init-db",
        number=13,
        name="Initialize Image DB",
        description=(
            "Create or refresh the per-image SQLite catalog and export directory tree. "
            "Must be run before every other analysis step. Safe to re-run at any time.\n\n"
            "[link=https://www.sleuthkit.org/]The Sleuth Kit homepage[/link]"
        ),
        script="image-analysis-init.sh",
        args_template=["{image}", "--map", "{mapfile}"],
        scan_run_key=None,
        pgrep_pattern=None,
        runtime_hint="< 10 sec",
        rerunnable=True,
        requires_db_write=False,
        requires_prior=["ddrescue-first"],
    ),
    StageDef(
        key="structure-scan",
        number=14,
        name="Structure Scan",
        description=(
            "Run fdisk, parted, mmls, img_stat, and blkid on the image. "
            "Collects partition layout, filesystem type hints, and sector geometry. "
            "Use --force to re-run after indexing has already populated the DB.\n\n"
            "[link=https://www.sleuthkit.org/]The Sleuth Kit homepage[/link]"
        ),
        script="image-structure-scan.sh",
        args_template=["{db}"],
        scan_run_key="structure-scan",
        pgrep_pattern="image-structure-scan",
        runtime_hint="< 30 sec",
        rerunnable=True,
        requires_db_write=True,
        requires_prior=["init-db"],
    ),
    StageDef(
        key="index-tsk",
        number=15,
        name="TSK Filesystem Index",
        description=(
            "Build a filesystem-aware file inventory using fiwalk. "
            "Preserves original paths, inodes, timestamps, and partition context. "
            "Wallet and picture detection depend on this step. "
            "This is the most valuable early analysis step.\n\n"
            "[link=https://www.sleuthkit.org/sleuthkit/]The Sleuth Kit / fiwalk[/link]"
        ),
        script="image-index-tsk.sh",
        args_template=["{db}"],
        scan_run_key="index-tsk",
        pgrep_pattern="fiwalk",
        runtime_hint="1 – 15 min",
        rerunnable=True,
        requires_db_write=True,
        requires_prior=["structure-scan"],
    ),
    StageDef(
        key="detect-wallets",
        number=16,
        name="Wallet Detection",
        description=(
            "Score files from the filesystem inventory against wallet keywords and extensions. "
            "Results go into the wallet_candidates table.\n\n"
            "CAVEAT: hits are candidates only. Terms like 'seeds' or 'wallet' appear "
            "in eMule/aMule temp dirs and many unrelated contexts. Always verify by content.\n\n"
            "Query results:\n"
            "  image-query.sh <db> wallets"
        ),
        script="image-detect-wallets.sh",
        args_template=["{db}"],
        scan_run_key="detect-wallets",
        pgrep_pattern="image-detect-wallets",
        runtime_hint="< 30 sec",
        rerunnable=True,
        requires_db_write=True,
        requires_prior=["index-tsk"],
    ),
    StageDef(
        key="detect-pictures",
        number=17,
        name="Picture Detection",
        description=(
            "Score files from the filesystem inventory against picture extensions "
            "and path patterns (DCIM, Pictures, Photos). "
            "Results go into the picture_candidates table.\n\n"
            "Query results:\n"
            "  image-query.sh <db> pictures"
        ),
        script="image-detect-pictures.sh",
        args_template=["{db}"],
        scan_run_key="detect-pictures",
        pgrep_pattern="image-detect-pictures",
        runtime_hint="< 30 sec",
        rerunnable=True,
        requires_db_write=True,
        requires_prior=["index-tsk"],
    ),
    # ── NEW: EXIF enrichment ──────────────────────────────────────────────
    StageDef(
        key="enrich-photos",
        number=18,
        name="EXIF Photo Enrichment",
        description=(
            "Run exiftool on recovered image artifacts to extract EXIF metadata.\n\n"
            "Populates in the database:\n"
            "  • picture_candidates.camera_model / taken_at / width / height\n"
            "  • findings table (source_tool=exiftool): GPS coordinates, camera make/model\n\n"
            "GPS coordinates are especially valuable — they can confirm a seed phrase backup "
            "photo was taken at a specific location or on a specific device.\n\n"
            "Requires: at least one carving or recovery stage to have produced image artifacts.\n\n"
            "Query GPS hits afterwards:\n"
            "  image-query.sh <db> findings exiftool\n\n"
            "[link=https://exiftool.org]exiftool by Phil Harvey[/link]"
        ),
        script="image-enrich-photos.sh",
        args_template=["{db}", "--run"],
        scan_run_key="enrich-photos",
        pgrep_pattern="exiftool",
        runtime_hint="1 – 20 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["detect-pictures"],
    ),
    # ── Filesystem-specific recovery ──────────────────────────────────────
    StageDef(
        key="ext-recover",
        number=19,
        name="Ext Deleted-File Recovery (optional)",
        description=(
            "Run ext3/ext4 journal-aware deleted file recovery using extundelete and/or ext4magic. "
            "Only useful if the image contains ext3/ext4 partitions. "
            "Recovers files deleted before imaging.\n\n"
            "[link=http://extundelete.sourceforge.net/]extundelete[/link]  "
            "[link=https://sourceforge.net/projects/ext4magic/]ext4magic[/link]"
        ),
        script="image-ext-recover.sh",
        args_template=["{db}"],
        scan_run_key="ext-recover",
        pgrep_pattern="extundelete|ext4magic",
        runtime_hint="5 – 30 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["index-tsk"],
    ),
    StageDef(
        key="ntfs-recover",
        number=20,
        name="NTFS Deleted-File Recovery (optional)",
        description=(
            "Recover deleted files from NTFS partitions using ntfsundelete. "
            "Only useful if the image contains NTFS partitions (Windows disks). "
            "Runs a scan pass to identify recoverable files, then extracts them.\n\n"
            "[link=https://www.tuxera.com/community/open-source-ntfs-3g/]ntfs-3g / ntfsundelete[/link]"
        ),
        script="image-ntfs-recover.sh",
        args_template=["{db}"],
        scan_run_key="ntfs-recover",
        pgrep_pattern="ntfsundelete",
        runtime_hint="5 – 30 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["index-tsk"],
    ),
    StageDef(
        key="fat-recover",
        number=21,
        name="FAT/exFAT Recovery (optional)",
        description=(
            "Recover all files including deleted ones from FAT12/FAT16/FAT32/exFAT "
            "partitions using fatcat. Covers old digital camera cards, USB sticks, "
            "and legacy Windows partitions.\n\n"
            "[link=https://github.com/Gregwar/fatcat]fatcat homepage[/link]"
        ),
        script="image-fat-recover.sh",
        args_template=["{db}"],
        scan_run_key="fat-recover",
        pgrep_pattern="fatcat",
        runtime_hint="5 – 30 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["index-tsk"],
    ),
    StageDef(
        key="xfs-recover",
        number=22,
        name="XFS Deleted-File Recovery (optional)",
        description=(
            "Recover deleted files from XFS partitions using xfs_undelete.\n\n"
            "Requires installation:\n"
            "  apt install xfsprogs tcllib\n"
            "  git clone https://github.com/ianka/xfs_undelete /opt/xfs_undelete\n"
            "  ln -s /opt/xfs_undelete/xfs_undelete /usr/local/bin/xfs_undelete\n\n"
            "[link=https://github.com/ianka/xfs_undelete]xfs_undelete homepage[/link]"
        ),
        script="image-xfs-recover.sh",
        args_template=["{db}"],
        scan_run_key="xfs-recover",
        pgrep_pattern="xfs_undelete",
        runtime_hint="10 min – 2 h",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["index-tsk"],
    ),
    StageDef(
        key="btrfs-recover",
        number=23,
        name="Btrfs Recovery (optional)",
        description=(
            "Recover files from Btrfs partitions using btrfs restore. "
            "btrfs restore is read-only against the source — safe on images. "
            "Continues past errors with -i flag, so partial output is common "
            "on damaged filesystems.\n\n"
            "Requires: apt install btrfs-progs\n\n"
            "[link=https://btrfs.readthedocs.io]Btrfs documentation[/link]"
        ),
        script="image-btrfs-recover.sh",
        args_template=["{db}"],
        scan_run_key="btrfs-recover",
        pgrep_pattern="btrfs",
        runtime_hint="5 min – 2 h",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["index-tsk"],
    ),
    StageDef(
        key="bulk-extractor-raw",
        number=24,
        name="Bulk Extractor (raw image)",
        description=(
            "Run bulk_extractor across the entire raw image to extract text artifacts:\n"
            "  • Email addresses, URLs, JSON fragments\n"
            "  • Bitcoin/crypto addresses (accts scanner)\n"
            "  • NTFS traces, Windows paths\n"
            "  • Hex-encoded strings ≥8 bytes [base16 scanner — catches hex private keys]\n"
            "  • Wordlist of all tokens [wordlist scanner — use with john/hashcat]\n"
            "  • Outlook PST/OST fragments [outlook scanner]\n\n"
            "Results stored in indexes/bulk_extractor_raw/ and imported into SQLite "
            "(capped at BULK_HIT_LIMIT rows per feature file).\n\n"
            "[link=https://github.com/simsong/bulk_extractor]bulk_extractor homepage[/link]"
        ),
        script="image-bulk-extractor.sh",
        args_template=["{db}", "--scope", "raw"],
        scan_run_key="bulk-extractor-raw",
        pgrep_pattern="bulk_extractor",
        runtime_hint="2 – 8 h",
        rerunnable=True,
        requires_db_write=True,
        warning="Very long-running. Monitor output growth: du -sh on indexes/bulk_extractor_raw/",
        requires_prior=["init-db"],
    ),
    # ── NEW: PDF seed extraction ──────────────────────────────────────────
    StageDef(
        key="pdf-extract",
        number=25,
        name="PDF Seed Phrase Extraction",
        description=(
            "Extract text from recovered PDF files and scan for BIP39 seed phrases.\n\n"
            "Wallet software commonly exports recovery instructions as PDFs:\n"
            "  • Electrum paper wallet exports\n"
            "  • Hardware wallet setup guides (Ledger, Trezor)\n"
            "  • MetaMask Secret Recovery Phrase backups\n\n"
            "Hits written to:\n"
            "  findings table (source_tool=pdf-extract, category=seed_phrase)\n"
            "  wallet_candidates (for runs ≥ 12 consecutive BIP39 words)\n"
            "  notes table (high-confidence hits only)\n\n"
            "Query results:\n"
            "  image-query.sh <db> findings pdf-extract\n\n"
            "[link=https://poppler.freedesktop.org]poppler-utils (pdftotext)[/link]"
        ),
        script="image-pdf-extract.sh",
        args_template=["{db}"],
        scan_run_key="pdf-extract",
        pgrep_pattern="pdftotext",
        runtime_hint="5 – 30 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["init-db"],
    ),
    # ── NEW: YARA scanning ────────────────────────────────────────────────
    StageDef(
        key="yara-scan",
        number=26,
        name="YARA Wallet & Key Pattern Scan",
        description=(
            "Run YARA pattern-matching rules against the recovered corpus.\n\n"
            "Rules in /root/hdd-recovery/config/yara/:\n"
            "  wallets.yar    — Ethereum keystore, Electrum JSON, MetaMask vault,\n"
            "                   Bitcoin Core wallet.dat markers, Exodus, Trust Wallet\n"
            "  crypto_keys.yar — xpub/xprv BIP32 keys, WIF private keys,\n"
            "                   PEM-armored EC/RSA keys, BIP39 seed word clusters\n\n"
            "Results written to:\n"
            "  findings table (source_tool=yara, category=wallet)\n"
            "  wallet_candidates (for score ≥ 65)\n"
            "  hits/yara/<timestamp>/hits.tsv\n\n"
            "Query results:\n"
            "  image-query.sh <db> findings yara\n\n"
            "[link=https://virustotal.github.io/yara/]YARA homepage[/link]"
        ),
        script="image-yara-scan.sh",
        args_template=["{db}"],
        scan_run_key="yara-scan",
        pgrep_pattern="yara",
        runtime_hint="5 – 30 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["init-db"],
    ),
    # ── Carving ───────────────────────────────────────────────────────────
    StageDef(
        key="carve-foremost",
        number=27,
        name="Carve with Foremost",
        description=(
            "Broad signature-based file carving using foremost. "
            "Recovers deleted and free-space files regardless of filesystem state. "
            "Expect heavy noise: web cache files, thumbnails, and application assets "
            "alongside useful files.\n\n"
            "[link=http://foremost.sourceforge.net/]Foremost homepage[/link]"
        ),
        script="image-carve.sh",
        args_template=["{db}", "--method", "foremost"],
        scan_run_key="carve-foremost",
        pgrep_pattern="foremost",
        runtime_hint="30 min – 3 h",
        rerunnable=True,
        requires_db_write=True,
        requires_prior=["init-db"],
    ),
    StageDef(
        key="carve-scalpel",
        number=28,
        name="Carve with Scalpel",
        description=(
            "Controlled carving using a tuned scalpel config targeting wallet files, "
            "documents, and common archive formats. Less noisy than foremost "
            "but may miss some file types.\n\n"
            "[link=https://github.com/sleuthkit/scalpel]Scalpel homepage[/link]"
        ),
        script="image-carve.sh",
        args_template=["{db}", "--method", "scalpel"],
        scan_run_key="carve-scalpel",
        pgrep_pattern="scalpel",
        runtime_hint="1 – 4 h",
        rerunnable=True,
        requires_db_write=True,
        requires_prior=["init-db"],
    ),
    StageDef(
        key="carve-recoverjpeg",
        number=29,
        name="Carve JPEGs with recoverjpeg (optional)",
        description=(
            "Fast JPEG-only carver — lighter and faster than foremost/scalpel "
            "when the primary goal is recovering photos.\n\n"
            "Scans for JPEG markers (SOI/EOI), extracts every recoverable image. "
            "Outputs named image000000.jpg, image000001.jpg, etc.\n\n"
            "[link=https://rfc1149.net/devel/recoverjpeg.html]recoverjpeg homepage[/link]"
        ),
        script="image-carve.sh",
        args_template=["{db}", "--method", "recoverjpeg"],
        scan_run_key="carve-recoverjpeg",
        pgrep_pattern="recoverjpeg",
        runtime_hint="5 – 30 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["init-db"],
    ),
    StageDef(
        key="carve-magicrescue",
        number=30,
        name="Carve with Magicrescue (optional)",
        description=(
            "Recipe-based carver with good coverage for office docs, images, audio, "
            "archives, and SQLite databases (useful for wallet.dat).\n\n"
            "Uses recipes: jpeg-jfif, jpeg-exif, png, sqlite, zip, rar, gzip, msoffice, "
            "mp3-id3v1, mp3-id3v2, avi.\n\n"
            "[link=https://github.com/jbj/magicrescue]magicrescue homepage[/link]"
        ),
        script="image-carve.sh",
        args_template=["{db}", "--method", "magicrescue"],
        scan_run_key="carve-magicrescue",
        pgrep_pattern="magicrescue",
        runtime_hint="15 min – 2 h",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["init-db"],
    ),
    # ── Post-carve analysis ───────────────────────────────────────────────
    StageDef(
        key="bulk-extractor-recovered",
        number=31,
        name="Bulk Extractor (recovered corpus)",
        description=(
            "Run bulk_extractor over the recovered/carved corpus directory. "
            "Extracts text artifacts from already-carved files — useful when "
            "the raw-image pass missed content inside carved containers.\n\n"
            "WARNING: can spin at 100% CPU during finalization with no visible output. "
            "Monitor with: du -sh on indexes/bulk_extractor_recovered/\n\n"
            "[link=https://github.com/simsong/bulk_extractor]bulk_extractor homepage[/link]"
        ),
        script="image-bulk-extractor.sh",
        args_template=["{db}", "--scope", "recovered"],
        scan_run_key="bulk-extractor-recovered",
        pgrep_pattern="bulk_extractor",
        runtime_hint="30 min – 3 h",
        rerunnable=True,
        requires_db_write=True,
        warning="Can hang/spin at 100% CPU during finalization. Monitor output dir size.",
        requires_prior=["carve-foremost"],
    ),
    StageDef(
        key="recoll-index",
        number=32,
        name="Recoll Full-Text Index (optional)",
        description=(
            "Build a Recoll full-text search index over the recovered corpus. "
            "Useful for searching specific terms inside recovered documents, PDFs, etc. "
            "Disabled by default (ENABLE_RECOLL=0 in config). "
            "Set ENABLE_RECOLL=1 to activate.\n\n"
            "[link=https://www.lesbonscomptes.com/recoll/]Recoll homepage[/link]"
        ),
        script="image-index-recoll.sh",
        args_template=["{db}", "--path", "{export_root}/recovered"],
        scan_run_key="recoll-index",
        pgrep_pattern="recollindex",
        runtime_hint="5 – 30 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["carve-foremost"],
    ),
    StageDef(
        key="ntfs-artifact-summary",
        number=33,
        name="NTFS Artifact Summary",
        description=(
            "Extract Windows-specific traces from raw bulk_extractor output: "
            "MFT carved entries, USN journal, prefetch records, LNK files, "
            "Windows directory paths.\n\n"
            "Useful even when the current filesystem is ext4 — "
            "NTFS traces reveal prior Windows use of the same disk.\n\n"
            "Outputs to: exports/recovered/ntfs-artifacts/\n"
            "  interesting-lines.tsv — keyword-matched lines\n"
            "  possible-windows-paths.tsv — reconstructed Windows paths\n\n"
            "[link=https://www.sleuthkit.org/]The Sleuth Kit homepage[/link]"
        ),
        script="image-ntfs-artifact-summary.sh",
        args_template=["{db}"],
        scan_run_key="ntfs-artifact-summary",
        pgrep_pattern="image-ntfs-artifact-summary",
        runtime_hint="< 2 min",
        rerunnable=True,
        requires_db_write=True,
        requires_prior=["bulk-extractor-raw"],
    ),
    # ── NEW: Windows Registry analysis ────────────────────────────────────
    StageDef(
        key="regripper",
        number=34,
        name="RegRipper — Windows Registry Analysis",
        description=(
            "Run RegRipper on Windows registry hive files found in the recovered corpus.\n\n"
            "Extracts from NTUSER.DAT / SOFTWARE / SYSTEM / SAM hives:\n"
            "  • MRU file lists — recently opened files (may include wallet software)\n"
            "  • Installed software — Bitcoin Core, Electrum, exchange apps\n"
            "  • USB device history — Ledger/Trezor hardware wallets are USB devices\n"
            "  • UserAssist — program execution history with timestamps\n"
            "  • Run/RunOnce keys — startup entries\n\n"
            "Outputs to: exports/structure/registry/\n"
            "  *.rip.txt          — full RegRipper output per hive\n"
            "  *.interesting.txt  — lines matching crypto/wallet keywords\n"
            "  summary.tsv        — hive processing summary\n\n"
            "Requires: at least one carving stage to have extracted registry hives.\n"
            "Only relevant for Windows disk images.\n\n"
            "Query results:\n"
            "  image-query.sh <db> findings regripper\n\n"
            "[link=https://github.com/keydet89/RegRipper3.0]RegRipper by Harlan Carvey[/link]"
        ),
        script="image-regripper.sh",
        args_template=["{db}"],
        scan_run_key="regripper",
        pgrep_pattern="regripper",
        runtime_hint="2 – 10 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["ntfs-artifact-summary"],
    ),
    # ── NEW: Windows Recycle Bin ──────────────────────────────────────────
    StageDef(
        key="rifiuti2",
        number=35,
        name="rifiuti2 — Windows Recycle Bin Analysis",
        description=(
            "Parse Windows Recycle Bin metadata to recover original file paths\n"
            "and deletion timestamps.\n\n"
            "Covers two formats:\n"
            "  INFO2 — Windows 98/Me/2000/XP ($Recycle.Bin/INFO2)\n"
            "  $I files — Windows Vista/7/8/10/11 (per-deleted-file metadata)\n\n"
            "Useful for:\n"
            "  • Wallet files the user deleted before reformatting\n"
            "  • Exchange app data or browser download history\n"
            "  • Documents or seed phrase backups\n\n"
            "Outputs to: exports/structure/recycle-bin/\n"
            "  all_deleted_files.tsv — original path + deletion time for every entry\n\n"
            "Only relevant for Windows disk images.\n\n"
            "Query results:\n"
            "  image-query.sh <db> findings rifiuti2\n\n"
            "[link=https://abelcheung.github.io/rifiuti2/]rifiuti2 homepage[/link]"
        ),
        script="image-rifiuti.sh",
        args_template=["{db}"],
        scan_run_key="rifiuti2",
        pgrep_pattern="rifiuti2",
        runtime_hint="< 5 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["ntfs-artifact-summary"],
    ),
    # ── NEW: Super-timeline ───────────────────────────────────────────────
    StageDef(
        key="plaso-timeline",
        number=36,
        name="plaso Super-Timeline",
        description=(
            "Generate a comprehensive super-timeline from the recovered corpus\n"
            "using log2timeline (plaso).\n\n"
            "Parses 50+ artifact types from the recovered/ directory:\n"
            "  • File system timestamps (atime, mtime, ctime)\n"
            "  • EXIF metadata from images\n"
            "  • Windows LNK shortcut files\n"
            "  • Windows prefetch records\n"
            "  • Browser history (Chrome, Firefox)\n"
            "  • Recycle Bin entries\n"
            "  • Shellbags, recently accessed folders\n\n"
            "Outputs to: exports/timeline/\n"
            "  <basename>.plaso          — plaso storage file (SQLite internally)\n"
            "  <basename>.timeline.csv   — sortable l2tcsv timeline\n\n"
            "Query the CSV timeline:\n"
            "  grep -i wallet <basename>.timeline.csv | sort\n\n"
            "NOTE: runs against recovered corpus only. Use --full for raw image (hours).\n\n"
            "Binaries: plaso-log2timeline, plaso-psort  (package: python3-plaso)\n\n"
            "[link=https://plaso.readthedocs.io]plaso / log2timeline documentation[/link]"
        ),
        script="image-plaso.sh",
        args_template=["{db}", "--run"],
        scan_run_key="plaso-timeline",
        pgrep_pattern="plaso-log2timeline",
        runtime_hint="30 min – 4 h",
        rerunnable=True,
        requires_db_write=True,
        warning="Long-running. Monitor progress in the log. Use --full to include raw image (much slower).",
        is_optional=True,
        requires_prior=["init-db"],
    ),
    # ── Broad recovery & report ───────────────────────────────────────────
    StageDef(
        key="photorec-broad",
        number=37,
        name="PhotoRec Broad Recovery",
        description=(
            "Broadest carver — recovers the most file types from unallocated space. "
            "Usually the highest-value broad carver for pictures and documents, "
            "but also the noisiest. Output under recovered/photorec/<profile>-<timestamp>/.\n\n"
            "Uses /cmd for unattended operation on this installed build.\n\n"
            "[link=https://www.cgsecurity.org/wiki/PhotoRec]PhotoRec homepage[/link]"
        ),
        script="image-photorec-run.sh",
        args_template=["{db}", "--profile", "broad"],
        scan_run_key="photorec-broad",
        pgrep_pattern="photorec",
        runtime_hint="1 – 6 h",
        rerunnable=True,
        requires_db_write=True,
        warning="Produces very large output with many duplicates. Review separately after all other stages.",
        requires_prior=["init-db"],
    ),
    StageDef(
        key="generate-report",
        number=38,
        name="Generate Report",
        description=(
            "Generate a summary report for this image covering all stages, "
            "file counts, wallet candidates, picture candidates, recovered artifact "
            "locations with disk usage, and bulk extractor hits. "
            "Re-run after any heavy stage to refresh the summary.\n\n"
            "Also useful after YARA, regripper, or rifiuti2 to capture their findings."
        ),
        script="image-report.sh",
        args_template=["{db}"],
        scan_run_key=None,
        pgrep_pattern=None,
        runtime_hint="< 30 sec",
        rerunnable=True,
        requires_db_write=False,
        requires_prior=["index-tsk"],
    ),
    # ── LLM photo tagging ─────────────────────────────────────────────────
    StageDef(
        key="tag-photos",
        number=39,
        name="LLM Photo Tagging (llava)",
        description=(
            "Tag recovered images using a local Ollama vision model.\n\n"
            "For each qualifying image the model writes a one-paragraph forensic\n"
            "description stored in the findings table\n"
            "(source_tool='llava', category='photo-description').\n\n"
            "Already-tagged images are skipped automatically — safe to interrupt\n"
            "and re-run. Progress is tracked in scan_runs.\n\n"
            "Scopes:\n"
            "  real  — JPEG files ≥ 20 KB only  (default, ~21 s/image)\n"
            "  all   — any image/* type ≥ 10 KB  (includes web cache PNGs/BMPs)\n\n"
            "Descriptions appear in the web gallery as hover overlays and can be\n"
            "searched via the description search box at /gallery.\n\n"
            "Requires: a running Ollama instance with the chosen model pulled:\n"
            "  ollama pull llava:7b"
        ),
        script="image-tag-photos.py",
        args_template=["{db}"],   # real args built by TagPhotosScreen config modal
        scan_run_key="llava-tag-photos",
        pgrep_pattern="image-tag-photos",
        runtime_hint="1 – 4 h",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        has_config_screen=True,
        requires_prior=["detect-pictures"],
        warning="Requires a running Ollama instance with llava:7b (or chosen model) pulled.",
    ),
    # ── Wallet inspection and cracking ────────────────────────────────────
    StageDef(
        key="wallet-inspect",
        number=40,
        name="pywallet Wallet Inspection",
        description=(
            "Inspect wallet.dat candidates with pywallet and record extracted keys "
            "or encrypted-wallet placeholders in wallet_keys.\n\n"
            "Outputs sensitive dumps under hits/wallet-inspect/."
        ),
        script="image-wallet-inspect.sh",
        args_template=["{db}", "--run"],
        scan_run_key="wallet-inspect",
        pgrep_pattern="image-wallet-inspect|pywallet",
        runtime_hint="1 – 20 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["detect-wallets"],
    ),
    StageDef(
        key="crack-wallet",
        number=41,
        name="Crack Bitcoin wallet.dat",
        description=(
            "Manual cracking stage for encrypted Bitcoin Core wallet.dat files. "
            "Uses bitcoin2john and hashcat -m 11300 with mandatory NVIDIA GPU; "
            "john CPU fallback only runs when explicitly requested outside the TUI."
        ),
        script="image-crack-wallet.sh",
        args_template=["{db}", "--run"],
        scan_run_key="crack-wallet",
        pgrep_pattern="image-crack-wallet|hashcat.*11300|john.*bitcoin",
        runtime_hint="manual / long-running",
        rerunnable=True,
        requires_db_write=True,
        warning="Manual cracking stage. Requires NVIDIA GPU; no silent CPU fallback.",
        is_optional=True,
        is_manual=True,
        requires_prior=["wallet-inspect", "yara-scan", "detect-wallets"],
    ),
    StageDef(
        key="btcrecover",
        number=42,
        name="btcrecover Partial Recovery",
        description=(
            "Manual btcrecover wrapper for partial BIP39 seed or partial password "
            "recovery. Operator writes a config under state/btcrecover/ and runs "
            "the wrapper with --config."
        ),
        script="image-btcrecover.sh",
        args_template=["{db}"],
        scan_run_key="btcrecover",
        pgrep_pattern="image-btcrecover|btcrecover|seedrecover",
        runtime_hint="manual / long-running",
        rerunnable=True,
        requires_db_write=True,
        warning="Manual operator-driven stage. Prepare config before running.",
        is_optional=True,
        is_manual=True,
        requires_prior=["init-db"],
    ),
    StageDef(
        key="crack-keepass",
        number=43,
        name="Crack KeePass KDBX",
        description=(
            "Manual KeePass cracking for recovered .kdbx artifacts. KDBX 1-3 uses "
            "hashcat -m 13400 with mandatory NVIDIA GPU; KDBX4 Argon2 CPU brute "
            "force is opt-in only from the command line."
        ),
        script="image-crack-keepass.sh",
        args_template=["{db}", "--run"],
        scan_run_key="crack-keepass",
        pgrep_pattern="image-crack-keepass|hashcat.*13400|keepass4brute",
        runtime_hint="manual / long-running",
        rerunnable=True,
        requires_db_write=True,
        warning="Manual cracking stage. Requires NVIDIA GPU for hashcat path.",
        is_optional=True,
        is_manual=True,
        requires_prior=["carve-foremost", "carve-scalpel", "carve-recoverjpeg", "carve-magicrescue"],
    ),
    # ── Seed and file enrichment ──────────────────────────────────────────
    StageDef(
        key="text-seed-scan",
        number=44,
        name="Text Seed Phrase Scan",
        description=(
            "Scan recovered text-like files for consecutive BIP39 seed-word runs. "
            "High-confidence 12+ word runs are also written to notes."
        ),
        script="image-text-seed-scan.sh",
        args_template=["{db}", "--run"],
        scan_run_key="text-seed-scan",
        pgrep_pattern="image-text-seed-scan",
        runtime_hint="1 – 30 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["carve-foremost", "carve-scalpel", "carve-recoverjpeg", "carve-magicrescue"],
    ),
    StageDef(
        key="ocr-seed-scan",
        number=49,
        name="OCR Seed Phrase Scan",
        description=(
            "OCR recovered images with Tesseract and flag any whose text contains "
            "a run of consecutive BIP39 seed words — catches seed phrases that were "
            "photographed or scanned rather than typed.\n\n"
            "Reads picture_candidates (joined with recovered_artifacts for file "
            "paths), runs tesseract on each image, and writes hits to "
            "<export_root>/hits/ocr-seeds/<timestamp>/ plus a scan_runs row; "
            "high-confidence 12+ word runs are appended to notes.\n\n"
            "Optional and intentionally NOT in the 'full' preset — OCR over every "
            "recovered image is slow; run it explicitly or add it to a preset when "
            "wanted.\n\n"
            "Query hits afterwards:\n"
            "  image-query.sh <db> findings ocr-seed-scan"
        ),
        script="image-ocr-seed-scan.py",
        args_template=["{db}"],
        scan_run_key="ocr-seed-scan",
        pgrep_pattern="image-ocr-seed-scan",
        runtime_hint="10 min – several hours",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["detect-pictures"],
    ),
    StageDef(
        key="enrich-trid",
        number=45,
        name="TrID File Type Enrichment",
        description=(
            "Run TrID over recovered artifacts and store top guesses in SQLite. "
            "This stage never uses --ae and never renames carved files."
        ),
        script="image-enrich-trid.sh",
        args_template=["{db}", "--run"],
        scan_run_key="enrich-trid",
        pgrep_pattern="image-enrich-trid|trid",
        runtime_hint="5 – 60 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["carve-foremost", "carve-scalpel", "carve-recoverjpeg", "carve-magicrescue"],
    ),
    StageDef(
        key="dedup-photos",
        number=46,
        name="Photo Perceptual Dedup",
        description=(
            "Group near-duplicate recovered photos by perceptual hash and mark one "
            "primary artifact per cluster. Does not move or delete files."
        ),
        script="image-dedup-photos.sh",
        args_template=["{db}", "--run"],
        scan_run_key="dedup-photos",
        pgrep_pattern="image-dedup-photos",
        runtime_hint="5 – 60 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["enrich-photos"],
    ),
    # ── Windows memory ────────────────────────────────────────────────────
    StageDef(
        key="extract-winmem",
        number=47,
        name="Extract Windows Memory Artifacts",
        description=(
            "Extract hiberfil.sys and pagefile.sys from the image using TSK inode "
            "data and register the outputs under winmem/."
        ),
        script="image-extract-winmem.sh",
        args_template=["{db}", "--run"],
        scan_run_key="extract-winmem",
        pgrep_pattern="image-extract-winmem|icat",
        runtime_hint="5 – 60 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["index-tsk"],
    ),
    StageDef(
        key="volatility3",
        number=48,
        name="Volatility3 Windows Memory Scan",
        description=(
            "Run focused Volatility3 plugins against winmem-extract outputs and "
            "import process, command-line, credential, network, and dumpfile findings."
        ),
        script="image-volatility-scan.sh",
        args_template=["{db}", "--run"],
        scan_run_key="volatility3",
        pgrep_pattern="image-volatility-scan|vol .*windows",
        runtime_hint="30 min – 4 h",
        rerunnable=True,
        requires_db_write=True,
        warning="Manual Windows memory analysis stage. Requires extracted winmem files.",
        is_optional=True,
        is_manual=True,
        requires_prior=["extract-winmem"],
    ),
    StageDef(
        key="detect-encrypted",
        number=50,
        name="Detect Encrypted Containers",
        description=(
            "Find encrypted containers and volumes — common crypto-holder backup "
            "strategies that the keyword/picture detectors miss entirely.\n\n"
            "Detects:\n"
            "  volume-level  LUKS and BitLocker partitions / whole-disk encryption\n"
            "                (parses the image partition table; no mount)\n"
            "  file-level    KeePass (.kdbx), PGP/GPG key material, encrypted ZIP,\n"
            "                7z/RAR archives, and VeraCrypt/TrueCrypt containers\n"
            "                (entropy + extension, since they have no magic)\n\n"
            "Signature matches are high-confidence; entropy/extension hits are "
            "low-confidence leads for human review. Results written to:\n"
            "  findings table (source_tool=encrypted-detect, category=encrypted-container)\n"
            "  hits/encrypted/hits-<timestamp>.tsv\n\n"
            "Query results:\n"
            "  image-query.sh <db> findings encrypted-detect"
        ),
        script="image-detect-encrypted-containers.sh",
        args_template=["{db}"],
        scan_run_key="detect-encrypted",
        pgrep_pattern="image-detect-encrypted",
        runtime_hint="2 – 30 min",
        rerunnable=True,
        requires_db_write=True,
        is_optional=True,
        requires_prior=["init-db"],
    ),
]

STAGE_BY_KEY: dict[str, StageDef] = {s.key: s for s in STAGES}
TOTAL_STAGES = len(STAGES)
