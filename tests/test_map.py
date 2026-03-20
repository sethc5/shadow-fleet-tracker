"""Tests for interactive map generation."""

import tempfile
from pathlib import Path

import pytest

from src.config import reset_config
from src.db import Database
from src.models import Position, Vessel
from src.viz.map import build_map, _score_color


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path=db_path)
    reset_config()
    yield db
    db_path.unlink(missing_ok=True)


def test_score_color():
    assert _score_color(90) == "red"
    assert _score_color(70) == "orange"
    assert _score_color(50) == "beige"
    assert _score_color(30) == "green"
    assert _score_color(5) == "blue"


def test_build_map_empty_db(db, tmp_path):
    """Map generates with empty database."""
    output = tmp_path / "test_map.html"
    path = build_map(db, output=output)
    assert path.exists()
    content = path.read_text()
    assert "OpenStreetMap" in content


def test_build_map_with_vessel(db, tmp_path):
    """Map includes vessel markers."""
    v = Vessel(imo=1234567, name="Test Ship", flag="PA", risk_score=75)
    db.upsert_vessel(v)
    db.add_position(Position(imo=1234567, lat=59.88, lon=29.88, timestamp="2026-03-20T10:00:00", source="test"))

    output = tmp_path / "test_map.html"
    path = build_map(db, output=output)
    content = path.read_text()
    assert "Test Ship" in content
    assert "59.88" in content or "59.87" in content


def test_build_map_single_vessel(db, tmp_path):
    """Map with --imo filter shows only that vessel."""
    v1 = Vessel(imo=1111111, name="Ship A")
    v2 = Vessel(imo=2222222, name="Ship B")
    db.upsert_vessel(v1)
    db.upsert_vessel(v2)
    db.add_position(Position(imo=1111111, lat=55.0, lon=20.0, timestamp="2026-03-20T10:00:00", source="test"))
    db.add_position(Position(imo=2222222, lat=56.0, lon=21.0, timestamp="2026-03-20T10:00:00", source="test"))

    output = tmp_path / "test_map.html"
    path = build_map(db, output=output, imo=1111111)
    content = path.read_text()
    assert "Ship A" in content
    # Ship B should not be in the map (different vessel)
    assert "Ship B" not in content


def test_build_map_russian_ports(db, tmp_path):
    """Map includes Russian port zones."""
    output = tmp_path / "test_map.html"
    path = build_map(db, output=output)
    content = path.read_text()
    assert "Primorsk" in content
    assert "Ust-Luga" in content


def test_build_map_no_ports_option(db, tmp_path):
    """Map with --no-ports hides port zones."""
    output = tmp_path / "test_map.html"
    path = build_map(db, output=output, show_ports=False)
    content = path.read_text()
    assert "Primorsk" not in content


def test_build_map_with_track(db, tmp_path):
    """Map shows vessel track lines."""
    v = Vessel(imo=1234567, name="Track Ship")
    db.upsert_vessel(v)
    db.add_position(Position(imo=1234567, lat=55.0, lon=20.0, timestamp="2026-03-20T10:00:00", source="test"))
    db.add_position(Position(imo=1234567, lat=55.5, lon=20.5, timestamp="2026-03-20T11:00:00", source="test"))
    db.add_position(Position(imo=1234567, lat=56.0, lon=21.0, timestamp="2026-03-20T12:00:00", source="test"))

    output = tmp_path / "test_map.html"
    path = build_map(db, output=output)
    content = path.read_text()
    # PolyLine creates lat/lon arrays
    assert "55.0" in content
    assert "56.0" in content