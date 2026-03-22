"""OpenSanctions result parser — shared utility for parsing vessel data."""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def parse_opensanctions_result(result: dict[str, Any]) -> dict[str, Any]:
    """Parse an OpenSanctions API result into our standard vessel format.
    
    Args:
        result: Raw OpenSanctions API response dict
        
    Returns:
        Dict with standardized vessel fields:
        - imo: int or None
        - name: str
        - flag: str or None
        - vessel_type: str or None
        - built_year: int or None
        - owner: str or None
        - programs: list[str]
        - source_url: str
        - raw: dict (original result)
    """
    props = result.get("properties", {})

    # Extract IMO from identifiers
    imo: Optional[int] = None
    imo_numbers = props.get("imoNumber", [])
    if imo_numbers:
        for val in imo_numbers:
            if val and str(val).isdigit():
                imo = int(val)
                break

    name = props.get("name", [""])[0] if props.get("name") else ""
    flag = props.get("flag", [""])[0] if props.get("flag") else None
    vessel_type = props.get("type", [""])[0] if props.get("type") else None

    # Built year
    built_year: Optional[int] = None
    build_dates = props.get("buildDate", [])
    if build_dates:
        for d in build_dates:
            if d and len(str(d)) >= 4:
                try:
                    built_year = int(str(d)[:4])
                    break
                except ValueError:
                    pass

    # Owner
    owner = props.get("owner", [""])[0] if props.get("owner") else None

    # Sanctions programs
    programs = props.get("program", [])

    # Source link
    source_url = result.get("id", "")

    return {
        "imo": imo,
        "name": name,
        "flag": flag,
        "vessel_type": vessel_type,
        "built_year": built_year,
        "owner": owner,
        "programs": programs if programs else ["OpenSanctions"],
        "source_url": source_url,
        "raw": result,
    }
