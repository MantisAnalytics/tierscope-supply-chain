"""SQLite schema of the graph store and the table constants derived from it."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    counts_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    type TEXT NOT NULL,
    id TEXT NOT NULL,
    referenced_only INTEGER NOT NULL,
    PRIMARY KEY (type, id)
);
CREATE TABLE IF NOT EXISTS node_sources (
    type TEXT NOT NULL,
    id TEXT NOT NULL,
    source_json TEXT NOT NULL,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (type, id, source_json)
);
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    key_json TEXT NOT NULL,
    src_type TEXT NOT NULL,
    src_id TEXT NOT NULL,
    dst_type TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    UNIQUE (type, key_json)
);
CREATE INDEX IF NOT EXISTS edges_src ON edges (src_type, src_id);
CREATE INDEX IF NOT EXISTS edges_dst ON edges (dst_type, dst_id);
CREATE TABLE IF NOT EXISTS edge_sources (
    edge_id INTEGER NOT NULL,
    source_json TEXT NOT NULL,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (edge_id, source_json)
);
CREATE TABLE IF NOT EXISTS field_values (
    id INTEGER PRIMARY KEY,
    owner_kind TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    field TEXT NOT NULL,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    method TEXT NOT NULL,
    source_json TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    locked INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 0,
    UNIQUE (owner_kind, owner_type, owner_key, field, value_json, source_json)
);
CREATE INDEX IF NOT EXISTS field_values_owner ON field_values (owner_type, owner_key);
CREATE TABLE IF NOT EXISTS oracle_values (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    source_json TEXT NOT NULL,
    anomaly TEXT,
    PRIMARY KEY (entity_type, entity_id, metric)
);
CREATE TABLE IF NOT EXISTS parameters (
    name TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    source_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    group_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    summary TEXT NOT NULL,
    created_run TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    comment TEXT,
    notes_json TEXT NOT NULL DEFAULT '[]',
    impact_json TEXT,
    impact_version INTEGER
);
CREATE INDEX IF NOT EXISTS proposals_group ON proposals (kind, group_key, status);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    summary TEXT NOT NULL,
    items_json TEXT NOT NULL,
    inverse_json TEXT NOT NULL,
    undone_by INTEGER,
    origin TEXT                       -- where an edit came from, e.g. thread:7 for an agent thread
);
CREATE TABLE IF NOT EXISTS deletions (
    item_kind TEXT NOT NULL,
    type TEXT NOT NULL,
    key TEXT NOT NULL,
    field TEXT,
    author TEXT NOT NULL,
    ts TEXT NOT NULL,
    reason TEXT,
    sources_json TEXT NOT NULL,
    journal_id INTEGER
);
CREATE TABLE IF NOT EXISTS aliases (
    type TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    journal_id INTEGER,
    PRIMARY KEY (type, from_id)
);
CREATE TABLE IF NOT EXISTS rejections (
    fingerprint TEXT PRIMARY KEY
);
"""

# Columns added after a table was first created; added to existing databases on open.
REVIEW_COLUMNS = ("review TEXT NOT NULL DEFAULT 'accepted'", "proposal_id INTEGER", "origin_json TEXT")
ADDED_COLUMNS = {
    "node_sources": ("resolution_json TEXT", *REVIEW_COLUMNS),
    "edge_sources": ("resolution_json TEXT", *REVIEW_COLUMNS),
    "field_values": ("resolution_json TEXT", *REVIEW_COLUMNS),
    "journal": ("origin TEXT",),
}

# Id prefix of nodes created for document mentions that matched no node.
UNRESOLVED_PREFIX = "mention:"
REVIEW_STATES = ("accepted", "proposed", "rejected", "superseded")

TABLES = ("runs", "nodes", "node_sources", "edges", "edge_sources", "field_values", "oracle_values", "parameters",
          "proposals", "journal", "deletions", "aliases", "rejections")
FACT_TABLES = ("node_sources", "edge_sources", "field_values")
# Tables whose `id INTEGER PRIMARY KEY` is the rowid.
ROWID_ALIASED = ("edges", "field_values", "proposals", "journal")
