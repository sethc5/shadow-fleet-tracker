"""AIS vessel position ingestion — multi-source cascade with evasion behavior detection."""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from ..db import Database
from ..models import Position, Vessel
from ..scoring import RUSSIAN_PORTS, _haversine

logger = logging.getLogger(__name__)

# --- Source configurations ---

AISHUB_BASE = "http://data.aishub.net"
VESSELFINDER_API = "https://www.vesselfinder.com/api/pub"
BARENTSWATCH_API = "https://live.ais.barentswatch.no/v1/latest/combined/by"

# Request timeout per source
TIMEOUT = 15


# =============================================================================
# AISHub — free, no auth
# =============================================================================

def fetch_aishub(mmsi: int) -> list[dict]:
    """Fetch vessel position from AISHub.

    Returns list of dicts with: lat, lon, speed, course, timestamp
    """
    url = f"{AISHUB_BASE}/stations.php"
    params = {"mmsi": mmsi, "format": 1, "output": "json"}

    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        # AISHub returns {"error": "..."} or {"info": [...]}
        if isinstance(data, dict) and "error" in data:
            logger.debug("AISHub error for MMSI %d: %s", mmsi, data["error"])
            return []

        # Parse response — structure varies
        positions = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("info", data.get("data", [data]))
        else:
            return []

        for item in items if isinstance(items, list) else [items]:
            pos = _parse_aishub_record(item, mmsi)
            if pos:
                positions.append(pos)

        return positions

    except requests.RequestException as e:
        logger.warning("AISHub request failed for MMSI %d: %s", mmsi, e)
        return []


def _parse_aishub_record(item: dict, mmsi: int) -> Optional[dict]:
    """Parse a single AISHub record into our standard format."""
    try:
        lat = float(item.get("LAT", item.get("lat", 0)))
        lon = float(item.get("LON", item.get("lon", 0)))
        if lat == 0 and lon == 0:
            return None

        timestamp = item.get("TIME", item.get("time", item.get("TIMESTAMP", "")))
        speed = _safe_float(item.get("SPEED", item.get("speed")))
        course = _safe_float(item.get("COURSE", item.get("course")))

        return {
            "mmsi": mmsi,
            "lat": lat,
            "lon": lon,
            "speed": speed,
            "course": course,
            "timestamp": str(timestamp),
            "source": "aishub",
        }
    except (ValueError, TypeError):
        return None


# =============================================================================
# VesselFinder — scrape fallback
# =============================================================================

def fetch_vesselfinder(mmsi: int) -> list[dict]:
    """Fetch vessel position from VesselFinder API/search.

    Returns list of position dicts.
    """
    # Try the public vessel search endpoint
    url = f"{VESSELFINDER_API}/vesselsearch"
    params = {"term": str(mmsi), "type": 0}

    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT, headers={
            "User-Agent": "ShadowFleetTracker/0.2",
            "Accept": "application/json",
        })

        if resp.status_code != 200:
            logger.debug("VesselFinder returned %d for MMSI %d", resp.status_code, mmsi)
            return []

        data = resp.json()

        # VesselFinder returns list of matches
        items = data if isinstance(data, list) else data.get("results", data.get("data", []))

        positions = []
        for item in items if isinstance(items, list) else [items]:
            pos = _parse_vesselfinder_record(item, mmsi)
            if pos:
                positions.append(pos)
                break  # Take first match only

        return positions

    except requests.RequestException as e:
        logger.warning("VesselFinder request failed for MMSI %d: %s", mmsi, e)
        return []
    except (ValueError, KeyError) as e:
        logger.debug("VesselFinder parse error for MMSI %d: %s", mmsi, e)
        return []


def _parse_vesselfinder_record(item: dict, mmsi: int) -> Optional[dict]:
    """Parse a VesselFinder record."""
    try:
        lat = float(item.get("lat", item.get("latitude", 0)))
        lon = float(item.get("lon", item.get("longitude", 0)))
        if lat == 0 and lon == 0:
            return None

        speed = _safe_float(item.get("speed", item.get("sog")))
        course = _safe_float(item.get("course", item.get("cog")))
        timestamp = item.get("time", item.get("timestamp", datetime.utcnow().isoformat()))

        return {
            "mmsi": mmsi,
            "lat": lat,
            "lon": lon,
            "speed": speed,
            "course": course,
            "timestamp": str(timestamp),
            "source": "vesselfinder",
        }
    except (ValueError, TypeError):
        return None


# =============================================================================
# BarentsWatch — free registration, excellent Baltic/Arctic coverage
# =============================================================================

def fetch_barentswatch(mmsi: int) -> list[dict]:
    """Fetch vessel position from BarentsWatch AIS.

    BarentsWatch requires free registration but no payment.
    Set BARENTSWATCH_CLIENT_ID and BARENTSWATCH_CLIENT_SECRET in .env.
    """
    import os

    client_id = os.environ.get("BARENTSWATCH_CLIENT_ID")
    client_secret = os.environ.get("BARENTSWATCH_CLIENT_SECRET")

    if not client_id or not client_secret:
        logger.debug("BarentsWatch credentials not configured, skipping")
        return []

    # Get OAuth token
    try:
        token_resp = requests.post(
            "https://id.barentswatch.no/connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "ais",
            },
            timeout=TIMEOUT,
        )
        token_resp.raise_for_status()
        token = token_resp.json().get("access_token")
    except requests.RequestException as e:
        logger.warning("BarentsWatch auth failed: %s", e)
        return []

    # Fetch position
    url = f"{BARENTSWATCH_API}"
    params = {"mmsi": mmsi}

    try:
        resp = requests.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )

        if resp.status_code == 404:
            return []

        resp.raise_for_status()
        data = resp.json()

        if not data:
            return []

        pos = _parse_barentswatch_record(data, mmsi)
        return [pos] if pos else []

    except requests.RequestException as e:
        logger.warning("BarentsWatch request failed for MMSI %d: %s", mmsi, e)
        return []


def _parse_barentswatch_record(item: dict, mmsi: int) -> Optional[dict]:
    """Parse a BarentsWatch AIS record."""
    try:
        lat = float(item.get("latitude", item.get("lat", 0)))
        lon = float(item.get("longitude", item.get("lon", 0)))
        if lat == 0 and lon == 0:
            return None

        speed = _safe_float(item.get("speedOverGround", item.get("speed")))
        course = _safe_float(item.get("courseOverGround", item.get("course")))
        timestamp = item.get("msgTime", item.get("timestamp", datetime.utcnow().isoformat()))

        return {
            "mmsi": mmsi,
            "lat": lat,
            "lon": lon,
            "speed": speed,
            "course": course,
            "timestamp": str(timestamp),
            "source": "barentswatch",
        }
    except (ValueError, TypeError):
        return None


# =============================================================================
# Unified fetcher — cascade with redundancy
# =============================================================================

def fetch_positions(mmsi: int) -> list[dict]:
    """Fetch positions from all available sources, merge and deduplicate.

    Cascade: AISHub → VesselFinder → BarentsWatch
    """
    all_positions = []

    # Source 1: AISHub
    positions = fetch_aishub(mmsi)
    if positions:
        logger.info("AISHub: %d position(s) for MMSI %d", len(positions), mmsi)
        all_positions.extend(positions)

    # Source 2: VesselFinder (always try for redundancy)
    positions = fetch_vesselfinder(mmsi)
    if positions:
        logger.info("VesselFinder: %d position(s) for MMSI %d", len(positions), mmsi)
        all_positions.extend(positions)

    # Source 3: BarentsWatch (if configured)
    positions = fetch_barentswatch(mmsi)
    if positions:
        logger.info("BarentsWatch: %d position(s) for MMSI %d", len(positions), mmsi)
        all_positions.extend(positions)

    # Deduplicate by timestamp (keep first from each source)
    seen = set()
    unique = []
    for p in all_positions:
        key = (round(p["lat"], 4), round(p["lon"], 4), p["timestamp"][:16])
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique


def resolve_mmsi(db: Database, imo: int) -> Optional[int]:
    """Resolve MMSI for a vessel from DB or external sources."""
    vessel = db.get_vessel(imo)
    if vessel and vessel.mmsi:
        return vessel.mmsi

    # Try OpenSanctions
    try:
        from .opensanctions import lookup_by_imo
        results = lookup_by_imo(imo)
        for r in results:
            props = r.get("properties", {})
            mmsi_list = props.get("mmsi", [])
            for m in mmsi_list:
                if m and str(m).isdigit():
                    return int(m)
    except Exception:
        pass

    return None


def ingest_positions(db: Database, imo: int) -> int:
    """Fetch and store positions for a vessel.

    Returns count of new positions stored.
    """
    mmsi = resolve_mmsi(db, imo)
    if mmsi is None:
        logger.warning("Cannot resolve MMSI for IMO %d, skipping", imo)
        return 0

    positions = fetch_positions(mmsi)
    if not positions:
        return 0

    count = 0
    for p in positions:
        pos = Position(
            imo=imo,
            lat=p["lat"],
            lon=p["lon"],
            timestamp=p["timestamp"],
            speed=p.get("speed"),
            course=p.get("course"),
            source=p.get("source"),
        )
        db.add_position(pos)
        count += 1

    logger.info("Stored %d positions for IMO %d (MMSI %d)", count, imo, mmsi)
    return count


def ingest_all_positions(db: Database, limit: int = 50) -> int:
    """Batch ingest positions for top N unsanctioned vessels by risk score.

    Returns total positions stored.
    """
    vessels = db.get_all_vessels()

    # Filter: unsanctioned, have risk score > 0
    candidates = []
    for v in vessels:
        sanctions = db.get_sanctions_for_vessel(v.imo)
        is_sanctioned = any(s.source.value in ("ofac", "eu", "uk") for s in sanctions)
        if not is_sanctioned and v.risk_score > 0:
            candidates.append(v)

    # Sort by risk score descending
    candidates.sort(key=lambda v: v.risk_score, reverse=True)
    candidates = candidates[:limit]

    logger.info("Batch tracking %d vessels", len(candidates))

    total = 0
    for i, vessel in enumerate(candidates):
        count = ingest_positions(db, vessel.imo)
        total += count
        logger.info("[%d/%d] %s (IMO %d): %d positions", i + 1, len(candidates), vessel.name, vessel.imo, count)

        # Rate limit: sleep between vessels
        if i < len(candidates) - 1:
            time.sleep(1.5)

    return total


def discover_new_vessels(db: Database, hours: int = 48) -> int:
    """Discover new vessels by tracking all existing vessels and finding Russian-flagged ones.

    Scans recent positions for vessels near Russian ports or with Russian flags,
    then tracks any new ones not yet in the database.

    Returns count of newly discovered vessels.
    """
    # Get all vessels with positions in the last N hours
    existing_imos = {v.imo for v in db.get_all_vessels()}

    # Check all tracked vessels' recent positions for Russian port proximity
    new_count = 0
    for vessel in db.get_all_vessels():
        positions = db.get_positions(vessel.imo, limit=100)
        if not positions:
            continue

        # Check if any position is near a Russian port
        for pos in positions:
            for port_lat, port_lon, port_name in RUSSIAN_PORTS:
                dist = _haversine(pos.lat, pos.lon, port_lat, port_lon)
                if dist < 50:
                    # This vessel is near Russia — make sure it's tracked
                    if vessel.imo not in existing_imos:
                        db.upsert_vessel(vessel)
                        new_count += 1
                    break

    # Also search for Russian-flagged vessels via OpenSanctions
    try:
        from .opensanctions import search_vessels
        results = search_vessels(query="Russian tanker oil", limit=100)
        for r in results:
            parsed = _parse_opensanctions_result(r)
            if parsed.get("imo") and parsed["imo"] not in existing_imos:
                vessel = Vessel(
                    imo=parsed["imo"],
                    name=parsed.get("name", f"UNKNOWN-{parsed['imo']}"),
                    flag=parsed.get("flag"),
                    vessel_type=parsed.get("vessel_type"),
                    built_year=parsed.get("built_year"),
                    owner=parsed.get("owner"),
                )
                db.upsert_vessel(vessel)
                new_count += 1
                existing_imos.add(parsed["imo"])
    except Exception as e:
        logger.warning("OpenSanctions discovery failed: %s", e)

    logger.info("Discovered %d new vessels", new_count)
    return new_count


def _parse_opensanctions_result(result: dict) -> dict:
    """Parse an OpenSanctions result (inline to avoid circular import)."""
    props = result.get("properties", {})
    imo = None
    imo_numbers = props.get("imoNumber", [])
    if imo_numbers:
        for val in imo_numbers:
            if val and str(val).isdigit():
                imo = int(val)
                break
    return {
        "imo": imo,
        "name": props.get("name", [""])[0] if props.get("name") else "",
        "flag": props.get("flag", [""])[0] if props.get("flag") else None,
        "vessel_type": props.get("type", [""])[0] if props.get("type") else None,
        "built_year": None,
        "owner": props.get("owner", [""])[0] if props.get("owner") else None,
    }


# =============================================================================
# Evasion behavior detectors
# =============================================================================

def detect_dark_events(positions: list[dict], gap_hours: float = 6.0) -> list[dict]:
    """Detect AIS dark periods (gaps in reporting).

    Returns list of gap dicts with: gap_start, gap_end, duration_hours, near_russia
    """
    if len(positions) < 2:
        return []

    # Sort by timestamp
    sorted_pos = sorted(positions, key=lambda p: p["timestamp"])

    gaps = []
    for i in range(1, len(sorted_pos)):
        try:
            t1 = _parse_timestamp(sorted_pos[i - 1]["timestamp"])
            t2 = _parse_timestamp(sorted_pos[i]["timestamp"])
        except (ValueError, TypeError):
            continue

        if t1 is None or t2 is None:
            continue

        gap_duration = (t2 - t1).total_seconds() / 3600

        if gap_duration >= gap_hours:
            # Check if gap start is near a Russian port
            near_russia = False
            for lat, lon, port_name in RUSSIAN_PORTS:
                dist = _haversine(
                    sorted_pos[i - 1]["lat"],
                    sorted_pos[i - 1]["lon"],
                    lat, lon,
                )
                if dist < 100:  # Within 100km of a Russian port
                    near_russia = True
                    break

            gaps.append({
                "gap_start": sorted_pos[i - 1]["timestamp"],
                "gap_end": sorted_pos[i]["timestamp"],
                "duration_hours": round(gap_duration, 1),
                "near_russia": near_russia,
                "start_lat": sorted_pos[i - 1]["lat"],
                "start_lon": sorted_pos[i - 1]["lon"],
            })

    return gaps


def detect_port_calls(positions: list[dict], radius_km: float = 30.0) -> list[dict]:
    """Detect proximity to Russian oil export ports.

    Returns list of port call dicts with: port_name, lat, lon, timestamp, distance_km
    """
    calls = []
    seen_ports = set()

    for pos in positions:
        for port_lat, port_lon, port_name in RUSSIAN_PORTS:
            dist = _haversine(pos["lat"], pos["lon"], port_lat, port_lon)

            if dist <= radius_km:
                # Only flag first occurrence per port to avoid noise
                if port_name not in seen_ports:
                    seen_ports.add(port_name)
                    calls.append({
                        "port_name": port_name,
                        "lat": pos["lat"],
                        "lon": pos["lon"],
                        "timestamp": pos["timestamp"],
                        "distance_km": round(dist, 1),
                    })

    return calls


def detect_sts_transfers(positions: list[dict], all_vessel_positions: dict[int, list[dict]]) -> list[dict]:
    """Detect potential ship-to-ship transfers.

    Heuristic: two vessels both have dark periods that overlap, and their
    last positions before going dark are within 10km of each other.

    Args:
        positions: Positions for the target vessel
        all_vessel_positions: Dict of {imo: positions} for all tracked vessels
    """
    target_gaps = detect_dark_events(positions)
    if not target_gaps:
        return []

    transfers = []
    for gap in target_gaps:
        if not gap["near_russia"]:
            continue  # STS more likely near Russia

        # Check other vessels for overlapping dark periods
        for other_imo, other_positions in all_vessel_positions.items():
            other_gaps = detect_dark_events(other_positions)

            for other_gap in other_gaps:
                # Check time overlap
                if _gaps_overlap(gap, other_gap):
                    # Check spatial proximity at gap start
                    dist = _haversine(
                        gap["start_lat"], gap["start_lon"],
                        other_gap["start_lat"], other_gap["start_lon"],
                    )
                    if dist < 10:  # Within 10km
                        transfers.append({
                            "vessel_1_gap": gap,
                            "vessel_2_imo": other_imo,
                            "vessel_2_gap": other_gap,
                            "proximity_km": round(dist, 1),
                        })

    return transfers


# =============================================================================
# Helpers
# =============================================================================

def _safe_float(value) -> Optional[float]:
    """Safely convert to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse various timestamp formats."""
    if not ts:
        return None

    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(ts.strip(), fmt)
        except ValueError:
            continue

    # Try ISO format
    try:
        return datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _gaps_overlap(gap1: dict, gap2: dict) -> bool:
    """Check if two time gaps overlap."""
    try:
        s1 = _parse_timestamp(gap1["gap_start"])
        e1 = _parse_timestamp(gap1["gap_end"])
        s2 = _parse_timestamp(gap2["gap_start"])
        e2 = _parse_timestamp(gap2["gap_end"])

        if any(x is None for x in [s1, e1, s2, e2]):
            return False

        return s1 < e2 and s2 < e1
    except (ValueError, TypeError):
        return False