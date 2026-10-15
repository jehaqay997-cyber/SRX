import sqlite3

from srx_api.database import Database


def test_fresh_database_is_ready(tmp_path):
    db = Database(str(tmp_path / "fresh.sqlite3"))
    assert db.readiness() is True


def test_existing_database_with_incomplete_table_is_not_ready(tmp_path):
    path = tmp_path / "stale.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sources (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    # Readiness must detect a stale schema even if initialization cannot upgrade it.
    db = object.__new__(Database)
    db.path = str(path)
    db._memory_connection = None
    assert db.readiness() is False


def test_readiness_detects_foreign_key_violations(tmp_path):
    path = tmp_path / "broken_fk.sqlite3"
    db = Database(str(path))
    with db.connection() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO observations(id,source_id,statement,epistemic_type,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            ("orphan-observation", "missing-source", "test", "historical", "{}", "2026-01-01T00:00:00+00:00"),
        )
    assert db.readiness() is False
