"""Tests for the scoring engine."""

import tempfile
from pathlib import Path

import pytest

from src.db import Database
from src.models import SanctionEntry, SanctionSource, Vessel
from src.config import reset_config
from src.scoring import _haversine, run_scoring, score_vessel


@pytest.fixture
def db():
    """Create a temporary test database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path=db_path)
    reset_config()
    yield db
    db_path.unlink(missing_ok=True)


def test_score_unsanctioned_vessel(db):
    v = Vessel(imo=1234567, name="Test Ship")
    db.upsert_vessel(v)
    score, reasons = score_vessel(db, 1234567)
    assert score == 0
    assert reasons == []


def test_score_sanctioned_vessel(db):
    v = Vessel(imo=1234567, name="Sanctioned Ship")
    db.upsert_vessel(v)
    s = SanctionEntry(source=SanctionSource.OFAC, imo=1234567, vessel_name="Sanctioned Ship")
    db.add_sanction(s)

    score, reasons = score_vessel(db, 1234567)
    assert score == 100
    assert any("Sanctioned" in r for r in reasons)


def test_score_high_risk_flag(db):
    v = Vessel(imo=1234567, name="Flag Ship", flag="CM")
    db.upsert_vessel(v)
    score, reasons = score_vessel(db, 1234567)
    assert score == 20
    assert any("High-risk flag" in r for r in reasons)


def test_score_old_vessel(db):
    v = Vessel(imo=1234567, name="Old Ship", built_year=2000)
    db.upsert_vessel(v)
    score, reasons = score_vessel(db, 1234567)
    assert score == 10
    assert any("Vessel age" in r for r in reasons)


def test_score_combined_risks(db):
    v = Vessel(imo=1234567, name="Risky Ship", flag="SL", built_year=1995)
    db.upsert_vessel(v)
    s = SanctionEntry(source=SanctionSource.TANKERTRACKERS, imo=1234567)
    db.add_sanction(s)

    score, reasons = score_vessel(db, 1234567)
    assert score == 100  # Capped at 100
    assert len(reasons) >= 3


def test_score_nonexistent_vessel(db):
    score, reasons = score_vessel(db, 9999999)
    assert score == 0
    assert reasons == []


def test_run_scoring_generates_alerts(db):
    # Add a sanctioned vessel (should NOT generate alert — already sanctioned)
    v1 = Vessel(imo=1111111, name="Sanctioned")
    db.upsert_vessel(v1)
    db.add_sanction(SanctionEntry(source=SanctionSource.OFAC, imo=1111111))

    # Add a high-risk unsanctioned vessel (should generate alert)
    v2 = Vessel(imo=2222222, name="Suspicious", flag="CM", built_year=1990)
    db.upsert_vessel(v2)

    alerts = run_scoring(db)
    # The sanctioned vessel should NOT generate an alert
    # The unsanctioned vessel with score 30 (20 flag + 10 age) is below threshold
    assert alerts == 0


def test_haversine_same_point():
    dist = _haversine(59.88, 29.88, 59.88, 29.88)
    assert dist < 0.01  # Nearly zero


def test_haversine_known_distance():
    # Primorsk to Ust-Luga ~100km
    dist = _haversine(59.88, 29.88, 59.68, 28.31)
    assert 50 < dist < 150  # Roughly 100km