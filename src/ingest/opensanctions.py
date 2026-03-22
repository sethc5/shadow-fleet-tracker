"""OpenSanctions API client — queries vessel data from aggregated sanctions database."""

import logging
import os
from typing import Optional

import requests

from .opensanctions_parser import parse_opensanctions_result
from ..db import Database
from ..models import SanctionEntry, SanctionSource, Vessel

logger = logging.getLogger(__name__)

BASE_URL = "https://api.opensanctions.org"
SEARCH_URL = f"{BASE_URL}/search/"
MATCH_URL = f"{BASE_URL}/match/"


def _get_headers() -> dict:
    """Build request headers with optional API key."""
    headers = {"Accept": "application/json"}
    api_key = os.environ.get("OPENSANCTIONS_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def search_vessels(
    query: str = "",
    imo: Optional[int] = None,
    limit: int = 50,
) -> list[dict]:
    """Search OpenSanctions for vessels.

    Args:
        query: Free-text search (vessel name, etc.)
        imo: Filter by IMO number
        limit: Max results to return

    Returns list of vessel dicts from OpenSanctions.
    """
    params = {
        "schema": "Vessel",
        "scope": "default",
        "limit": limit,
    }
    if query:
        params["q"] = query
    if imo:
        params["q"] = str(imo)

    logger.info("Searching OpenSanctions: q=%s imo=%s", query, imo)
    resp = requests.get(SEARCH_URL, params=params, headers=_get_headers(), timeout=30)

    if resp.status_code == 401:
        logger.warning("OpenSanctions requires API key (set OPENSANCTIONS_API_KEY)")
        return []

    resp.raise_for_status()

    data = resp.json()
    results = data.get("results", [])
    logger.info("OpenSanctions returned %d results", len(results))
    return results


def lookup_by_imo(imo: int) -> list[dict]:
    """Look up a specific vessel by IMO number."""
    return search_vessels(imo=imo)


def ingest_opensanctions(db: Database, query: str = "shadow fleet Russia oil tanker") -> int:
    """Search OpenSanctions and ingest matching vessels.

    For bulk ingestion, we search by common shadow fleet terms.
    Returns count of vessels ingested.
    """
    results = search_vessels(query=query, limit=200)

    count = 0
    for result in results:
        parsed = parse_opensanctions_result(result)
        if parsed["imo"] is None:
            continue

        vessel = Vessel(
            imo=parsed["imo"],
            name=parsed["name"],
            flag=parsed["flag"],
            vessel_type=parsed["vessel_type"],
            built_year=parsed["built_year"],
            owner=parsed["owner"],
        )
        db.upsert_vessel(vessel)

        for program in parsed["programs"]:
            entry = SanctionEntry(
                source=SanctionSource.OPENSANCTIONS,
                imo=parsed["imo"],
                vessel_name=parsed["name"],
                list_name=f"OpenSanctions ({program})",
                raw_data=str(parsed["raw"]),
            )
            db.add_sanction(entry)

        count += 1

    logger.info("Ingested %d vessels from OpenSanctions", count)
    return count