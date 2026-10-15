from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY NOT NULL,
 title TEXT NOT NULL CHECK(length(trim(title)) > 0),
 origin TEXT NOT NULL CHECK(length(trim(origin)) > 0),
 retrieved_at TEXT NOT NULL,
 epistemic_type TEXT NOT NULL CHECK(epistemic_type IN ('historical','traditional','research_hypothesis','empirically_tested')),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded','retracted','unverified')),
 fingerprint TEXT,
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_created ON sources(created_at DESC, id ASC);
CREATE TABLE IF NOT EXISTS observations (
 id TEXT PRIMARY KEY NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
 statement TEXT NOT NULL CHECK(length(trim(statement)) > 0),
 epistemic_type TEXT NOT NULL CHECK(epistemic_type IN ('historical','traditional','research_hypothesis','empirically_tested')),
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_source ON observations(source_id);
CREATE TABLE IF NOT EXISTS rules (
 id TEXT PRIMARY KEY NOT NULL,
 epistemic_type TEXT NOT NULL CHECK(epistemic_type IN ('historical','traditional','research_hypothesis','empirically_tested')),
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenarios (
 id TEXT PRIMARY KEY NOT NULL,
 description TEXT NOT NULL CHECK(length(trim(description)) > 0),
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecasts (
 id TEXT PRIMARY KEY NOT NULL,
 scenario_id TEXT NOT NULL REFERENCES scenarios(id) ON DELETE RESTRICT,
 question TEXT NOT NULL CHECK(length(trim(question)) > 0),
 ranking_score REAL,
 probability REAL CHECK(probability IS NULL OR (probability >= 0 AND probability <= 1)),
 probability_type TEXT NOT NULL DEFAULT 'unassigned' CHECK(probability_type IN ('unassigned','ranking_score','probability')),
 calibration_status TEXT NOT NULL DEFAULT 'uncalibrated' CHECK(calibration_status IN ('uncalibrated','calibrated','externally_validated')),
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forecasts_scenario ON forecasts(scenario_id);
CREATE TABLE IF NOT EXISTS audit_events (
 id TEXT PRIMARY KEY NOT NULL,
 actor_subject TEXT NOT NULL CHECK(length(trim(actor_subject)) > 0),
 actor_issuer TEXT NOT NULL CHECK(length(trim(actor_issuer)) > 0),
 action TEXT NOT NULL,
 resource_type TEXT NOT NULL,
 resource_id TEXT NOT NULL,
 created_at TEXT NOT NULL,
 details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_events_resource ON audit_events(resource_type, resource_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_actor ON audit_events(actor_subject, created_at DESC);
CREATE TABLE IF NOT EXISTS api_tokens (
 id TEXT PRIMARY KEY NOT NULL,
 owner_subject TEXT NOT NULL CHECK(length(trim(owner_subject)) > 0),
 owner_issuer TEXT NOT NULL CHECK(length(trim(owner_issuer)) > 0),
 label TEXT NOT NULL CHECK(length(trim(label)) BETWEEN 1 AND 100),
 fingerprint TEXT NOT NULL UNIQUE CHECK(length(fingerprint) = 64),
 token_prefix TEXT NOT NULL CHECK(length(token_prefix) <= 16),
 created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 revoked_at TEXT,
 last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_owner ON api_tokens(owner_issuer, owner_subject, created_at DESC);
CREATE TABLE IF NOT EXISTS concepts (
 id TEXT PRIMARY KEY NOT NULL,
 label TEXT NOT NULL CHECK(length(trim(label)) > 0),
 system_id TEXT NOT NULL CHECK(length(trim(system_id)) > 0),
 concept_type TEXT NOT NULL CHECK(concept_type IN ('historical_practice','modern_practice','research_concept','entity','event')),
 epistemic_status TEXT NOT NULL CHECK(epistemic_status IN ('documented','disputed','inferred','synthetic')),
 description TEXT NOT NULL DEFAULT '',
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_concepts_system ON concepts(system_id, id);
CREATE TABLE IF NOT EXISTS concept_relations (
 id TEXT PRIMARY KEY NOT NULL,
 from_concept_id TEXT NOT NULL REFERENCES concepts(id) ON DELETE RESTRICT,
 relation_type TEXT NOT NULL CHECK(length(trim(relation_type)) > 0),
 to_concept_id TEXT NOT NULL REFERENCES concepts(id) ON DELETE RESTRICT,
 system_id TEXT NOT NULL CHECK(length(trim(system_id)) > 0),
 evidence_status TEXT NOT NULL CHECK(evidence_status IN ('documented','disputed','inferred','synthetic')),
 source_id TEXT REFERENCES sources(id) ON DELETE RESTRICT,
 rationale TEXT NOT NULL CHECK(length(trim(rationale)) > 0),
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 CHECK(from_concept_id <> to_concept_id),
 CHECK(evidence_status = 'synthetic' OR source_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_concept_relations_from ON concept_relations(from_concept_id);
CREATE INDEX IF NOT EXISTS idx_concept_relations_to ON concept_relations(to_concept_id);
CREATE TABLE IF NOT EXISTS evaluations (
 id TEXT PRIMARY KEY NOT NULL,
 forecast_id TEXT NOT NULL REFERENCES forecasts(id) ON DELETE RESTRICT,
 outcome INTEGER NOT NULL CHECK(outcome IN (0,1)),
 observed_at TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
"""

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class Database:
    def __init__(self, path: str):
        self.path = path
        self._memory_connection: sqlite3.Connection | None = None
        if path == ":memory:":
            self._memory_connection = self._connect()
            self._memory_connection.executescript(SCHEMA)
            self._memory_connection.commit()
        else:
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            with self.connection() as conn:
                conn.executescript(SCHEMA)
                conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if self._memory_connection is not None:
            yield self._memory_connection
            return
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def readiness(self) -> bool:
        # Table names alone are insufficient: a stale or manually-created table can
        # exist while lacking columns required by the API. Fail closed in that case.
        required_columns = {
            "sources": {"id", "title", "origin", "retrieved_at", "epistemic_type", "status", "payload_json", "created_at"},
            "observations": {"id", "source_id", "statement", "epistemic_type", "payload_json", "created_at"},
            "rules": {"id", "epistemic_type", "payload_json", "created_at"},
            "scenarios": {"id", "description", "payload_json", "created_at"},
            "forecasts": {"id", "scenario_id", "question", "probability", "probability_type", "calibration_status", "payload_json", "created_at"},
            "audit_events": {"id", "actor_subject", "actor_issuer", "action", "resource_type", "resource_id", "created_at", "details_json"},
            "api_tokens": {"id", "owner_subject", "owner_issuer", "label", "fingerprint", "token_prefix", "created_at", "expires_at", "revoked_at", "last_used_at"},
            "evaluations": {"id", "forecast_id", "outcome", "observed_at", "payload_json", "created_at"},
            "concepts": {"id", "label", "system_id", "concept_type", "epistemic_status", "description", "payload_json", "created_at"},
            "concept_relations": {"id", "from_concept_id", "relation_type", "to_concept_id", "system_id", "evidence_status", "source_id", "rationale", "payload_json", "created_at"},
        }
        try:
            with self.connection() as conn:
                for table, expected in required_columns.items():
                    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                    actual = {row[1] for row in rows}
                    if not expected.issubset(actual):
                        return False
                foreign_keys_enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
                foreign_key_violations = conn.execute("PRAGMA foreign_key_check").fetchone()
            return foreign_keys_enabled and foreign_key_violations is None
        except (sqlite3.Error, TypeError, IndexError):
            return False

def row_json(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    if "payload_json" in result:
        payload = result.pop("payload_json")
        result["payload"] = json.loads(payload)
    return result
