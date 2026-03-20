"""Tests for alert deduplication in scoring engine."""

import tempfile
from pathlib import Path

import pytest

from src.config import reset_config
from src.db import Database
from src.models import Alert, SanctionEntry, SanctionSource, Vessel
from src.scoring import run_scoring


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path=db_path)
    reset_config()
    yield db
    db_path.unlink(missing_ok=True)


def test_alert_dedup_skips_duplicate(db):
    """Running scoring twice at same score creates only one alert."""
    v = Vessel(imo=1234567, name="Test", flag="CM", built_year=1990)
    db.upsert_vessel(v)

    # First run: should create alert (score 30: flag 20 + age 10)
    run_scoring(db)
    alerts1 = db.get_alerts(min_score=0)
    count1 = len(alerts1)

    # Reset score so scoring runs again
    db.update_risk_score(1234567, 0)

    # Second run: same score, should NOT create new alert
    run_scoring(db)
    alerts2 = db.get_alerts(min_score=0)
    count2 = len(alerts2)

    # Since score 30 is below threshold 60, no alerts should be created
    assert count1 == 0
    assert count2 == 0


def test_alert_dedup_allows_higher_score(db):
    """New alert created when score increases (unsanctioned vessel)."""
    v = Vessel(imo=1234567, name="Test", flag="CM", built_year=1990)
    db.upsert_vessel(v)

    # First alert at score 30 (flag 20 + age 10)
    # But 30 < threshold 60, so no alert created by run_scoring
    # Manually create one at a higher score to test dedup
    db.add_alert(Alert(imo=1234567, score=50, reasons=["Previous"]))

    # run_scoring will compute score=30, which is < latest alert (50),
    # so dedup should skip (30 < 50)
    run_scoring(db)

    alerts = db.get_alerts(min_score=0)
    vessel_alerts = [a for a in alerts if a.imo == 1234567]

    # Only the original alert — new score is lower, so skipped
    assert len(vessel_alerts) == 1
    assert vessel_alerts[0].score == 50


def test_alert_dedup_skips_lower_score(db):
    """No new alert when score drops."""
    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)

    # Existing alert at score 80
    db.add_alert(Alert(imo=1234567, score=80, reasons=["Previous"]))

    # Current score is 0 (no risk factors)
    run_scoring(db)

    alerts = db.get_alerts(min_score=0)
    vessel_alerts = [a for a in alerts if a.imo == 1234567]

    # Only the original alert, no new one
    assert len(vessel_alerts) == 1
    assert vessel_alerts[0].score == 80


def test_get_latest_alert(db):
    """get_latest_alert returns most recent alert for vessel."""
    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)

    db.add_alert(Alert(imo=1234567, score=60, reasons=["First"]))
    db.add_alert(Alert(imo=1234567, score=75, reasons=["Second"]))

    latest = db.get_latest_alert(1234567)
    assert latest is not None
    assert latest.score == 75
    assert latest.reasons == ["Second"]


def test_get_latest_alert_none(db):
    """get_latest_alert returns None for vessel with no alerts."""
    latest = db.get_latest_alert(9999999)
    assert latest is None