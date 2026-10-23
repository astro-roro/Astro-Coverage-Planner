"""SQLite connection layer for `schedulerdb.sqlite`.

Three responsibilities:
- Open the connection with sensible defaults (busy_timeout, no FK enforcement
  to match the plugin's own behaviour, row factory for dict-style access).
- Verify `PRAGMA user_version` is in the allowlist before any write attempt.
- Make a sibling backup copy before the first write of a session.
"""
from __future__ import annotations

import contextlib
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

# The schema version(s) this extension is known to be compatible with.
# Bump when we've tested against a newer TS plugin schema. Every step from 23
# to 28 is an additive ALTER TABLE ADD COLUMN and only one of them touches a
# table we write, so a single code path with version checks covers the range.
# See docs/schema-history.md for the per-version diff and the citations.
SUPPORTED_USER_VERSIONS: tuple[int, ...] = (23, 24, 25, 26, 27, 28)

DEFAULT_BUSY_TIMEOUT_MS = 10_000


class SchemaVersionError(RuntimeError):
    """Raised when the live DB's user_version is outside our allowlist."""

    def __init__(self, found: int, supported: tuple[int, ...]) -> None:
        self.found = found
        self.supported = supported
        super().__init__(
            f"Target Scheduler DB is at PRAGMA user_version={found}; "
            f"this extension supports {sorted(supported)}. "
            "Refusing to write — see the extension README for the compat matrix."
        )


def open_connection(db_path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the DB. Always sets `busy_timeout`. Read-only uses URI mode."""
    if read_only:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, isolation_level=None
        )
    else:
        conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
    # The plugin doesn't enable FK enforcement, so we don't either — adding
    # it could cause inserts in a particular order to suddenly start failing
    # against DBs that work fine for the plugin.
    return conn


def get_user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def assert_supported_version(conn: sqlite3.Connection) -> int:
    """Verify the schema is one we support, or raise SchemaVersionError."""
    version = get_user_version(conn)
    if version not in SUPPORTED_USER_VERSIONS:
        raise SchemaVersionError(version, SUPPORTED_USER_VERSIONS)
    return version


def backup_db(db_path: Path) -> Path:
    """Copy the DB file to a sibling `*-acpsync-<ts>-backup.sqlite`.

    Uses `shutil.copy2` rather than the `sqlite3.Connection.backup` API
    because the plugin's connection is closed between operations, the file
    isn't huge, and a plain copy preserves any sidecar -wal/-shm files if
    journal mode ever changes.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = db_path.with_name(f"{db_path.stem}-acpsync-{stamp}-backup{db_path.suffix}")
    shutil.copy2(db_path, dst)
    return dst


@contextlib.contextmanager
def write_transaction(
    db_path: Path, *, make_backup: bool = True
) -> Iterator[tuple[sqlite3.Connection, Path | None]]:
    """Open the DB, verify schema, take a backup, run the body in a tx.

    Yields ``(conn, backup_path)``. Commits on clean exit, rolls back on any
    exception. The backup is taken *before* the transaction begins so if the
    write phase fails, the user has an exact copy of the pre-write state.
    """
    backup_path: Path | None = backup_db(db_path) if make_backup else None
    conn = open_connection(db_path, read_only=False)
    try:
        assert_supported_version(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn, backup_path
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    finally:
        conn.close()
