"""Tests for OFAC SDN parser using local fixture."""

import tempfile
from pathlib import Path

import pytest

from src.db import Database
from src.ingest.ofac import ingest_ofac, parse_sdn_vessels

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_sdn.xml"


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path=db_path)
    yield db
    db_path.unlink(missing_ok=True)


def test_parse_sdn_vessels():
    vessels = parse_sdn_vessels(FIXTURE_PATH)
    assert len(vessels) == 2  # Only vessels, not individuals

    imos = {v["imo"] for v in vessels}
    assert 9876543 in imos
    assert 9876544 in imos


def test_parse_vessel_alpha():
    vessels = parse_sdn_vessels(FIXTURE_PATH)
    alpha = next(v for v in vessels if v["imo"] == 9876543)
    assert alpha["name"] == "TEST VESSEL ALPHA"
    assert "UKRAINE-EO14024" in alpha["programs"]
    assert alpha["vessel_info"]["flag"] == "Panama"
    assert alpha["vessel_info"]["vessel_type"] == "Oil Tanker"
    assert alpha["vessel_info"]["dwt"] == 150000


def test_parse_vessel_beta():
    vessels = parse_sdn_vessels(FIXTURE_PATH)
    beta = next(v for v in vessels if v["imo"] == 9876544)
    assert beta["name"] == "TEST VESSEL BETA"
    assert "RUSSIA-EO14024" in beta["programs"]
    assert "UKRAINE-EO13662" in beta["programs"]
    assert beta["vessel_info"]["flag"] == "Liberia"


def test_ingest_ofac(db):
    count = ingest_ofac(db, xml_path=FIXTURE_PATH)
    assert count == 2
    assert db.vessel_count() == 2
    assert db.sanctions_count() == 3  # Alpha has 1 program, Beta has 2


def test_ingested_vessel_data(db):
    ingest_ofac(db, xml_path=FIXTURE_PATH)

    alpha = db.get_vessel(9876543)
    assert alpha is not None
    assert alpha.name == "TEST VESSEL ALPHA"
    assert alpha.flag == "Panama"
    assert alpha.vessel_type == "Oil Tanker"
    assert alpha.dwt == 150000


def test_ingested_sanctions(db):
    ingest_ofac(db, xml_path=FIXTURE_PATH)

    alpha_sanctions = db.get_sanctions_for_vessel(9876543)
    assert len(alpha_sanctions) == 1
    assert alpha_sanctions[0].source.value == "ofac"

    beta_sanctions = db.get_sanctions_for_vessel(9876544)
    assert len(beta_sanctions) == 2