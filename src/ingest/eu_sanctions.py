"""EU Council sanctions list parser — extracts vessel entries with IMO numbers."""

import csv
import io
import logging
from pathlib import Path

import requests

from ..db import Database
from ..models import SanctionEntry, SanctionSource, Vessel

logger = logging.getLogger(__name__)

# EU sanctions CSV export URLs (multiple sources, tried in order)
EU_URLS = [
    "https://sanctionsmap.eu/api/v1/sanctions/export/csv",  # sanctionsmap.eu mirror
    "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList.csv",  # Official EU
]
CACHE_DIR = Path("data") / "raw"
CACHE_FILE = CACHE_DIR / "eu_sanctions.csv"

# Column names in EU consolidated list (may vary, we handle flexibly)
NAME_COLS = ["name", "entity_name", "Name 1", "whole_name"]
IMO_COLS = ["imo", "IMO", "identification_number", "Identification number 1"]
TYPE_COLS = ["subject_type", "Subject type", "entity_type"]
REGULATION_COLS = ["regulation_number", "Regulation number", "regulation"]


def download_eu_csv(force: bool = False) -> Path:
    """Download EU consolidated sanctions list CSV.
    
    Tries multiple URLs in order until one succeeds.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if CACHE_FILE.exists() and not force:
        logger.info("Using cached EU sanctions CSV at %s", CACHE_FILE)
        return CACHE_FILE

    for url in EU_URLS:
        try:
            logger.info("Downloading EU sanctions CSV from %s", url)
            resp = requests.get(url, timeout=120, stream=True)
            resp.raise_for_status()
            
            with open(CACHE_FILE, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            logger.info("Downloaded EU sanctions CSV to %s", CACHE_FILE)
            return CACHE_FILE
            
        except requests.RequestException as e:
            logger.warning("EU sanctions download from %s failed: %s", url, e)
            continue
    
    # If all URLs failed, return cached file if available
    if CACHE_FILE.exists():
        logger.warning("All EU URLs failed, using cached file")
        return CACHE_FILE
    
    raise RuntimeError("Failed to download EU sanctions from all sources")


def _find_column(headers: list[str], candidates: list[str]) -> str | None:
    """Find the first matching column name (case-insensitive)."""
    header_lower = {h.lower().strip(): h for h in headers}
    for candidate in candidates:
        if candidate.lower() in header_lower:
            return header_lower[candidate.lower()]
    return None


def _try_parse_imo(value: str) -> int | None:
    """Extract IMO number from a string."""
    if not value:
        return None
    cleaned = "".join(c for c in str(value) if c.isdigit())
    if len(cleaned) == 7:
        return int(cleaned)
    return None


def parse_eu_vessels(csv_path: Path | None = None) -> list[dict]:
    """Parse EU sanctions CSV and return vessel records with IMO numbers.

    Returns list of dicts with: imo, name, regulation, designation_date
    """
    if csv_path is None:
        csv_path = download_eu_csv()

    logger.info("Parsing EU sanctions CSV from %s", csv_path)

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    # Detect delimiter
    delimiter = ","
    if "\t" in content[:1000]:
        delimiter = "\t"
    elif ";" in content[:1000]:
        delimiter = ";"

    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    headers = reader.fieldnames or []

    # Find relevant columns
    name_col = _find_column(headers, NAME_COLS)
    imo_col = _find_column(headers, IMO_COLS)
    type_col = _find_column(headers, TYPE_COLS)
    reg_col = _find_column(headers, REGULATION_COLS)

    if not name_col:
        logger.warning("Could not find name column in EU CSV. Headers: %s", headers[:10])
        return []

    vessels = []
    seen_imos = set()

    for row in reader:
        # Normalize keys to lowercase
        lower_row = {k.lower().strip(): v.strip() if v else "" for k, v in row.items()}

        # Check if this is a vessel type (if type column exists)
        if type_col:
            row_type = lower_row.get(type_col.lower(), "").lower()
            if row_type and "vessel" not in row_type and "ship" not in row_type:
                continue

        # Try to find IMO in dedicated column first
        imo = None
        if imo_col:
            imo = _try_parse_imo(lower_row.get(imo_col.lower(), ""))

        # If no IMO column, scan all columns for IMO-like values
        if imo is None:
            for val in lower_row.values():
                imo = _try_parse_imo(val)
                if imo:
                    break

        if imo is None or imo in seen_imos:
            continue

        seen_imos.add(imo)

        # Get name
        name = lower_row.get(name_col.lower(), f"UNKNOWN-{imo}")

        # Get regulation
        regulation = ""
        if reg_col:
            regulation = lower_row.get(reg_col.lower(), "")

        # Get designation date
        designation_date = ""
        for date_col in ["regulation_date", "publication_date", "date", "entry_into_force"]:
            if date_col in lower_row and lower_row[date_col]:
                designation_date = lower_row[date_col]
                break

        vessels.append({
            "imo": imo,
            "name": name,
            "regulation": regulation,
            "designation_date": designation_date,
            "raw_row": dict(lower_row),
        })

    logger.info("Parsed %d unique vessel entries from EU sanctions CSV", len(vessels))
    return vessels


def ingest_eu_sanctions(db: Database, force_download: bool = False) -> int:
    """Download and ingest EU sanctions vessel data.

    Returns count of vessels ingested.
    """
    csv_path = download_eu_csv(force=force_download)
    vessels = parse_eu_vessels(csv_path)

    count = 0
    for v in vessels:
        vessel = Vessel(
            imo=v["imo"],
            name=v["name"],
        )
        db.upsert_vessel(vessel)

        entry = SanctionEntry(
            source=SanctionSource.EU,
            imo=v["imo"],
            vessel_name=v["name"],
            designation_date=v["designation_date"],
            list_name=f"EU Council ({v['regulation']})" if v["regulation"] else "EU Council",
            raw_data=str(v["raw_row"]),
        )
        db.add_sanction(entry)
        count += 1

    logger.info("Ingested %d vessels from EU sanctions", count)
    return count