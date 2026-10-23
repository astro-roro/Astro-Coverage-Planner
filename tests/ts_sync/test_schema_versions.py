"""The version gate: 23 through 28 pass, anything else is refused."""
from __future__ import annotations

import sqlite3

import pytest

from nina_ts_sync.db import (
    SUPPORTED_USER_VERSIONS,
    SchemaVersionError,
    assert_supported_version,
    get_user_version,
    open_connection,
)
from .make_db import make_db


def test_supported_range_is_23_through_28():
    assert SUPPORTED_USER_VERSIONS == (23, 24, 25, 26, 27, 28)


@pytest.mark.parametrize("version", SUPPORTED_USER_VERSIONS)
def test_supported_version_opens_and_passes(tmp_path, version):
    path = make_db(version, tmp_path / "schedulerdb.sqlite")
    conn = open_connection(path)
    try:
        assert get_user_version(conn) == version
        assert assert_supported_version(conn) == version
    finally:
        conn.close()


@pytest.mark.parametrize("version", [22, 29])
def test_unsupported_version_is_refused(tmp_path, version):
    path = make_db(version, tmp_path / "schedulerdb.sqlite")
    conn = open_connection(path)
    try:
        with pytest.raises(SchemaVersionError) as excinfo:
            assert_supported_version(conn)
    finally:
        conn.close()

    err = excinfo.value
    assert err.found == version
    assert err.supported == SUPPORTED_USER_VERSIONS
    # Same message shape as before the range widened, just a longer list.
    assert str(err).startswith(
        f"Target Scheduler DB is at PRAGMA user_version={version}; "
        f"this extension supports [23, 24, 25, 26, 27, 28]."
    )
    assert "Refusing to write" in str(err)


def test_write_transaction_refuses_unsupported_version(tmp_path):
    path = make_db(29, tmp_path / "schedulerdb.sqlite")
    from nina_ts_sync.db import write_transaction

    with pytest.raises(SchemaVersionError):
        with write_transaction(path, make_backup=False) as (conn, _):
            conn.execute("INSERT INTO project (profileId, name) VALUES ('x', 'y')")

    # Nothing was written.
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 0
    finally:
        conn.close()
