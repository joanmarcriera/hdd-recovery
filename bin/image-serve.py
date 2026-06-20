#!/usr/bin/env python3
"""
Read-only web query interface for hdd-recovery SQLite databases.

Serves a local HTTP UI that lets you browse all recovery databases under
a given root directory.  All SQL executed is restricted to SELECT/WITH.

Usage:
  image-serve.py [--root DIR] [--port PORT] [--host HOST]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Cohesive units split out of this module (#18). Re-exported names keep the
# public call sites (and tests) unchanged.
from lib.serve_auth import (  # noqa: E402,F401
    _WEB_AUTH_EXEMPT_PATHS,
    _auth_path_exempt,
    _basic_auth_credentials,
    _constant_time_equal,
    _webui_auth_config,
    _webui_auth_ok,
)
from lib.serve_db import (  # noqa: E402,F401
    _DISCOVERY_MAX_DEPTH,
    _DISCOVERY_PRUNE,
    db_export_root,
    find_databases,
    query_scalar,
    run_query,
    safe_sql,
)
from lib.serve_gallery import (  # noqa: E402,F401
    GALLERY_ALL_CAP,
    _CARVED_SORT_COLS,
    _FS_SORT_COLS,
    _resolve_under_root,
    _thumb_cache_dir,
    export_file_via_icat,
    make_thumbnail,
    page_gallery,
    page_gallery_fs,
)
from lib.serve_mapfile import (  # noqa: E402
    MAP_STATUS as _MAP_STATUS,
    map_svg as _map_svg,
    parse_mapfile,
)
from lib.serve_pipeline import (  # noqa: E402,F401
    PIPELINE_PRESETS,
    _db_log_path,
    _merge_preset_stages,
    _pipeline_pgrep_lines,
    _queue_log_dir,
    build_queue_cmd,
    cancel_active_pipeline,
    cancel_active_queue,
    list_pipeline_logs,
    page_pipeline_log,
    panel_pipeline,
    pipeline_active_for,
    pipeline_stop_requested,
    queue_active,
    queue_stop_requested,
    request_stop_active_pipeline,
    request_stop_active_queue,
    spawn_pipeline,
    spawn_queue,
)
from lib.serve_pages import (  # noqa: E402,F401
    _HOME_CACHE,
    _HOME_CACHE_TTL,
    _HOME_STAGE_GROUPS,
    _WALLET_DEDUP_SQL,
    _home_db_stats,
    _queue_progress_html,
    _runs_table_html,
    _stage_group_chips,
    page_artifacts,
    page_bulk_hits,
    page_db,
    page_files,
    page_findings,
    page_home,
    page_mapview,
    page_pictures,
    page_queue,
    page_queue_log,
    page_sql,
    page_timeline,
    page_wallets,
    queue_log_payload,
    wallet_dedup_counts,
)
from lib.serve_queue_log import (  # noqa: E402
    QPROG_CACHE as _QPROG_CACHE,
    QPROG_LOCK as _QPROG_LOCK,
    QUEUE_NOISE_RE as _QUEUE_NOISE_RE,
    collapse_queue_noise as _collapse_queue_noise,
    queue_progress_cached as _queue_progress_cached,
    queue_progress_html as _queue_progress_html_impl,
    scan_queue_progress as _scan_queue_progress,
)
from lib.serve_ui import (  # noqa: E402,F401
    APP_VERSION,
    CSS,
    SORT_JS,
    _bar,
    badge,
    h,
    human_size as _human_size,
    mem_panel,
    mem_status,
    page,
    table_html,
)

# ── request handler and entry point live in lib/serve_app.py ──────────────────

from lib.serve_app import Handler, _WEB_AUTH_REALM, main  # noqa: E402,F401


if __name__ == "__main__":
    main()
