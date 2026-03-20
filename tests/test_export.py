"""Tests for CSV export functionality."""

import csv
import tempfile
from pathlib import Path

import pytest

from src.config import reset_config
from src.db import Database
from src.models import Position, SanctionEntry, SanctionSource, Vessel


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path=db_path)
    reset_config()
    yield db
    db_path.unlink(missing_ok=True)


def test_export_csv_columns(db, tmp_path):
    """CSV export has correct column headers."""
    v = Vessel(imo=1234567, name="Test Ship", flag="PA")
    db.upsert_vessel(v)

    output = tmp_path / "test_export.csv"
    _run_export(db, output)

    with open(output, "r") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames

    expected = [
        "imo", "name", "flag", "vessel_type", "built_year", "owner", "dwt",
        "risk_score", "is_sanctioned", "sanctions_sources",
        "last_position_lat", "last_position_lon", "last_seen",
        "near_port", "dark_events_count",
    ]
    assert headers == expected


def test_export_csv_data(db, tmp_path):
    """CSV export contains correct vessel data."""
    v = Vessel(imo=1234567, name="Alpha", flag="LR", vessel_type="Tanker", dwt=50000)
    db.upsert_vessel(v)
    db.add_sanction(SanctionEntry(source=SanctionSource.OFAC, imo=1234567, vessel_name="Alpha"))
    db.add_position(Position(imo=1234567, lat=55.5, lon=20.3, timestamp="2026-03-20T10:00:00", source="test"))

    output = tmp_path / "test_export.csv"
    _run_export(db, output)

    with open(output, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 1
    row = rows[0]
    assert row["imo"] == "1234567"
    assert row["name"] == "Alpha"
    assert row["flag"] == "LR"
    assert row["is_sanctioned"] == "True"
    assert row["last_position_lat"] == "55.5"
    assert row["last_position_lon"] == "20.3"


def test_export_empty_db(db, tmp_path):
    """CSV export works with empty database."""
    output = tmp_path / "test_export.csv"
    _run_export(db, output)

    with open(output, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 0


def _run_export(db, output):
    """Helper to run export without CLI."""
    import csv
    from src.ingest.ais import detect_dark_events, detect_port_calls

    vessels = db.get_all_vessels()
    fieldnames = [
        "imo", "name", "flag", "vessel_type", "built_year", "owner", "dwt",
        "risk_score", "is_sanctioned", "sanctions_sources",
        "last_position_lat", "last_position_lon", "last_seen",
        "near_port", "dark_events_count",
    ]

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for v in vessels:
            sanctions = db.get_sanctions_for_vessel(v.imo)
            is_sanctioned = len(sanctions) > 0
            sources = list(set(s.source.value for s in sanctions))

            positions = db.get_positions(v.imo, limit=500)
            lat, lon, last_seen = "", "", ""
            near_port = ""
            dark_count = 0

            if positions:
                p = positions[0]
                lat, lon = p.lat, p.lon
                last_seen = p.timestamp

                # Convert Position objects to dicts for detection functions
                pos_dicts = [
                    {"lat": pos.lat, "lon": pos.lon, "timestamp": pos.timestamp, "source": pos.source}
                    for pos in positions
                ]
                port_calls = detect_port_calls(pos_dicts)
                if port_calls:
                    near_port = port_calls[0]["port_name"]
                dark_events = detect_dark_events(pos_dicts)
                dark_count = len(dark_events)

            writer.writerow({
                "imo": v.imo, "name": v.name, "flag": v.flag or "",
                "vessel_type": v.vessel_type or "", "built_year": v.built_year or "",
                "owner": v.owner or "", "dwt": v.dwt or "",
                "risk_score": v.risk_score, "is_sanctioned": is_sanctioned,
                "sanctions_sources": "; ".join(sources),
                "last_position_lat": lat, "last_position_lon": lon,
                "last_seen": last_seen, "near_port": near_port,
                "dark_events_count": dark_count,
            })