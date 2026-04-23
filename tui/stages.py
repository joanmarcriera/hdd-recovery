"""Stage definitions — one entry per workflow step."""
from dataclasses import dataclass, field
from typing import Optional


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
    requires_prior: list[str] = field(default_factory=list)
    auto_stdin: bytes = b""         # written to stdin after process starts (ddrescue --ask)


STAGES: list[StageDef] = [
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
    StageDef(
        key="ddrescue-preview",
        number=3,
        name="Preview ddrescue Command",
        description=(
            "Run ddrescue-run.sh in plan mode to confirm source device, "
            "image path, and map path before any real imaging starts. "
            "Nothing is written to disk."
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
            "The TUI will automatically confirm the ddrescue --ask prompt."
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
            "Use this to decide whether retry passes are needed."
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
        key="ddrescue-retry",
        number=6,
        name="ddrescue Retry Pass (optional)",
        description=(
            "Retry unread sectors using direct I/O and up to 3 attempts per block. "
            "Only useful if the first pass left unread areas (map coverage < 100%)."
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
        number=7,
        name="ddrescue Reverse Pass (optional)",
        description="Approach remaining bad sectors from the opposite direction.",
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
        number=8,
        name="ddrescue Retrim Pass (optional)",
        description="Mark failed blocks for retrim and make one final conservative attempt.",
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
    StageDef(
        key="init-db",
        number=9,
        name="Initialize Image DB",
        description=(
            "Create or refresh the per-image SQLite catalog and export directory tree. "
            "Must be run before every other analysis step. Safe to re-run at any time."
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
        number=10,
        name="Structure Scan",
        description=(
            "Run fdisk, parted, mmls, img_stat, and blkid on the image. "
            "Collects partition layout, filesystem type hints, and sector geometry. "
            "Use --force to re-run after indexing has already populated the DB."
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
        number=11,
        name="TSK Filesystem Index",
        description=(
            "Build a filesystem-aware file inventory using fiwalk. "
            "Preserves original paths, inodes, timestamps, and partition context. "
            "Wallet and picture detection depend on this step. "
            "This is the most valuable early analysis step."
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
        number=12,
        name="Wallet Detection",
        description=(
            "Score files from the filesystem inventory against wallet keywords and extensions. "
            "Results go into the wallet_candidates table.\n\n"
            "CAVEAT: hits are candidates only. Terms like 'seeds' or 'wallet' appear "
            "in eMule/aMule temp dirs and many unrelated contexts. Always verify by content."
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
        number=13,
        name="Picture Detection",
        description=(
            "Score files from the filesystem inventory against picture extensions "
            "and path patterns (DCIM, Pictures, Photos). "
            "Results go into the picture_candidates table."
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
    StageDef(
        key="ext-recover",
        number=14,
        name="Ext Deleted-File Recovery (optional)",
        description=(
            "Run ext3/ext4 journal-aware deleted file recovery using extundelete and/or ext4magic. "
            "Only useful if the image contains ext3/ext4 partitions. "
            "Recovers files deleted before imaging."
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
        key="bulk-extractor-raw",
        number=15,
        name="Bulk Extractor (raw image)",
        description=(
            "Run bulk_extractor across the entire raw image to extract text artifacts: "
            "email addresses, URLs, Bitcoin/crypto addresses, JSON fragments, "
            "NTFS traces, and other patterns.\n\n"
            "Results stored in indexes/bulk_extractor_raw/ and imported into SQLite "
            "(capped at BULK_HIT_LIMIT rows per feature file)."
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
    StageDef(
        key="carve-foremost",
        number=16,
        name="Carve with Foremost",
        description=(
            "Broad signature-based file carving using foremost. "
            "Recovers deleted and free-space files regardless of filesystem state. "
            "Expect heavy noise: web cache files, thumbnails, and application assets "
            "alongside useful files."
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
        number=17,
        name="Carve with Scalpel",
        description=(
            "Controlled carving using a tuned scalpel config targeting wallet files, "
            "documents, and common archive formats. Less noisy than foremost "
            "but may miss some file types."
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
        key="bulk-extractor-recovered",
        number=18,
        name="Bulk Extractor (recovered corpus)",
        description=(
            "Run bulk_extractor over the recovered/carved corpus directory. "
            "Extracts text artifacts from already-carved files — useful when "
            "the raw-image pass missed content inside carved containers.\n\n"
            "WARNING: can spin at 100% CPU during finalization with no visible output. "
            "Monitor with: du -sh on indexes/bulk_extractor_recovered/"
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
        number=19,
        name="Recoll Full-Text Index (optional)",
        description=(
            "Build a Recoll full-text search index over the recovered corpus. "
            "Useful for searching specific terms inside recovered documents, PDFs, etc. "
            "Disabled by default (ENABLE_RECOLL=0 in config). "
            "Set ENABLE_RECOLL=1 to activate."
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
        number=20,
        name="NTFS Artifact Summary",
        description=(
            "Extract Windows-specific traces from raw bulk_extractor output: "
            "MFT carved entries, USN journal, prefetch records, LNK files, "
            "Windows directory paths.\n\n"
            "Useful even when the current filesystem is ext4 — "
            "NTFS traces reveal prior Windows use of the same disk."
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
    StageDef(
        key="photorec-broad",
        number=21,
        name="PhotoRec Broad Recovery",
        description=(
            "Broadest carver — recovers the most file types from unallocated space. "
            "Usually the highest-value broad carver for pictures and documents, "
            "but also the noisiest. Output under recovered/photorec/<profile>-<timestamp>/.\n\n"
            "Uses /cmd for unattended operation on this installed build."
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
        number=22,
        name="Generate Report",
        description=(
            "Generate a summary report for this image covering all stages, "
            "file counts, wallet candidates, picture candidates, and recovered artifacts. "
            "Re-run after any heavy stage to refresh the summary."
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
]

STAGE_BY_KEY: dict[str, StageDef] = {s.key: s for s in STAGES}
TOTAL_STAGES = len(STAGES)
