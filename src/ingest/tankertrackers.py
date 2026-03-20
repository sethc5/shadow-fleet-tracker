"""TankerTrackers sanctioned list sync — fetches and parses sanctioned tanker CSV data."""

import csv
import io
import logging
from pathlib import Path

import requests

from ..db import Database
from ..models import SanctionEntry, SanctionSource, Vessel

logger = logging.getLogger(__name__)

SANCTIONED_URL = "https://tankertrackers.com/report/sanctioned"
CACHE_DIR = Path("data") / "raw"
CACHE_FILE = CACHE_DIR / "tankertrackers_sanctioned.csv"


def download_sanctioned_csv(force: bool = False) -> Path:
    """Download TankerTrackers sanctioned vessels CSV."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if CACHE_FILE.exists() and not force:
        logger.info("Using cached TankerTrackers CSV at %s", CACHE_FILE)
        return CACHE_FILE

    logger.info("Downloading TankerTrackers sanctioned list from %s", SANCTIONED_URL)
    resp = requests.get(SANCTIONED_URL, timeout=60)
    resp.raise_for_status()

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(resp.text)

    logger.info("Downloaded TankerTrackers CSV to %s", CACHE_FILE)
    return CACHE_FILE


def _try_parse_imo(value: str) -> int | None:
    """Attempt to parse an IMO number from a string."""
    cleaned = "".join(c for c in value if c.isdigit())
    if len(cleaned) == 7:
        return int(cleaned)
    return None


def parse_sanctioned_csv(csv_path: Path | None = None) -> list[dict]:
    """Parse TankerTrackers CSV and return vessel records.

    TankerTrackers CSV format varies, so we handle common column names flexibly.
    Returns list of dicts with keys: imo, name, sanctioning_authority, date
    """
    if csv_path is None:
        csv_path = download_sanctioned_csv()

    logger.info("Parsing TankerTrackers CSV from %s", csv_path)

    with open(csv_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Detect delimiter
    delimiter = ","
    if "\t" in content[:500]:
        delimiter = "\t"
    elif ";" in content[:500]:
        delimiter = ";"

    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)

    # Normalize column names
    vessels = []
    for row in reader:
        # Lowercase all keys for flexible matching
        lower_row = {k.lower().strip(): v.strip() if v else "" for k, v in row.items()}

        # Find IMO
        imo = None
        for key in ["imo", "imo_number", "imo number", "imo#"]:
            if key in lower_row and lower_row[key]:
                imo = _try_parse_imo(lower_row[key])
                if imo:
                    break

        # Also scan all columns for IMO-like values
        if imo is None:
            for val in lower_row.values():
                imo = _try_parse_imo(val)
                if imo:
                    break

        if imo is None:
            continue

        # Find vessel name
        name = ""
        for key in ["vessel", "vessel_name", "name", "ship_name", "tanker"]:
            if key in lower_row and lower_row[key]:
                name = lower_row[key]
                break
        if not name:
            name = f"UNKNOWN-{imo}"

        # Find sanctioning authority
        authority = ""
        for key in ["authority", "sanctioning_authority", "sanctioned_by", "source", "list"]:
            if key in lower_row and lower_row[key]:
                authority = lower_row[key]
                break

        # Find date
        date = ""
        for key in ["date", "designation_date", "sanctioned_date", "added"]:
            if key in lower_row and lower_row[key]:
                date = lower_row[key]
                break

        vessels.append({
            "imo": imo,
            "name": name,
            "sanctioning_authority": authority,
            "date": date,
            "raw_row": dict(lower_row),
        })

    logger.info("Parsed %d vessels from TankerTrackers CSV", len(vessels))
    return vessels


def ingest_tankertrackers(db: Database, force_download: bool = False) -> int:
    """Download and ingest TankerTrackers sanctioned vessel data.

    Returns count of vessels ingested.
    """
    csv_path = download_sanctioned_csv(force=force_download)
    vessels = parse_sanctioned_csv(csv_path)

    count = 0
    for v in vessels:
        vessel = Vessel(
            imo=v["imo"],
            name=v["name"],
        )
        db.upsert_vessel(vessel)

        entry = SanctionEntry(
            source=SanctionSource.TANKERTRACKERS,
            imo=v["imo"],
            vessel_name=v["name"],
            designation_date=v["date"],
            list_name=f"TankerTrackers ({v['sanctioning_authority']})" if v["sanctioning_authority"] else "TankerTrackers",
            raw_data=str(v["raw_row"]),
        )
        db.add_sanction(entry)
        count += 1

    logger.info("Ingested %d vessels from TankerTrackers", count)
    return count