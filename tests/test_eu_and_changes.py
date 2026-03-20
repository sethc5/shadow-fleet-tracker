"""Tests for EU sanctions parser and vessel change detection."""

import tempfile
from pathlib import Path

import pytest

from src.config import reset_config
from src.db import Database
from src.models import SanctionEntry, SanctionSource, Vessel
from src.scoring import score_vessel


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path=db_path)
    reset_config()
    yield db
    db_path.unlink(missing_ok=True)


def test_vessel_change_detection_flag(db):
    """Flag change is recorded when vessel flag changes on upsert."""
    v1 = Vessel(imo=1234567, name="Test", flag="PA")
    db.upsert_vessel(v1)

    v2 = Vessel(imo=1234567, name="Test", flag="LR")
    db.upsert_vessel(v2)

    changes = db.get_recent_changes(1234567, "flag", days=90)
    assert len(changes) == 1
    assert changes[0]["old"] == "PA"
    assert changes[0]["new"] == "LR"


def test_vessel_change_detection_owner(db):
    """Owner change is recorded when vessel owner changes on upsert."""
    v1 = Vessel(imo=1234567, name="Test", owner="Old Corp")
    db.upsert_vessel(v1)

    v2 = Vessel(imo=1234567, name="Test", owner="New Corp")
    db.upsert_vessel(v2)

    changes = db.get_recent_changes(1234567, "owner", days=90)
    assert len(changes) == 1
    assert changes[0]["old"] == "Old Corp"
    assert changes[0]["new"] == "New Corp"


def test_no_change_recorded_when_same(db):
    """No change recorded when values don't change."""
    v1 = Vessel(imo=1234567, name="Test", flag="PA")
    db.upsert_vessel(v1)

    v2 = Vessel(imo=1234567, name="Test", flag="PA")
    db.upsert_vessel(v2)

    changes = db.get_recent_changes(1234567, "flag", days=90)
    assert len(changes) == 0


def test_no_change_on_new_vessel(db):
    """No change recorded for first-time vessel insert."""
    v = Vessel(imo=1234567, name="Test", flag="PA")
    db.upsert_vessel(v)

    changes = db.get_recent_changes(1234567, "flag", days=90)
    assert len(changes) == 0


def test_score_with_flag_change(db):
    """Flag change adds points to risk score."""
    v1 = Vessel(imo=1234567, name="Test", flag="PA")
    db.upsert_vessel(v1)

    v2 = Vessel(imo=1234567, name="Test", flag="CM")
    db.upsert_vessel(v2)

    score, reasons = score_vessel(db, 1234567)
    assert any("Flag change" in r for r in reasons)
    assert score >= 20  # flag_change_90d weight


def test_score_with_owner_change(db):
    """Owner change adds points to risk score."""
    v1 = Vessel(imo=1234567, name="Test", owner="Old Corp")
    db.upsert_vessel(v1)

    v2 = Vessel(imo=1234567, name="Test", owner="New Corp")
    db.upsert_vessel(v2)

    score, reasons = score_vessel(db, 1234567)
    assert any("Ownership change" in r for r in reasons)


def test_cleanup_old_positions(db):
    """Cleanup deletes old positions."""
    from src.models import Position
    from datetime import datetime, timedelta

    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)

    # Add old position (100 days ago)
    old_time = (datetime.now() - timedelta(days=100)).isoformat()
    db.add_position(Position(imo=1234567, lat=55.0, lon=20.0, timestamp=old_time, source="test"))

    # Add recent position
    db.add_position(Position(imo=1234567, lat=55.1, lon=20.1, timestamp=datetime.now().isoformat(), source="test"))

    assert len(db.get_positions(1234567, limit=100)) == 2

    deleted = db.cleanup_old_positions(days=90)
    assert deleted == 1
    assert len(db.get_positions(1234567, limit=100)) == 1


def test_get_alerts_for_vessel(db):
    """get_alerts_for_vessel returns only alerts for that vessel."""
    from src.models import Alert

    v1 = Vessel(imo=1111111, name="A")
    v2 = Vessel(imo=2222222, name="B")
    db.upsert_vessel(v1)
    db.upsert_vessel(v2)

    db.add_alert(Alert(imo=1111111, score=70, reasons=["Test A"]))
    db.add_alert(Alert(imo=2222222, score=80, reasons=["Test B"]))
    db.add_alert(Alert(imo=1111111, score=90, reasons=["Test A2"]))

    alerts_a = db.get_alerts_for_vessel(1111111)
    assert len(alerts_a) == 2
    assert all(a.imo == 1111111 for a in alerts_a)

    alerts_b = db.get_alerts_for_vessel(2222222)
    assert len(alerts_b) == 1
    assert alerts_b[0].score == 80


def test_batch_positions_insert(db):
    """Batch insert positions works and deduplicates."""
    from src.models import Position

    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)

    positions = [
        Position(imo=1234567, lat=55.0, lon=20.0, timestamp="2026-03-20T10:00:00", source="test"),
        Position(imo=1234567, lat=55.1, lon=20.1, timestamp="2026-03-20T11:00:00", source="test"),
    ]

    count = db.add_positions_batch(positions)
    assert count == 2

    # Insert same positions again — should deduplicate
    count2 = db.add_positions_batch(positions)
    assert count2 == 0

    stored = db.get_positions(1234567, limit=100)
    assert len(stored) == 2