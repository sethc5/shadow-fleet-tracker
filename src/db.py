"""SQLite database connection, schema, and CRUD operations."""

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from .constants import SCHEMA_VERSION
from .models import Alert, Position, SanctionEntry, SanctionSource, Vessel

DEFAULT_DB_PATH = Path("data") / "vessels.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vessels (
    imo INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    mmsi INTEGER,
    flag TEXT,
    vessel_type TEXT,
    built_year INTEGER,
    owner TEXT,
    dwt INTEGER,
    risk_score INTEGER DEFAULT 0,
    last_updated TEXT
);

CREATE TABLE IF NOT EXISTS sanctions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    imo INTEGER,
    vessel_name TEXT,
    designation_date TEXT,
    list_name TEXT,
    raw_data TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (imo) REFERENCES vessels(imo)
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imo INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    timestamp TEXT NOT NULL,
    speed REAL,
    course REAL,
    source TEXT,
    FOREIGN KEY (imo) REFERENCES vessels(imo),
    UNIQUE(imo, timestamp, lat, lon)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imo INTEGER NOT NULL,
    score INTEGER NOT NULL,
    reasons TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (imo) REFERENCES vessels(imo)
);

CREATE INDEX IF NOT EXISTS idx_sanctions_imo ON sanctions(imo);
CREATE INDEX IF NOT EXISTS idx_sanctions_source ON sanctions(source);
CREATE INDEX IF NOT EXISTS idx_positions_imo ON positions(imo);
CREATE INDEX IF NOT EXISTS idx_positions_ts ON positions(timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_imo ON alerts(imo);
CREATE INDEX IF NOT EXISTS idx_alerts_score ON alerts(score);

CREATE TABLE IF NOT EXISTS vessel_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imo INTEGER NOT NULL,
    change_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    detected_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (imo) REFERENCES vessels(imo)
);

CREATE INDEX IF NOT EXISTS idx_changes_imo ON vessel_changes(imo);
CREATE INDEX IF NOT EXISTS idx_changes_type ON vessel_changes(change_type);
"""


class Database:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            env_url = os.environ.get("DATABASE_URL", "")
            if env_url.startswith("sqlite:///"):
                db_path = Path(env_url.replace("sqlite:///", ""))
            else:
                db_path = DEFAULT_DB_PATH

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        """Initialize database schema with versioning and migrations."""
        with self.connection() as conn:
            # Create schema_version table if it doesn't exist
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            
            # Get current version
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            current_version = row[0] if row else 0
            
            # Run base schema if new database
            if current_version == 0:
                conn.executescript(SCHEMA_SQL)
                conn.execute(
                    "INSERT INTO schema_version (id, version) VALUES (1, ?)",
                    (SCHEMA_VERSION,)
                )
            elif current_version < SCHEMA_VERSION:
                # Run migrations
                self._migrate_schema(conn, current_version, SCHEMA_VERSION)
                conn.execute(
                    "UPDATE schema_version SET version = ?, updated_at = datetime('now') WHERE id = 1",
                    (SCHEMA_VERSION,)
                )

    def _migrate_schema(self, conn, from_version: int, to_version: int):
        """Run schema migrations from one version to another.
        
        Add new migration functions as schema evolves:
        - Migration 1->2: Add new columns
        - Migration 2->3: Add new tables
        etc.
        """
        logger = logging.getLogger(__name__)
        logger.info("Migrating schema from v%d to v%d", from_version, to_version)
        
        # Example migration pattern:
        # if from_version < 2:
        #     conn.execute("ALTER TABLE vessels ADD COLUMN new_column TEXT")
        #     from_version = 2
        
        logger.info("Schema migration complete")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- Vessel CRUD ---

    def upsert_vessel(self, vessel: Vessel) -> Vessel:
        """Upsert a vessel, tracking flag and owner changes."""
        with self.connection() as conn:
            # Get existing vessel to detect changes
            existing = None
            row = conn.execute("SELECT * FROM vessels WHERE imo = ?", (vessel.imo,)).fetchone()
            if row:
                existing = self._row_to_vessel(row)

            conn.execute(
                """
                INSERT INTO vessels (imo, name, mmsi, flag, vessel_type, built_year, owner, dwt, risk_score, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(imo) DO UPDATE SET
                    name = COALESCE(excluded.name, vessels.name),
                    mmsi = COALESCE(excluded.mmsi, vessels.mmsi),
                    flag = COALESCE(excluded.flag, vessels.flag),
                    vessel_type = COALESCE(excluded.vessel_type, vessels.vessel_type),
                    built_year = COALESCE(excluded.built_year, vessels.built_year),
                    owner = COALESCE(excluded.owner, vessels.owner),
                    dwt = COALESCE(excluded.dwt, vessels.dwt),
                    risk_score = excluded.risk_score,
                    last_updated = datetime('now')
                """,
                (
                    vessel.imo, vessel.name, vessel.mmsi, vessel.flag,
                    vessel.vessel_type, vessel.built_year, vessel.owner,
                    vessel.dwt, vessel.risk_score,
                ),
            )

            # Detect and record changes
            if existing:
                if vessel.flag and existing.flag and vessel.flag != existing.flag:
                    conn.execute(
                        "INSERT INTO vessel_changes (imo, change_type, old_value, new_value) VALUES (?, 'flag', ?, ?)",
                        (vessel.imo, existing.flag, vessel.flag),
                    )
                if vessel.owner and existing.owner and vessel.owner != existing.owner:
                    conn.execute(
                        "INSERT INTO vessel_changes (imo, change_type, old_value, new_value) VALUES (?, 'owner', ?, ?)",
                        (vessel.imo, existing.owner, vessel.owner),
                    )

        return vessel

    def get_vessel(self, imo: int) -> Optional[Vessel]:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM vessels WHERE imo = ?", (imo,)).fetchone()
        if row is None:
            return None
        return self._row_to_vessel(row)

    def get_all_vessels(self) -> list[Vessel]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM vessels ORDER BY risk_score DESC").fetchall()
        return [self._row_to_vessel(r) for r in rows]

    def get_sanctioned_vessels(self) -> list[Vessel]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT v.* FROM vessels v
                JOIN sanctions s ON v.imo = s.imo
                ORDER BY v.risk_score DESC
                """
            ).fetchall()
        return [self._row_to_vessel(r) for r in rows]

    def update_risk_score(self, imo: int, score: int):
        with self.connection() as conn:
            conn.execute(
                "UPDATE vessels SET risk_score = ?, last_updated = datetime('now') WHERE imo = ?",
                (score, imo),
            )

    def vessel_count(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM vessels").fetchone()
        return row[0]

    # --- Sanctions CRUD ---

    def add_sanction(self, entry: SanctionEntry) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO sanctions (source, imo, vessel_name, designation_date, list_name, raw_data)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.source.value, entry.imo, entry.vessel_name,
                    entry.designation_date, entry.list_name, entry.raw_data,
                ),
            )
        return cursor.lastrowid

    def get_sanctions_for_vessel(self, imo: int) -> list[SanctionEntry]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM sanctions WHERE imo = ? ORDER BY designation_date DESC",
                (imo,),
            ).fetchall()
        return [self._row_to_sanction(r) for r in rows]

    def sanctions_by_source(self, source: SanctionSource) -> list[SanctionEntry]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM sanctions WHERE source = ? ORDER BY designation_date DESC",
                (source.value,),
            ).fetchall()
        return [self._row_to_sanction(r) for r in rows]

    def sanctions_count(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM sanctions").fetchone()
        return row[0]

    # --- Positions CRUD ---

    def add_position(self, pos: Position) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO positions (imo, lat, lon, timestamp, speed, course, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (pos.imo, pos.lat, pos.lon, pos.timestamp, pos.speed, pos.course, pos.source),
            )
        return cursor.lastrowid

    def add_positions_batch(self, positions: list[Position]) -> int:
        """Batch insert positions. Returns count of new positions inserted."""
        if not positions:
            return 0
        with self.connection() as conn:
            cursor = conn.executemany(
                """
                INSERT OR IGNORE INTO positions (imo, lat, lon, timestamp, speed, course, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [(p.imo, p.lat, p.lon, p.timestamp, p.speed, p.course, p.source) for p in positions],
            )
        return cursor.rowcount

    def get_recent_changes(self, imo: int, change_type: str, days: int = 90) -> list[dict]:
        """Get recent changes of a specific type for a vessel."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM vessel_changes WHERE imo = ? AND change_type = ? AND detected_at >= datetime('now', ?) ORDER BY detected_at DESC",
                (imo, change_type, f"-{days} days"),
            ).fetchall()
        return [{"old": r["old_value"], "new": r["new_value"], "detected": r["detected_at"]} for r in rows]

    def cleanup_old_positions(self, days: int = 90) -> int:
        """Delete positions older than N days. Returns count deleted."""
        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM positions WHERE timestamp < datetime('now', ?)",
                (f"-{days} days",),
            )
        return cursor.rowcount

    def get_positions(self, imo: int, limit: int = 100) -> list[Position]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE imo = ? ORDER BY timestamp DESC LIMIT ?",
                (imo, limit),
            ).fetchall()
        return [self._row_to_position(r) for r in rows]

    # --- Alerts CRUD ---

    def add_alert(self, alert: Alert) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO alerts (imo, score, reasons, created_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (alert.imo, alert.score, alert.reasons_text()),
            )
        return cursor.lastrowid

    def get_alerts_for_vessel(self, imo: int, limit: int = 10) -> list[Alert]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE imo = ? ORDER BY id DESC LIMIT ?",
                (imo, limit),
            ).fetchall()
        return [self._row_to_alert(r) for r in rows]

    def get_alerts(self, min_score: int = 0, limit: int = 100) -> list[Alert]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE score >= ? ORDER BY created_at DESC LIMIT ?",
                (min_score, limit),
            ).fetchall()
        return [self._row_to_alert(r) for r in rows]

    def get_latest_alert(self, imo: int) -> Optional[Alert]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM alerts WHERE imo = ? ORDER BY id DESC LIMIT 1",
                (imo,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_alert(row)

    # --- Row converters ---

    @staticmethod
    def _row_to_vessel(row: sqlite3.Row) -> Vessel:
        return Vessel(
            imo=row["imo"],
            name=row["name"],
            mmsi=row["mmsi"],
            flag=row["flag"],
            vessel_type=row["vessel_type"],
            built_year=row["built_year"],
            owner=row["owner"],
            dwt=row["dwt"],
            risk_score=row["risk_score"],
            last_updated=row["last_updated"],
        )

    @staticmethod
    def _row_to_sanction(row: sqlite3.Row) -> SanctionEntry:
        return SanctionEntry(
            id=row["id"],
            source=SanctionSource(row["source"]),
            imo=row["imo"],
            vessel_name=row["vessel_name"],
            designation_date=row["designation_date"],
            list_name=row["list_name"],
            raw_data=row["raw_data"],
        )

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> Position:
        return Position(
            id=row["id"],
            imo=row["imo"],
            lat=row["lat"],
            lon=row["lon"],
            timestamp=row["timestamp"],
            speed=row["speed"],
            course=row["course"],
            source=row["source"],
        )

    @staticmethod
    def _row_to_alert(row: sqlite3.Row) -> Alert:
        reasons_str = row["reasons"] or ""
        return Alert(
            id=row["id"],
            imo=row["imo"],
            score=row["score"],
            reasons=[r.strip() for r in reasons_str.split(";") if r.strip()],
            created_at=row["created_at"],
        )