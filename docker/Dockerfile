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
    bash curl wget ca-certificates git procps pv jq file lsof unzip \
    build-essential libdb-dev python3-dev libssl-dev libffi-dev \
    autoconf automake libtool pkg-config \
    # Disk & filesystem
    ddrescue parted gdisk util-linux fdisk btrfs-progs xfsprogs dosfstools \
    fatcat tcllib \
    # Forensics
    sleuthkit testdisk bulk-extractor foremost scalpel \
    extundelete ext4magic recoverjpeg magicrescue binwalk dc3dd \
    john john-data hashcat hashcat-data keepassxc \
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

# ── Pinned PyPI dependencies (reproducible builds) ───────────────────────────
# Single source of truth for the formerly-unpinned pip installs (bsddb3, ecdsa,
# pycryptodome, volatility3, imagehash). See docker/requirements.txt.
COPY docker/requirements.txt /tmp/hdd-requirements.txt
RUN python3 -m pip install --break-system-packages -r /tmp/hdd-requirements.txt

# ── pywallet (GSC Python 3 fork) ─────────────────────────────────────────────
ARG PYWALLET_COMMIT=5376a54a36f75cec7226de7cca1511e9cf058f37
RUN git clone https://github.com/Great-Software-Company/pywallet.git /opt/pywallet \
    && cd /opt/pywallet \
    && git checkout "$PYWALLET_COMMIT" \
    && printf '%s\n' \
      'from setuptools import setup' \
      'setup(name="pywallet-gsc", version="2.3", py_modules=["pywallet"])' \
      > setup.py \
    && python3 -m pip install --break-system-packages -e . \
    && chmod +x /opt/pywallet/pywallet.py /opt/pywallet/run_pywallet.sh \
    && ln -sf /opt/pywallet/run_pywallet.sh /usr/local/bin/pywallet \
    && python3 -c 'import pywallet'

# ── btcrecover (3rdIteration fork — not on PyPI, ships as scripts) ───────────
ARG BTCRECOVER_REF=7101ca41b8ca526f1b9bca905587701ad4767bea  # tag 1.12.0
RUN git clone https://github.com/3rdIteration/btcrecover.git /opt/btcrecover \
    && cd /opt/btcrecover \
    && git checkout "$BTCRECOVER_REF" \
    && python3 -m pip install --break-system-packages -r requirements.txt \
    && chmod +x btcrecover.py seedrecover.py \
    && printf '%s\n' '#!/usr/bin/env bash' \
      'exec python3 /opt/btcrecover/btcrecover.py "$@"' \
      > /usr/local/bin/btcrecover.py \
    && printf '%s\n' '#!/usr/bin/env bash' \
      'exec python3 /opt/btcrecover/seedrecover.py "$@"' \
      > /usr/local/bin/seedrecover.py \
    && chmod +x /usr/local/bin/btcrecover.py /usr/local/bin/seedrecover.py \
    && btcrecover.py --help >/dev/null \
    && seedrecover.py --help >/dev/null

# Volatility3 and imagehash are installed from docker/requirements.txt above.

# ── xfs_undelete (Tcl script, requires tcllib from apt) ─────────────────────
ARG XFS_UNDELETE_REF=43fec30088d4bdc608a43a1aa81f2525dd260d17
RUN git clone https://github.com/ianka/xfs_undelete.git /opt/xfs_undelete \
    && cd /opt/xfs_undelete \
    && git checkout "$XFS_UNDELETE_REF" \
    && chmod +x xfs_undelete \
    && ln -sf /opt/xfs_undelete/xfs_undelete /usr/local/bin/xfs_undelete

# ── keepass4brute for KDBX 4 / Argon2 CPU-only fallback ─────────────────────
ARG KEEPASS4BRUTE_COMMIT=6e0b1a0e11daaa674653c5f15f8d06ba2164c335
RUN git clone https://github.com/r3nt0n/keepass4brute.git /opt/keepass4brute \
    && cd /opt/keepass4brute \
    && git checkout "$KEEPASS4BRUTE_COMMIT" \
    && chmod +x keepass4brute.sh \
    && ln -sf /opt/keepass4brute/keepass4brute.sh /usr/local/bin/keepass4brute.sh

# ── TrID file identifier ─────────────────────────────────────────────────────
ARG TRID_VERSION=2.47
ARG TRID_DEFS_DATE=20260414
RUN mkdir -p /opt/trid \
    && cd /opt/trid \
    && curl -fsSL https://mark0.net/download/trid.zip -o trid.zip \
    && unzip -q trid.zip \
    && curl -fsSL https://mark0.net/download/triddefs.zip -o triddefs.zip \
    && unzip -q triddefs.zip \
    && if [ -f TrIDDefs.TRD ]; then mv TrIDDefs.TRD triddefs.trd; fi \
    && chmod +x trid* \
    && if [ -f /opt/trid/trid.py ]; then ln -sf /opt/trid/trid.py /usr/local/bin/trid.real; else ln -sf /opt/trid/trid /usr/local/bin/trid.real; fi \
    && printf '%s\n' '#!/usr/bin/env bash' \
      'if [[ "${1:-}" == "--version" ]]; then echo "TrID v'"$TRID_VERSION"'"; exit 0; fi' \
      'cd /opt/trid && exec /usr/local/bin/trid.real "$@"' \
      > /usr/local/bin/trid \
    && chmod +x /usr/local/bin/trid \
    && trid --version

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

# ── Build version (surfaced in /status, the supervisor banner, and the web UI
#    footer so a running container can be matched to a build) ──────────────────
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# Marker read by lib/storage_guard.py so the overlay-storage guard enforces only
# inside the container, where an unmounted data root means silent data loss.
ENV HDD_IN_CONTAINER=1
LABEL org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/joanmarcriera/hdd-recovery"

# ── Port ──────────────────────────────────────────────────────────────────────
# 7788  unified web UI: review pages, /terminal/, /health, /status
EXPOSE 7788

ENTRYPOINT ["/usr/local/bin/hdd-supervisor"]
