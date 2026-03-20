"""Tests for database operations."""

import tempfile
from pathlib import Path

import pytest

from src.db import Database
from src.models import Alert, Position, SanctionEntry, SanctionSource, Vessel


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path=db_path)
    yield db
    db_path.unlink(missing_ok=True)


def test_upsert_vessel(db):
    v = Vessel(imo=1234567, name="Test Ship", flag="PA")
    db.upsert_vessel(v)
    result = db.get_vessel(1234567)
    assert result is not None
    assert result.name == "Test Ship"
    assert result.flag == "PA"


def test_upsert_updates_existing(db):
    v1 = Vessel(imo=1234567, name="Old Name", flag="PA")
    db.upsert_vessel(v1)

    v2 = Vessel(imo=1234567, name="New Name", flag="LR")
    db.upsert_vessel(v2)

    result = db.get_vessel(1234567)
    assert result.name == "New Name"
    assert result.flag == "LR"


def test_upsert_preserves_existing_on_null(db):
    v1 = Vessel(imo=1234567, name="Keep This", flag="PA", dwt=50000)
    db.upsert_vessel(v1)

    v2 = Vessel(imo=1234567, name="Keep This", flag=None, dwt=None)
    db.upsert_vessel(v2)

    result = db.get_vessel(1234567)
    assert result.flag == "PA"
    assert result.dwt == 50000


def test_get_vessel_not_found(db):
    result = db.get_vessel(9999999)
    assert result is None


def test_add_sanction(db):
    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)

    s = SanctionEntry(
        source=SanctionSource.OFAC,
        imo=1234567,
        vessel_name="Test",
        designation_date="2024-01-01",
        list_name="OFAC SDN",
    )
    sid = db.add_sanction(s)
    assert sid > 0


def test_get_sanctions_for_vessel(db):
    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)

    s1 = SanctionEntry(source=SanctionSource.OFAC, imo=1234567, vessel_name="Test")
    s2 = SanctionEntry(source=SanctionSource.TANKERTRACKERS, imo=1234567, vessel_name="Test")
    db.add_sanction(s1)
    db.add_sanction(s2)

    results = db.get_sanctions_for_vessel(1234567)
    assert len(results) == 2


def test_get_sanctioned_vessels(db):
    v1 = Vessel(imo=1111111, name="Sanctioned")
    v2 = Vessel(imo=2222222, name="Not Sanctioned")
    db.upsert_vessel(v1)
    db.upsert_vessel(v2)

    db.add_sanction(SanctionEntry(source=SanctionSource.OFAC, imo=1111111))

    results = db.get_sanctioned_vessels()
    assert len(results) == 1
    assert results[0].imo == 1111111


def test_add_position(db):
    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)

    p = Position(imo=1234567, lat=59.88, lon=29.88, timestamp="2024-01-01T00:00:00", source="test")
    pid = db.add_position(p)
    assert pid > 0

    positions = db.get_positions(1234567)
    assert len(positions) == 1
    assert positions[0].lat == 59.88


def test_update_risk_score(db):
    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)

    db.update_risk_score(1234567, 75)
    result = db.get_vessel(1234567)
    assert result.risk_score == 75


def test_add_alert(db):
    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)

    a = Alert(imo=1234567, score=80, reasons=["High risk flag", "Old vessel"])
    aid = db.add_alert(a)
    assert aid > 0

    alerts = db.get_alerts(min_score=60)
    assert len(alerts) == 1
    assert alerts[0].score == 80


def test_vessel_count(db):
    assert db.vessel_count() == 0
    db.upsert_vessel(Vessel(imo=1111111, name="A"))
    db.upsert_vessel(Vessel(imo=2222222, name="B"))
    assert db.vessel_count() == 2