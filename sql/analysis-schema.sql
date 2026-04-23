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
  notes TEXT
);

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
  UNIQUE(method, relative_path),
  FOREIGN KEY (source_run_id) REFERENCES scan_runs(id) ON DELETE SET NULL
);

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
