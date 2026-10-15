from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path


class BackupValidationError(RuntimeError):
    """Raised when a SQLite backup cannot be verified as consistent."""


def validate_sqlite_database(path: str | Path) -> None:
    """Validate SQLite integrity and foreign-key consistency without modifying data."""
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {target}")
    try:
        with sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True, timeout=10) as conn:
            integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
            if integrity_rows != [("ok",)]:
                details = "; ".join(str(row[0]) for row in integrity_rows[:10])
                raise BackupValidationError(f"SQLite integrity_check failed: {details}")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                details = "; ".join(repr(tuple(row)) for row in violations[:10])
                raise BackupValidationError(f"SQLite foreign_key_check failed: {details}")
    except sqlite3.DatabaseError as exc:
        raise BackupValidationError(f"SQLite database could not be validated: {exc}") from exc


def create_sqlite_backup(source: str | Path, destination: str | Path, *, overwrite: bool = False) -> Path:
    """Create a consistent SQLite backup, validate it, and publish it atomically.

    The temporary file is created in the destination directory so os.replace is
    atomic on the same filesystem. Existing backups are protected by default.
    """
    src = Path(source).expanduser().resolve()
    dst = Path(destination).expanduser().resolve()
    if src == dst:
        raise ValueError("Source and destination must be different files")
    if not src.is_file():
        raise FileNotFoundError(f"SQLite source database does not exist: {src}")
    if dst.exists() and not overwrite:
        raise FileExistsError(f"Backup destination already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    validate_sqlite_database(src)

    fd, temporary_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=dst.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with sqlite3.connect(src, timeout=10) as source_conn:
            with sqlite3.connect(temporary, timeout=10) as backup_conn:
                source_conn.backup(backup_conn)
                backup_conn.commit()
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            # Permission hardening is best-effort on platforms without POSIX modes.
            pass
        validate_sqlite_database(temporary)
        if dst.exists() and not overwrite:
            # Recheck just before publication to reduce accidental overwrite risk.
            raise FileExistsError(f"Backup destination already exists: {dst}")
        os.replace(temporary, dst)
        return dst
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def restore_sqlite_backup(backup: str | Path, destination: str | Path, *, overwrite: bool = False) -> Path:
    """Restore a verified SQLite backup to a new path and publish atomically.

    This never overwrites an existing destination unless explicitly requested.
    The backup is validated before copying and the restored database is validated
    again before it becomes visible at the destination path.
    """
    src = Path(backup).expanduser().resolve()
    dst = Path(destination).expanduser().resolve()
    if src == dst:
        raise ValueError("Backup and restore destination must be different files")
    if not src.is_file():
        raise FileNotFoundError(f"SQLite backup does not exist: {src}")
    if dst.exists() and not overwrite:
        raise FileExistsError(f"Restore destination already exists: {dst}")

    validate_sqlite_database(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".restore.tmp", dir=dst.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=10) as source_conn:
            with sqlite3.connect(temporary, timeout=10) as target_conn:
                source_conn.backup(target_conn)
                target_conn.commit()
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        validate_sqlite_database(temporary)
        if dst.exists() and not overwrite:
            raise FileExistsError(f"Restore destination already exists: {dst}")
        os.replace(temporary, dst)
        return dst
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
