"""Tests for data models."""

import pytest

from src.models import Vessel, SanctionEntry, SanctionSource, HIGH_RISK_FLAGS


def test_vessel_age():
    v = Vessel(imo=1234567, name="Test", built_year=2000)
    assert v.age is not None
    assert v.age > 20


def test_vessel_age_none():
    v = Vessel(imo=1234567, name="Test")
    assert v.age is None


def test_high_risk_flag():
    v = Vessel(imo=1234567, name="Test", flag="CM")
    assert v.is_high_risk_flag is True


def test_normal_flag():
    v = Vessel(imo=1234567, name="Test", flag="LR")
    assert v.is_high_risk_flag is False


def test_no_flag():
    v = Vessel(imo=1234567, name="Test")
    assert v.is_high_risk_flag is False


def test_vessel_to_dict():
    v = Vessel(imo=1234567, name="Test Ship", flag="PA", risk_score=75)
    d = v.to_dict()
    assert d["imo"] == 1234567
    assert d["name"] == "Test Ship"
    assert d["risk_score"] == 75


def test_sanction_entry():
    s = SanctionEntry(
        source=SanctionSource.OFAC,
        imo=1234567,
        vessel_name="Test",
        designation_date="2024-01-01",
        list_name="OFAC SDN (UKRAINE-EO14024)",
    )
    d = s.to_dict()
    assert d["source"] == "ofac"
    assert d["imo"] == 1234567