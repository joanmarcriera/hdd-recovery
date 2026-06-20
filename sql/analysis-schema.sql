PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS image_info (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  image_path TEXT NOT NULL,
  image_name TEXT NOT NULL,
  image_basename TEXT NOT NULL,
  image_sha256 TEXT,
  image_size_bytes INTEGER,
  image_mtime_epoch INTEGER,
  ddrescue_map_path TEXT,
  export_root TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_runs (
  id INTEGER PRIMARY KEY,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  command_line TEXT,
  log_path TEXT,
  output_dir TEXT,
  notes TEXT,
  -- Supervision/durability (see lib/runs.py): record the owning process so a row
  -- left 'running' by a kill/crash/restart can be reconciled to 'interrupted'.
  pid INTEGER,
  pgid INTEGER,
  host TEXT,
  heartbeat_at TEXT,
  last_progress_at TEXT,
  cancel_requested INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS supervised_runs (
  id INTEGER PRIMARY KEY,
  run_kind TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  command_line TEXT,
  log_path TEXT,
  pid INTEGER,
  pgid INTEGER,
  host TEXT,
  heartbeat_at TEXT,
  last_progress_at TEXT,
  cancel_requested INTEGER DEFAULT 0,
  exit_code INTEGER,
  notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_supervised_status
  ON supervised_runs(status, run_kind, started_at);

CREATE TABLE IF NOT EXISTS partitions (
  id INTEGER PRIMARY KEY,
  slot TEXT,
  start_sector INTEGER,
  end_sector INTEGER,
  length_sectors INTEGER,
  sector_size INTEGER,
  table_type TEXT,
  description TEXT,
  filesystem_hint TEXT,
  mount_role TEXT,
  source TEXT NOT NULL DEFAULT 'structure-scan',
  UNIQUE(slot, start_sector, source)
);

CREATE TABLE IF NOT EXISTS filesystems (
  id INTEGER PRIMARY KEY,
  partition_id INTEGER,
  fs_type TEXT,
  label TEXT,
  block_size INTEGER,
  offset_sectors INTEGER,
  source TEXT NOT NULL,
  notes TEXT,
  FOREIGN KEY (partition_id) REFERENCES partitions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  partition_id INTEGER,
  source_tool TEXT NOT NULL,
  inode TEXT,
  path TEXT,
  name TEXT,
  extension TEXT,
  allocated INTEGER,
  deleted INTEGER,
  is_dir INTEGER,
  size_bytes INTEGER,
  uid TEXT,
  gid TEXT,
  mode TEXT,
  atime TEXT,
  mtime TEXT,
  ctime TEXT,
  crtime TEXT,
  md5 TEXT,
  sha1 TEXT,
  mime_type TEXT,
  notes TEXT,
  UNIQUE(partition_id, source_tool, inode, path),
  FOREIGN KEY (partition_id) REFERENCES partitions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS wallet_candidates (
  id INTEGER PRIMARY KEY,
  file_id INTEGER,
  source_stage TEXT NOT NULL,
  score INTEGER NOT NULL,
  reason TEXT NOT NULL,
  details TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(file_id, reason),
  FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS picture_candidates (
  id INTEGER PRIMARY KEY,
  file_id INTEGER,
  source_stage TEXT NOT NULL,
  score INTEGER NOT NULL,
  reason TEXT NOT NULL,
  details TEXT,
  width INTEGER,
  height INTEGER,
  camera_model TEXT,
  taken_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(file_id, reason),
  FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS recovered_artifacts (
  id INTEGER PRIMARY KEY,
  method TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  full_path TEXT NOT NULL,
  size_bytes INTEGER,
  sha256 TEXT,
  mime_type TEXT,
  file_output TEXT,
  source_run_id INTEGER,
  created_at TEXT NOT NULL,
  notes TEXT,
  trid_top_ext TEXT,
  trid_top_score REAL,
  trid_top3_json TEXT,
  dedup_cluster_id INTEGER,
  is_cluster_primary INTEGER DEFAULT 0,
  quality_score REAL,
  UNIQUE(method, relative_path),
  FOREIGN KEY (source_run_id) REFERENCES scan_runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS crack_tasks (
  id INTEGER PRIMARY KEY,
  cracker TEXT NOT NULL,
  target_artifact_id INTEGER,
  target_kind TEXT NOT NULL,
  hash_mode TEXT,
  wordlist_path TEXT,
  rules_path TEXT,
  checkpoint_path TEXT,
  progress_pct REAL,
  eta_seconds INTEGER,
  started_at TEXT,
  paused_at TEXT,
  ended_at TEXT,
  status TEXT NOT NULL,
  result_value TEXT,
  notes TEXT,
  FOREIGN KEY (target_artifact_id) REFERENCES recovered_artifacts(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_crack_status ON crack_tasks(status);

CREATE TABLE IF NOT EXISTS wallet_keys (
  id INTEGER PRIMARY KEY,
  source_artifact_id INTEGER,
  source_method TEXT NOT NULL,
  key_type TEXT NOT NULL,
  key_value TEXT NOT NULL,
  address TEXT,
  encrypted INTEGER NOT NULL DEFAULT 0,
  decrypt_passphrase TEXT,
  notes TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (source_artifact_id) REFERENCES recovered_artifacts(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_wallet_keys_type ON wallet_keys(key_type);

CREATE TABLE IF NOT EXISTS bulk_extractor_hits (
  id INTEGER PRIMARY KEY,
  source_scope TEXT NOT NULL,
  feature_file TEXT NOT NULL,
  offset_ref TEXT,
  value TEXT,
  context TEXT,
  source_run_id INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY (source_run_id) REFERENCES scan_runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY,
  source_kind TEXT NOT NULL,
  source_ref TEXT,
  relative_path TEXT NOT NULL,
  full_path TEXT NOT NULL,
  sha256 TEXT,
  size_bytes INTEGER,
  created_at TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension);
CREATE INDEX IF NOT EXISTS idx_wallet_candidates_score ON wallet_candidates(score DESC);
CREATE INDEX IF NOT EXISTS idx_picture_candidates_score ON picture_candidates(score DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_method ON recovered_artifacts(method);
CREATE INDEX IF NOT EXISTS idx_bulk_value ON bulk_extractor_hits(value);
CREATE INDEX IF NOT EXISTS idx_bulk_scope_file ON bulk_extractor_hits(source_scope, feature_file);

-- Unified findings table: output from exiftool, YARA, regripper, rifiuti2, plaso, pdf-extract.
-- source_tool: 'exiftool' | 'yara' | 'regripper' | 'rifiuti2' | 'plaso' | 'pdf-extract'
-- category:    'gps' | 'metadata' | 'wallet' | 'registry' | 'recycle_bin' | 'timeline' | 'seed_phrase'
-- key/value:   tool-specific attribute name and its value
-- score:       relevance 0-100 (0 = informational, ≥70 = high interest)
CREATE TABLE IF NOT EXISTS findings (
  id          INTEGER PRIMARY KEY,
  source_tool TEXT    NOT NULL,
  category    TEXT    NOT NULL,
  file_id     INTEGER,
  artifact_id INTEGER,
  path        TEXT,
  key         TEXT,
  value       TEXT,
  score       INTEGER DEFAULT 0,
  notes       TEXT,
  created_at  TEXT    NOT NULL,
  FOREIGN KEY (file_id)     REFERENCES files(id)               ON DELETE SET NULL,
  FOREIGN KEY (artifact_id) REFERENCES recovered_artifacts(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_tool     ON findings(source_tool);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category);
CREATE INDEX IF NOT EXISTS idx_findings_score    ON findings(score DESC);
CREATE INDEX IF NOT EXISTS idx_findings_key      ON findings(key);
