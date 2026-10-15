from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from srx_api.backup import BackupValidationError, create_sqlite_backup, restore_sqlite_backup, validate_sqlite_database


def _make_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE parent (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE child (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(id))")
        conn.execute("INSERT INTO parent(id) VALUES ('p1')")
        conn.execute("INSERT INTO child(id, parent_id) VALUES ('c1', 'p1')")


def test_backup_is_valid_and_contains_source_data(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "backups" / "backup.sqlite3"
    _make_db(source)

    result = create_sqlite_backup(source, destination)

    assert result == destination.resolve()
    validate_sqlite_database(destination)
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT parent_id FROM child WHERE id='c1'").fetchone() == ("p1",)


def test_backup_refuses_to_overwrite_by_default(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "backup.sqlite3"
    _make_db(source)
    destination.write_bytes(b"keep me")

    with pytest.raises(FileExistsError):
        create_sqlite_backup(source, destination)
    assert destination.read_bytes() == b"keep me"


def test_backup_rejects_same_source_and_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    _make_db(source)
    with pytest.raises(ValueError):
        create_sqlite_backup(source, source)


def test_validation_rejects_foreign_key_violations(tmp_path: Path) -> None:
    path = tmp_path / "broken.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE parent (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE child (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(id))")
        conn.execute("INSERT INTO child(id, parent_id) VALUES ('orphan', 'missing')")

    with pytest.raises(BackupValidationError, match="foreign_key_check"):
        validate_sqlite_database(path)


def test_restore_creates_verified_database_with_same_data(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored" / "restored.sqlite3"
    _make_db(source)
    create_sqlite_backup(source, backup)

    result = restore_sqlite_backup(backup, restored)

    assert result == restored.resolve()
    validate_sqlite_database(restored)
    with sqlite3.connect(restored) as conn:
        assert conn.execute("SELECT parent_id FROM child WHERE id='c1'").fetchone() == ("p1",)


def test_restore_refuses_to_overwrite_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    destination = tmp_path / "restored.sqlite3"
    _make_db(source)
    create_sqlite_backup(source, backup)
    destination.write_bytes(b"keep me")

    with pytest.raises(FileExistsError):
        restore_sqlite_backup(backup, destination)
    assert destination.read_bytes() == b"keep me"


def test_restore_rejects_corrupt_backup_without_publishing(tmp_path: Path) -> None:
    backup = tmp_path / "corrupt.sqlite3"
    destination = tmp_path / "restored.sqlite3"
    backup.write_bytes(b"not a sqlite database")

    with pytest.raises(BackupValidationError):
        restore_sqlite_backup(backup, destination)
    assert not destination.exists()


def test_restore_rejects_same_backup_and_destination(tmp_path: Path) -> None:
    backup = tmp_path / "backup.sqlite3"
    _make_db(backup)
    with pytest.raises(ValueError):
        restore_sqlite_backup(backup, backup)
