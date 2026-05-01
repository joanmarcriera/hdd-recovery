# ── Stage 1: build the Go supervisor ─────────────────────────────────────────
FROM golang:1.22-alpine AS supervisor-builder

WORKDIR /build
COPY docker/supervisor/ .
RUN go build -ldflags="-s -w" -o hdd-supervisor .

# ── Stage 2: forensics container ─────────────────────────────────────────────
FROM kalilinux/kali-rolling

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ── Forensics toolchain ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core utilities
    bash curl wget ca-certificates git procps pv jq file lsof \
    # Disk & filesystem
    ddrescue parted gdisk util-linux btrfs-progs xfsprogs dosfstools \
    # Forensics
    sleuthkit testdisk bulk-extractor foremost scalpel \
    extundelete ext4magic recoverjpeg magicrescue binwalk dc3dd \
    # Metadata & databases
    libimage-exiftool-perl db5.3-util sqlite3 \
    # Python runtime (for inline scripts in existing bash tools)
    python3 python3-pip \
    # OCR
    tesseract-ocr tesseract-ocr-eng \
    # Full-text indexing (ENABLE_RECOLL=1 to activate)
    recoll \
    # PDF text extraction (pdftotext)
    poppler-utils \
    # YARA pattern matching
    yara \
    # Windows Registry analysis
    regripper \
    # Windows Recycle Bin analysis
    rifiuti2 \
    # Super-timeline (log2timeline.py / psort.py)
    python3-plaso \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── ttyd: download static binary (not in Kali apt) ───────────────────────────
RUN ARCH=$(dpkg --print-architecture) && \
    case "$ARCH" in \
      amd64) TTYD_ARCH="x86_64" ;; \
      arm64) TTYD_ARCH="aarch64" ;; \
      *) echo "Unsupported arch for ttyd: $ARCH" && exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.${TTYD_ARCH}" \
      -o /usr/local/bin/ttyd && \
    chmod +x /usr/local/bin/ttyd && \
    echo "ttyd installed: $(ttyd --version 2>&1)"

# ── uv (Python package manager, used by the TUI) ─────────────────────────────
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# ── BIP39 wordlist (2048-word English list from BIP-39 specification) ─────────
RUN curl -fsSL \
      "https://raw.githubusercontent.com/bitcoin/bips/master/bip-0039/english.txt" \
      -o /usr/local/share/bip39-english.txt \
    && wc -l /usr/local/share/bip39-english.txt | grep -q "^2048" \
    && echo "BIP39 wordlist: 2048 words OK"

# ── Copy scripts and configs from the repository ─────────────────────────────
COPY bin/    /root/hdd-recovery/bin/
COPY lib/    /root/hdd-recovery/lib/
COPY sql/    /root/hdd-recovery/sql/
COPY config/ /root/hdd-recovery/config/
COPY tui/    /root/hdd-recovery/tui/

RUN chmod +x /root/hdd-recovery/bin/*.sh \
             /root/hdd-recovery/bin/*.py

# Pre-install TUI Python dependencies so the container works without internet.
RUN cd /root/hdd-recovery/tui && uv sync --frozen

# ── Go supervisor binary ──────────────────────────────────────────────────────
COPY --from=supervisor-builder /build/hdd-supervisor /usr/local/bin/hdd-supervisor

# ── Jobs directory (user-provided at runtime via volume or created on demand) ─
RUN mkdir -p /root/hdd-recovery/jobs

WORKDIR /root/hdd-recovery

# ── Ports ─────────────────────────────────────────────────────────────────────
# 7681  ttyd — browser terminal
# 8080  supervisor health/status API
EXPOSE 7681 8080

ENTRYPOINT ["/usr/local/bin/hdd-supervisor"]
