"""Cross-image inventory aggregation for the whole recovery haul.

Everything else in the toolkit is per-image (one ``*.analysis.sqlite`` per disk).
This module walks *all* catalogs under a root and rolls their counts and
highest-value hits up into one structure, so the operator can see the entire
haul at once. It is shared by the CLI (``bin/inventory-summary.py``, which
renders markdown/JSON/CSV) and the ``/inventory`` web route, so the two can
never disagree.

Strictly read-only: it opens each catalog ``mode=ro`` and never writes. Missing
tables/columns on older catalogs degrade to zero rather than raising.
"""
from __future__ import annotations

import csv
import io
import os
from typing import Any

from lib import harvest
from lib.db import ro_db

# Feature files whose distinct values are worth counting across the whole haul.
BULK_FEATURES = ("bitcoin.txt", "ethereum.txt", "email.txt", "domain.txt")

# How many rows to keep in each cross-disk highlight section.
TOP_WALLETS = 40
PER_DISK_WALLETS = 15
TOP_FINDINGS = 60


def _scalar(conn, sql, default: Any = 0):
    try:
        row = conn.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default


def _rows(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def _disk_summary(db_path):
    """Per-disk counts for the inventory table."""
    label = harvest.db_disk_label(db_path)
    row = {
        "db_path": db_path,
        "label": label,
        "image_path": "",
        "image_size": 0,
        "ddrescue_map_path": "",
        "files": 0,
        "deleted": 0,
        "artifacts": 0,
        "by_method": {},
        "photos": harvest.count_photos(db_path),
        "docs": harvest.count_docs(db_path),
        "wallet_candidates": 0,
        "findings_high": 0,
        "encrypted": 0,
        "last_run": "",
    }
    try:
        with ro_db(db_path) as conn:
            info = conn.execute(
                "SELECT image_path, image_size_bytes, ddrescue_map_path "
                "FROM image_info WHERE id=1"
            ).fetchone()
            if info:
                row["image_path"] = info["image_path"] or ""
                row["image_size"] = info["image_size_bytes"] or 0
                row["ddrescue_map_path"] = info["ddrescue_map_path"] or ""
            if not row["image_size"] and row["image_path"]:
                try:
                    row["image_size"] = os.path.getsize(row["image_path"])
                except OSError:
                    pass
            row["files"] = _scalar(conn, "SELECT COUNT(*) FROM files")
            row["deleted"] = _scalar(conn, "SELECT COUNT(*) FROM files WHERE deleted=1")
            row["artifacts"] = _scalar(conn, "SELECT COUNT(*) FROM recovered_artifacts")
            row["by_method"] = {
                m: c for m, c in _rows(
                    conn, "SELECT method, COUNT(*) FROM recovered_artifacts GROUP BY method")
            }
            row["wallet_candidates"] = _scalar(conn, "SELECT COUNT(*) FROM wallet_candidates")
            row["findings_high"] = _scalar(
                conn, "SELECT COUNT(*) FROM findings WHERE score >= 70")
            row["encrypted"] = _scalar(
                conn, "SELECT COUNT(*) FROM findings WHERE source_tool='encrypted-detect'")
            row["last_run"] = _scalar(
                conn, "SELECT MAX(COALESCE(ended_at, started_at)) FROM scan_runs", "")
    except Exception:
        pass
    return row


def _disk_highlights(db_path, label):
    """Cross-disk highlight rows for one disk (wallets, sensitive findings)."""
    wallets, findings, encrypted = [], [], []
    try:
        with ro_db(db_path) as conn:
            for r in _rows(conn,
                "SELECT wc.score, wc.reason, f.path, f.size_bytes, f.deleted "
                "FROM wallet_candidates wc JOIN files f ON f.id = wc.file_id "
                "ORDER BY wc.score DESC LIMIT ?", (PER_DISK_WALLETS,)):
                wallets.append({"disk": label, "score": r["score"], "reason": r["reason"],
                                "path": r["path"], "size_bytes": r["size_bytes"],
                                "deleted": r["deleted"]})
            for r in _rows(conn,
                "SELECT category, key, value, score, COALESCE(path,'') AS path "
                "FROM findings WHERE category IN ('wallet','seed_phrase') "
                "ORDER BY score DESC LIMIT ?", (TOP_FINDINGS,)):
                findings.append({"disk": label, "category": r["category"], "key": r["key"],
                                 "value": (r["value"] or "")[:120], "score": r["score"],
                                 "path": r["path"]})
            for r in _rows(conn,
                "SELECT category, key, value, score, COALESCE(path,'') AS path "
                "FROM findings WHERE source_tool='encrypted-detect' "
                "ORDER BY score DESC LIMIT ?", (TOP_FINDINGS,)):
                encrypted.append({"disk": label, "category": r["category"], "key": r["key"],
                                  "value": (r["value"] or "")[:120], "score": r["score"],
                                  "path": r["path"]})
    except Exception:
        pass
    return wallets, findings, encrypted


def _bulk_unique(db_path):
    """Distinct bulk_extractor values per feature file for one disk."""
    out = {feat: set() for feat in BULK_FEATURES}
    try:
        with ro_db(db_path) as conn:
            for feat in BULK_FEATURES:
                for (val,) in _rows(conn,
                    "SELECT DISTINCT value FROM bulk_extractor_hits WHERE feature_file=?",
                    (feat,)):
                    if val:
                        out[feat].add(val)
    except Exception:
        pass
    return out


def collect_inventory(root):
    """Aggregate every catalog under ``root`` into one inventory structure.

    The caller stamps ``generated_at`` (timestamps are injected, never taken
    from the clock here, to keep outputs reproducible/testable).
    """
    disks = [_disk_summary(db) for db in harvest.iter_databases(root)]

    totals = {
        "disks": len(disks),
        "image_size": sum(d["image_size"] for d in disks),
        "files": sum(d["files"] for d in disks),
        "deleted": sum(d["deleted"] for d in disks),
        "artifacts": sum(d["artifacts"] for d in disks),
        "photos": sum(d["photos"] for d in disks),
        "docs": sum(d["docs"] for d in disks),
        "wallet_candidates": sum(d["wallet_candidates"] for d in disks),
        "findings_high": sum(d["findings_high"] for d in disks),
        "encrypted": sum(d["encrypted"] for d in disks),
    }

    wallets, findings, encrypted = [], [], []
    bulk_sets = {feat: set() for feat in BULK_FEATURES}
    for d in disks:
        w, f, e = _disk_highlights(d["db_path"], d["label"])
        wallets += w
        findings += f
        encrypted += e
        for feat, vals in _bulk_unique(d["db_path"]).items():
            bulk_sets[feat] |= vals

    wallets.sort(key=lambda r: (r["score"] or 0), reverse=True)
    findings.sort(key=lambda r: (r["score"] or 0), reverse=True)
    encrypted.sort(key=lambda r: (r["score"] or 0), reverse=True)

    return {
        "root": root,
        "disks": disks,
        "totals": totals,
        "top_wallets": wallets[:TOP_WALLETS],
        "wallet_findings": findings[:TOP_FINDINGS],
        "encrypted": encrypted[:TOP_FINDINGS],
        "bulk_unique": {feat: len(vals) for feat, vals in bulk_sets.items()},
    }


# ── renderers (CLI) ──────────────────────────────────────────────────────────

def _human_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            s = f"{n:.1f}".rstrip("0").rstrip(".")
            return f"{s} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def render_markdown(data, generated_at):
    t = data["totals"]
    out = []
    out.append("# Cross-Image Recovery Inventory\n")
    out.append(f"_Generated: {generated_at}_\n")
    out.append(f"_Root: `{data['root']}`_\n")

    out.append("\n## Totals\n")
    out.append("| Metric | Value |")
    out.append("| --- | --- |")
    out.append(f"| Images | {t['disks']:,} |")
    out.append(f"| Total image size | {_human_size(t['image_size'])} |")
    out.append(f"| Files inventoried | {t['files']:,} |")
    out.append(f"| Deleted files | {t['deleted']:,} |")
    out.append(f"| Recovered artifacts | {t['artifacts']:,} |")
    out.append(f"| Curated photos | {t['photos']:,} |")
    out.append(f"| Curated documents | {t['docs']:,} |")
    out.append(f"| Wallet candidates | {t['wallet_candidates']:,} |")
    out.append(f"| High-interest findings (score ≥ 70) | {t['findings_high']:,} |")
    out.append(f"| Encrypted-container findings | {t['encrypted']:,} |")

    bu = data["bulk_unique"]
    out.append("\n### Unique bulk_extractor values across all disks\n")
    out.append("| Feature | Unique values |")
    out.append("| --- | --- |")
    for feat in BULK_FEATURES:
        out.append(f"| {feat} | {bu.get(feat, 0):,} |")

    out.append("\n## Per-disk breakdown\n")
    out.append("| Disk | Size | Files | Deleted | Artifacts | Photos | Docs | Wallets | Hi-find | Enc | Last run |")
    out.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for d in data["disks"]:
        out.append(
            f"| {d['label']} | {_human_size(d['image_size'])} | {d['files']:,} | "
            f"{d['deleted']:,} | {d['artifacts']:,} | {d['photos']:,} | {d['docs']:,} | "
            f"{d['wallet_candidates']:,} | {d['findings_high']:,} | {d['encrypted']:,} | "
            f"{str(d['last_run'])[:19]} |"
        )

    if data["top_wallets"]:
        out.append("\n## Top wallet candidates (all disks)\n")
        out.append("| Disk | Score | Reason | Path | Bytes | Del |")
        out.append("| --- | ---: | --- | --- | ---: | :-: |")
        for w in data["top_wallets"]:
            out.append(
                f"| {w['disk']} | {w['score']} | {w['reason']} | {w['path']} | "
                f"{w['size_bytes'] if w['size_bytes'] is not None else '?'} | "
                f"{w['deleted'] if w['deleted'] is not None else '?'} |")

    if data["wallet_findings"]:
        out.append("\n## Wallet / seed-phrase findings (all disks)\n")
        out.append("| Disk | Category | Key | Value | Score | Path |")
        out.append("| --- | --- | --- | --- | ---: | --- |")
        for f in data["wallet_findings"]:
            out.append(
                f"| {f['disk']} | {f['category']} | {f['key']} | {f['value']} | "
                f"{f['score']} | {f['path']} |")

    if data["encrypted"]:
        out.append("\n## Encrypted containers (all disks)\n")
        out.append("| Disk | Category | Key | Value | Score | Path |")
        out.append("| --- | --- | --- | --- | ---: | --- |")
        for e in data["encrypted"]:
            out.append(
                f"| {e['disk']} | {e['category']} | {e['key']} | {e['value']} | "
                f"{e['score']} | {e['path']} |")

    out.append("")
    return "\n".join(out)


def render_csv(data):
    """Per-disk breakdown as CSV (the machine-readable disk table)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["disk", "image_path", "image_size_bytes", "files", "deleted",
                "artifacts", "photos", "docs", "wallet_candidates",
                "findings_high", "encrypted", "last_run"])
    for d in data["disks"]:
        w.writerow([d["label"], d["image_path"], d["image_size"], d["files"],
                    d["deleted"], d["artifacts"], d["photos"], d["docs"],
                    d["wallet_candidates"], d["findings_high"], d["encrypted"],
                    d["last_run"]])
    return buf.getvalue()
