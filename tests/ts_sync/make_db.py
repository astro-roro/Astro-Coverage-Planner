"""Build an empty Target Scheduler database at a requested schema version.

`tests/fixtures/` carries three full schemas: 28, dumped from a real install,
plus 23 and 25 reconstructed by replaying Target Scheduler's own migration
scripts. The versions in between differ from the nearest lower fixture only by
a handful of additive `ALTER TABLE ... ADD COLUMN` statements, so this module
keeps those deltas inline rather than carrying three more near-identical
schema dumps.

Every statement below is copied verbatim from the matching
`NINA.Plugin.TargetScheduler/Database/Migrate/N.sql`; see
`docs/schema-history.md` for the citations.

Usage::

    from tests.ts_sync.make_db import make_db
    path = make_db(24, tmp_path / "schedulerdb.sqlite")
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Versions we have a complete schema file for.
BASE_VERSIONS: tuple[int, ...] = (23, 25, 28)

#: version -> the ALTER statements that `Migrate/<version>.sql` applies.
#: Only the versions we have to synthesise are listed.
MIGRATION_STEPS: dict[int, tuple[str, ...]] = {
    24: ("ALTER TABLE target ADD COLUMN priority INTEGER DEFAULT -1;",),
    26: ("ALTER TABLE profilepreference ADD COLUMN enablePlannerReports INTEGER DEFAULT 0;",),
    27: ("ALTER TABLE profilepreference ADD COLUMN enableClientUpdatesExposurePlan INTEGER DEFAULT 1;",),
}

#: The full range this test suite can build.
BUILDABLE_VERSIONS: tuple[int, ...] = (23, 24, 25, 26, 27, 28)


def schema_sql(version: int) -> str:
    """The complete `CREATE TABLE` script for the nearest base at or below."""
    base = max(v for v in BASE_VERSIONS if v <= version)
    return (FIXTURES / f"ts-schema-{base}.sql").read_text(encoding="utf-8")


def make_db(version: int, path: Path) -> Path:
    """Create an empty TS database at *version* on disk at *path*.

    Raises ValueError for a version this suite has no recipe for. Note that
    22 and 29 are deliberately buildable so the refusal path can be tested:
    22 is the 23 schema minus nothing but with the version pragma wound back,
    and 29 is the 28 schema wound forward. Neither shape is claimed to be the
    real schema at that version; they exist only to carry the pragma.
    """
    if version in BUILDABLE_VERSIONS:
        base = max(v for v in BASE_VERSIONS if v <= version)
        steps = [s for v in range(base + 1, version + 1) for s in MIGRATION_STEPS.get(v, ())]
    elif version == 22:
        base, steps = 23, []
    elif version == 29:
        base, steps = 28, []
    else:
        raise ValueError(f"no recipe for schema version {version}")

    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript((FIXTURES / f"ts-schema-{base}.sql").read_text(encoding="utf-8"))
        for stmt in steps:
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()
    return path


def table_columns(path: Path, table: str) -> list[str]:
    """Column names of *table*, in declaration order."""
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()
