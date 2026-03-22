"""Tests for the FastAPI endpoints and auth middleware."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture
def client(db):
    """Create test client with patched DB."""
    from src.api.main import app
    # Patch the global db in main module
    import src.api.main as main_module
    original_db = main_module.db
    main_module.db = db
    with TestClient(app) as c:
        yield c
    main_module.db = original_db


def test_health_endpoint(client):
    """Test health endpoint returns database and vessel info.
    
    Note: External API checks may return 'degraded' status if services
    are unreachable during testing, which is expected behavior.
    """
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    # Status can be 'healthy' or 'degraded' depending on external API availability
    assert data["status"] in ("healthy", "degraded")
    assert data["database"] == "healthy"
    assert "vessels" in data
    assert "sanctions" in data
    assert "external_apis" in data


def test_vessel_endpoint_not_found(client):
    resp = client.get("/vessel/9999999")
    assert resp.status_code == 404


def test_vessel_endpoint_with_data(client, db):
    v = Vessel(imo=1234567, name="Test Ship", flag="PA")
    db.upsert_vessel(v)
    db.add_sanction(SanctionEntry(source=SanctionSource.OFAC, imo=1234567, vessel_name="Test Ship"))

    resp = client.get("/vessel/1234567")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Test Ship"
    assert data["is_sanctioned"] is True
    assert len(data["sanctions"]) == 1


def test_vessel_positions_endpoint(client, db):
    v = Vessel(imo=1234567, name="Test Ship")
    db.upsert_vessel(v)
    db.add_position(Position(imo=1234567, lat=59.88, lon=29.88, timestamp="2026-03-20T10:00:00", source="test"))

    resp = client.get("/vessel/1234567/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["imo"] == 1234567
    assert len(data["positions"]) == 1
    assert data["positions"][0]["lat"] == 59.88


def test_sanctioned_endpoint(client, db):
    v1 = Vessel(imo=1111111, name="Sanctioned")
    v2 = Vessel(imo=2222222, name="Clean")
    db.upsert_vessel(v1)
    db.upsert_vessel(v2)
    db.add_sanction(SanctionEntry(source=SanctionSource.OFAC, imo=1111111, vessel_name="Sanctioned"))

    resp = client.get("/sanctioned")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["imo"] == 1111111


def test_alerts_endpoint(client, db):
    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)
    from src.models import Alert
    db.add_alert(Alert(imo=1234567, score=75, reasons=["Test alert"]))

    resp = client.get("/alerts/today?min_score=60")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["score"] == 75


def test_fleet_summary_endpoint(client, db):
    v = Vessel(imo=1234567, name="Test")
    db.upsert_vessel(v)

    resp = client.get("/fleet/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_vessels"] == 1


def test_export_csv_endpoint(client, db):
    v = Vessel(imo=1234567, name="Test Ship", flag="PA")
    db.upsert_vessel(v)

    resp = client.get("/export/csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "1234567" in resp.text


def test_auth_required_when_configured(client, db):
    """When SFT_API_KEY is set, requests need X-API-Key header."""
    with patch.dict("os.environ", {"SFT_API_KEY": "test-secret-key"}):
        # Clear config cache to pick up env var
        reset_config()

        resp = client.get("/vessel/1234567")
        assert resp.status_code == 401

        resp = client.get("/vessel/1234567", headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 404  # 404 because vessel doesn't exist, but auth passed


def test_auth_bypass_when_not_configured(client):
    """When no API key is configured, requests pass through."""
    resp = client.get("/health")
    assert resp.status_code == 200


def test_auth_skips_health(client):
    """Health endpoint is always accessible regardless of auth."""
    with patch.dict("os.environ", {"SFT_API_KEY": "test-secret-key"}):
        reset_config()
        resp = client.get("/health")
        assert resp.status_code == 200