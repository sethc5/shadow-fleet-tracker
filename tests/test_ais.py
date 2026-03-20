"""Tests for AIS ingestion, dark event detection, and port call detection."""

import tempfile
from pathlib import Path

import pytest

from src.db import Database
from src.ingest.ais import (
    detect_dark_events,
    detect_port_calls,
    _parse_timestamp,
    _gaps_overlap,
)
from src.models import Position, Vessel
from src.scoring import score_vessel_with_positions, RUSSIAN_PORTS


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path=db_path)
    yield db
    db_path.unlink(missing_ok=True)


def test_detect_dark_events_finds_12h_gap():
    positions = [
        {"lat": 59.88, "lon": 29.88, "timestamp": "2026-03-01T00:00:00", "source": "test"},
        {"lat": 59.88, "lon": 29.88, "timestamp": "2026-03-01T01:00:00", "source": "test"},
        # 12-hour gap here
        {"lat": 55.00, "lon": 20.00, "timestamp": "2026-03-01T13:00:00", "source": "test"},
        {"lat": 55.00, "lon": 20.00, "timestamp": "2026-03-01T14:00:00", "source": "test"},
    ]

    gaps = detect_dark_events(positions, gap_hours=6.0)
    assert len(gaps) == 1
    assert gaps[0]["duration_hours"] == 12.0
    assert gaps[0]["near_russia"] is True  # 59.88, 29.88 is Primorsk


def test_detect_dark_events_no_gaps_normal_reporting():
    positions = [
        {"lat": 55.0, "lon": 20.0, "timestamp": f"2026-03-01T{i:02d}:00:00", "source": "test"}
        for i in range(0, 10)
    ]

    gaps = detect_dark_events(positions, gap_hours=6.0)
    assert len(gaps) == 0


def test_detect_dark_events_gap_not_near_russia():
    positions = [
        {"lat": 10.0, "lon": 10.0, "timestamp": "2026-03-01T00:00:00", "source": "test"},
        # 24-hour gap in the middle of the Atlantic
        {"lat": 10.5, "lon": 10.5, "timestamp": "2026-03-02T00:00:00", "source": "test"},
    ]

    gaps = detect_dark_events(positions, gap_hours=6.0)
    assert len(gaps) == 1
    assert gaps[0]["near_russia"] is False


def test_detect_dark_events_empty():
    assert detect_dark_events([]) == []
    assert detect_dark_events([{"lat": 55.0, "lon": 20.0, "timestamp": "2026-03-01T00:00:00"}]) == []


def test_detect_port_calls_primorsk():
    # Position near Primorsk (59.88, 29.88)
    positions = [
        {"lat": 59.90, "lon": 29.90, "timestamp": "2026-03-01T10:00:00", "source": "test"},
        {"lat": 55.00, "lon": 20.00, "timestamp": "2026-03-02T10:00:00", "source": "test"},
    ]

    calls = detect_port_calls(positions, radius_km=30.0)
    assert len(calls) == 1
    assert calls[0]["port_name"] == "Primorsk"
    assert calls[0]["distance_km"] < 5


def test_detect_port_calls_ust_luga():
    # Position near Ust-Luga (59.68, 28.31)
    positions = [
        {"lat": 59.70, "lon": 28.30, "timestamp": "2026-03-01T10:00:00", "source": "test"},
    ]

    calls = detect_port_calls(positions, radius_km=30.0)
    assert len(calls) == 1
    assert calls[0]["port_name"] == "Ust-Luga"


def test_detect_port_calls_no_proximity():
    # Position in the middle of the Atlantic
    positions = [
        {"lat": 30.0, "lon": -40.0, "timestamp": "2026-03-01T10:00:00", "source": "test"},
    ]

    calls = detect_port_calls(positions, radius_km=30.0)
    assert len(calls) == 0


def test_detect_port_calls_deduplicates():
    # Multiple positions near the same port should only count once
    positions = [
        {"lat": 59.90, "lon": 29.90, "timestamp": "2026-03-01T10:00:00", "source": "test"},
        {"lat": 59.89, "lon": 29.89, "timestamp": "2026-03-01T11:00:00", "source": "test"},
        {"lat": 59.88, "lon": 29.88, "timestamp": "2026-03-01T12:00:00", "source": "test"},
    ]

    calls = detect_port_calls(positions, radius_km=30.0)
    assert len(calls) == 1  # Only Primorsk, once


def test_score_with_positions_dark_event_near_russia(db):
    v = Vessel(imo=1234567, name="Dark Vessel", flag="LR")
    db.upsert_vessel(v)

    # Add positions with a gap near Primorsk
    for hour in [0, 1, 2]:
        db.add_position(Position(
            imo=1234567, lat=59.88, lon=29.88,
            timestamp=f"2026-03-01T{hour:02d}:00:00", source="test",
        ))
    # 12-hour gap, then position far away
    db.add_position(Position(
        imo=1234567, lat=50.0, lon=20.0,
        timestamp="2026-03-01T15:00:00", source="test",
    ))

    score, reasons = score_vessel_with_positions(db, 1234567)
    assert any("AIS gap" in r and "Russian" in r for r in reasons)
    assert score > 0


def test_score_with_positions_port_call(db):
    v = Vessel(imo=1234567, name="Port Caller", flag="LR")
    db.upsert_vessel(v)

    # Position near Primorsk
    db.add_position(Position(
        imo=1234567, lat=59.90, lon=29.90,
        timestamp="2026-03-20T10:00:00", source="test",
    ))

    score, reasons = score_vessel_with_positions(db, 1234567)
    assert any("Russian port call" in r for r in reasons)


def test_score_without_positions_uses_basic(db):
    v = Vessel(imo=1234567, name="No Positions", flag="CM", built_year=1995)
    db.upsert_vessel(v)

    score, reasons = score_vessel_with_positions(db, 1234567)
    # Should still get flag + age scores
    assert any("High-risk flag" in r for r in reasons)
    assert any("Vessel age" in r for r in reasons)


def test_parse_timestamp():
    assert _parse_timestamp("2026-03-01T10:00:00") is not None
    assert _parse_timestamp("2026-03-01T10:00:00Z") is not None
    assert _parse_timestamp("2026-03-01 10:00:00") is not None
    assert _parse_timestamp("") is None
    assert _parse_timestamp(None) is None


def test_gaps_overlap():
    gap1 = {"gap_start": "2026-03-01T00:00:00", "gap_end": "2026-03-01T12:00:00"}
    gap2 = {"gap_start": "2026-03-01T06:00:00", "gap_end": "2026-03-01T18:00:00"}
    gap3 = {"gap_start": "2026-03-02T00:00:00", "gap_end": "2026-03-02T12:00:00"}

    assert _gaps_overlap(gap1, gap2) is True
    assert _gaps_overlap(gap1, gap3) is False
    assert _gaps_overlap(gap2, gap3) is False