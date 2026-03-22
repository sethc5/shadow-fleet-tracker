"""Core data models for Shadow Fleet Tracker."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from .constants import HIGH_RISK_FLAGS


class SanctionSource(str, Enum):
    OFAC = "ofac"
    EU = "eu"
    UK = "uk"
    OPENSANCTIONS = "opensanctions"
    TANKERTRACKERS = "tankertrackers"


@dataclass
class Vessel:
    imo: int
    name: str
    mmsi: Optional[int] = None
    flag: Optional[str] = None
    vessel_type: Optional[str] = None
    built_year: Optional[int] = None
    owner: Optional[str] = None
    dwt: Optional[int] = None
    risk_score: int = 0
    last_updated: Optional[str] = None

    @property
    def age(self) -> Optional[int]:
        if self.built_year:
            return datetime.now().year - self.built_year
        return None

    @property
    def is_high_risk_flag(self) -> bool:
        return self.flag is not None and self.flag.upper() in HIGH_RISK_FLAGS

    def to_dict(self) -> dict:
        return {
            "imo": self.imo,
            "name": self.name,
            "mmsi": self.mmsi,
            "flag": self.flag,
            "vessel_type": self.vessel_type,
            "built_year": self.built_year,
            "owner": self.owner,
            "dwt": self.dwt,
            "risk_score": self.risk_score,
            "last_updated": self.last_updated,
        }


@dataclass
class SanctionEntry:
    source: SanctionSource
    imo: Optional[int] = None
    vessel_name: Optional[str] = None
    designation_date: Optional[str] = None
    list_name: Optional[str] = None
    raw_data: Optional[str] = None
    id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source.value,
            "imo": self.imo,
            "vessel_name": self.vessel_name,
            "designation_date": self.designation_date,
            "list_name": self.list_name,
            "raw_data": self.raw_data,
        }


@dataclass
class Position:
    imo: int
    lat: float
    lon: float
    timestamp: str
    speed: Optional[float] = None
    course: Optional[float] = None
    source: Optional[str] = None
    id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "imo": self.imo,
            "lat": self.lat,
            "lon": self.lon,
            "timestamp": self.timestamp,
            "speed": self.speed,
            "course": self.course,
            "source": self.source,
        }


@dataclass
class AISGap:
    imo: int
    gap_start: str
    gap_end: str
    duration_hours: float
    near_russian_port: bool = False
    id: Optional[int] = None

    @property
    def is_significant(self) -> bool:
        return self.duration_hours >= 6.0


@dataclass
class Alert:
    imo: int
    score: int
    reasons: list[str] = field(default_factory=list)
    created_at: Optional[str] = None
    id: Optional[int] = None

    def reasons_text(self) -> str:
        return "; ".join(self.reasons)