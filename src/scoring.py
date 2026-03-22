"""Risk scoring engine — rule-based evasion indicator scoring for vessels."""

import logging
from datetime import datetime, timedelta

from .config import get_config
from .constants import HIGH_RISK_FLAGS, RUSSIAN_PORTS
from .db import Database
from .models import Alert, Vessel

logger = logging.getLogger(__name__)


def _get_weights() -> dict:
    return get_config()["scoring"]["weights"]


def _get_threshold() -> int:
    return get_config()["scoring"]["alert_threshold"]


def _get_high_risk_flags() -> set:
    # Use config if overridden, otherwise use defaults from constants
    config_flags = get_config()["scoring"].get("high_risk_flags", [])
    if config_flags:
        return set(f.upper() for f in config_flags)
    return HIGH_RISK_FLAGS.copy()


def score_vessel(db: Database, imo: int) -> tuple[int, list[str]]:
    """Calculate risk score for a vessel.

    Returns (score, list of reason strings).
    """
    vessel = db.get_vessel(imo)
    if vessel is None:
        return 0, []

    weights = _get_weights()
    score = 0
    reasons = []

    # Check sanctions
    sanctions = db.get_sanctions_for_vessel(imo)
    if sanctions:
        score += weights["sanctioned"]
        sources = list(set(s.source.value for s in sanctions))
        reasons.append(f"Sanctioned ({', '.join(sources)})")

    # Check flag
    if vessel.flag and vessel.flag.upper() in _get_high_risk_flags():
        score += weights["flag_high_risk"]
        reasons.append(f"High-risk flag: {vessel.flag}")

    # Check vessel age
    if vessel.age is not None and vessel.age > 20:
        score += weights["age_over_20"]
        reasons.append(f"Vessel age: {vessel.age} years")

    # Check for recent flag changes
    flag_changes = db.get_recent_changes(imo, "flag", days=90)
    if flag_changes:
        score += weights["flag_change_90d"]
        latest = flag_changes[0]
        reasons.append(f"Flag change: {latest['old']} → {latest['new']}")

    # Check for recent ownership changes
    owner_changes = db.get_recent_changes(imo, "owner", days=90)
    if owner_changes:
        score += weights["ownership_change_90d"]
        latest = owner_changes[0]
        reasons.append(f"Ownership change: {latest['old']} → {latest['new']}")

    return min(score, 100), reasons


def score_vessel_with_positions(db: Database, imo: int) -> tuple[int, list[str]]:
    """Calculate risk score including position-based indicators.

    Requires position data (v0.2+). Falls back to basic scoring if no positions.
    """
    score, reasons = score_vessel(db, imo)

    positions = db.get_positions(imo, limit=500)
    if not positions:
        return score, reasons

    weights = _get_weights()

    # Sort positions by timestamp
    positions.sort(key=lambda p: p.timestamp)

    # Check for AIS gaps > 6 hours
    for i in range(1, len(positions)):
        try:
            t1 = datetime.fromisoformat(positions[i - 1].timestamp)
            t2 = datetime.fromisoformat(positions[i].timestamp)
        except (ValueError, TypeError):
            continue

        gap_hours = (t2 - t1).total_seconds() / 3600
        if gap_hours >= 6:
            # Check if near a Russian port
            near_russia = any(
                _haversine(positions[i - 1].lat, positions[i - 1].lon, lat, lon) < 50
                for lat, lon, _ in RUSSIAN_PORTS
            )
            if near_russia:
                score += weights["ais_dark_russia"]
                reasons.append(f"AIS gap {gap_hours:.0f}h near Russian port")
            else:
                score += weights["ais_dark_open"]
                reasons.append(f"AIS gap {gap_hours:.0f}h in open water")

    # Check for Russian port proximity in recent 30 days
    cutoff_30d = datetime.now() - timedelta(days=30)
    for pos in positions:
        try:
            pos_time = datetime.fromisoformat(pos.timestamp)
        except (ValueError, TypeError):
            continue

        if pos_time < cutoff_30d:
            continue

        for lat, lon, port_name in RUSSIAN_PORTS:
            if _haversine(pos.lat, pos.lon, lat, lon) < 30:
                score += weights["russian_port_30d"]
                reasons.append(f"Russian port call: {port_name}")
                break
        else:
            continue
        break  # Only count once

    return min(score, 100), reasons


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in km using Haversine formula."""
    import math

    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def run_scoring(db: Database) -> int:
    """Run scoring on all vessels and generate alerts for high-risk ones.

    Uses position-based scoring when available, falls back to basic scoring.
    Deduplicates alerts — only creates new alert if score is higher than previous.
    Returns count of alerts generated.
    """
    vessels = db.get_all_vessels()
    alert_count = 0
    threshold = _get_threshold()

    for vessel in vessels:
        # Use position-based scoring if positions exist
        positions = db.get_positions(vessel.imo, limit=500)
        if positions:
            score, reasons = score_vessel_with_positions(db, vessel.imo)
        else:
            score, reasons = score_vessel(db, vessel.imo)

        # Update vessel risk score
        db.update_risk_score(vessel.imo, score)

        # Generate alert if above threshold and not already sanctioned
        if score >= threshold:
            sanctions = db.get_sanctions_for_vessel(vessel.imo)
            is_sanctioned = len(sanctions) > 0

            if not is_sanctioned:
                # Dedup: skip if we already alerted at same or higher score
                latest = db.get_latest_alert(vessel.imo)
                if latest and latest.score >= score:
                    continue

                alert = Alert(imo=vessel.imo, score=score, reasons=reasons)
                db.add_alert(alert)
                alert_count += 1
                logger.info(
                    "ALERT: %s (IMO %d) score=%d: %s",
                    vessel.name, vessel.imo, score, "; ".join(reasons),
                )

    logger.info("Scoring complete: %d alerts generated from %d vessels", alert_count, len(vessels))
    return alert_count