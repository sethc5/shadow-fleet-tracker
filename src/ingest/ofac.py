"""OFAC SDN List parser — downloads and extracts vessel entries with IMO numbers."""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import requests

from ..db import Database
from ..models import SanctionEntry, SanctionSource, Vessel

logger = logging.getLogger(__name__)

SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
CACHE_DIR = Path("data") / "raw"
CACHE_FILE = CACHE_DIR / "sdn.xml"

# OFAC programs related to Russia / Ukraine sanctions
RUSSIA_PROGRAMS = {
    "UKRAINE-EO14024",
    "UKRAINE-EO13662",
    "RUSSIA-EO14024",
    "RUSSIA-EO13662",
    "CYBER2",
    "CYBER",
    "NPWMD",
    "IFSR",
}


def download_sdn_xml(force: bool = False) -> Path:
    """Download the OFAC SDN XML file. Returns path to cached file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if CACHE_FILE.exists() and not force:
        logger.info("Using cached SDN XML at %s", CACHE_FILE)
        return CACHE_FILE

    logger.info("Downloading SDN XML from %s", SDN_URL)
    resp = requests.get(SDN_URL, timeout=120, stream=True)
    resp.raise_for_status()

    with open(CACHE_FILE, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info("Downloaded SDN XML to %s", CACHE_FILE)
    return CACHE_FILE


def _extract_imo_from_entry(entry_elem: ET.Element) -> Optional[int]:
    """Extract IMO number from an SDN entry's idList."""
    id_list = entry_elem.find("idList")
    if id_list is None:
        return None

    for id_item in id_list.findall("id"):
        id_type = id_item.find("idType")
        id_num = id_item.find("idNumber")
        if id_type is not None and id_num is not None:
            type_text = (id_type.text or "").strip().upper()
            num_text = (id_num.text or "").strip()
            # OFAC uses "IMO Ship Identification Number" or just "IMO"
            if "IMO" in type_text and num_text.isdigit():
                return int(num_text)
    return None


def _extract_programs(entry_elem: ET.Element) -> list[str]:
    """Extract sanctions program names from an SDN entry."""
    programs = []
    program_list = entry_elem.find("programList")
    if program_list is not None:
        for prog in program_list.findall("program"):
            if prog.text:
                programs.append(prog.text.strip())
    return programs


def _extract_vessel_info(entry_elem: ET.Element) -> dict:
    """Extract vessel-specific info from an SDN entry."""
    info = {}
    vessel = entry_elem.find("vesselInfo")
    if vessel is not None:
        call_sign = vessel.find("callSign")
        vessel_type = vessel.find("vesselType")
        tonnage = vessel.find("tonnage")
        gross_tonnage = vessel.find("grossRegisteredTonnage")
        flag = vessel.find("vesselFlag")

        if call_sign is not None and call_sign.text:
            info["call_sign"] = call_sign.text.strip()
        if vessel_type is not None and vessel_type.text:
            info["vessel_type"] = vessel_type.text.strip()
        if tonnage is not None and tonnage.text:
            try:
                info["dwt"] = int(tonnage.text.strip().replace(",", ""))
            except ValueError:
                pass
        if gross_tonnage is not None and gross_tonnage.text:
            try:
                info["gross_tonnage"] = int(gross_tonnage.text.strip().replace(",", ""))
            except ValueError:
                pass
        if flag is not None and flag.text:
            info["flag"] = flag.text.strip()

    return info


def _strip_namespaces(root: ET.Element):
    """Remove XML namespaces from all elements for easier parsing."""
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
        # Also strip namespace from attributes
        cleaned_attrib = {}
        for key, value in elem.attrib.items():
            if "}" in key:
                key = key.split("}", 1)[1]
            cleaned_attrib[key] = value
        elem.attrib = cleaned_attrib


def parse_sdn_vessels(xml_path: Optional[Path] = None) -> list[dict]:
    """Parse SDN XML and return list of vessel records with IMO numbers.

    Returns list of dicts with keys: imo, name, programs, designation_date, list_name, vessel_info
    """
    if xml_path is None:
        xml_path = download_sdn_xml()

    logger.info("Parsing SDN XML from %s", xml_path)
    tree = ET.parse(str(xml_path))
    root = tree.getroot()

    # Strip namespaces for consistent parsing
    _strip_namespaces(root)

    # Get publication date
    pub_date = ""
    pub_date_elem = root.find("publshInformation/Publish_Date")
    if pub_date_elem is not None and pub_date_elem.text:
        pub_date = pub_date_elem.text.strip()

    vessels = []
    for entry in root.iter("sdnEntry"):
        _process_sdn_entry(entry, pub_date, vessels)

    logger.info("Found %d vessel entries with IMO numbers", len(vessels))
    return vessels


def _process_sdn_entry(entry: ET.Element, pub_date: str, vessels: list[dict]):
    """Process a single SDN entry and append to vessels list if it has an IMO."""
    # Check if this is a vessel type
    sdn_type = entry.find("sdnType")
    if sdn_type is None or sdn_type.text is None:
        return
    if "vessel" not in sdn_type.text.strip().lower():
        return

    imo = _extract_imo_from_entry(entry)
    if imo is None:
        return

    # Get vessel name
    name_elem = entry.find("lastName")
    name = name_elem.text.strip() if name_elem is not None and name_elem.text else f"UNKNOWN-{imo}"

    programs = _extract_programs(entry)
    vessel_info = _extract_vessel_info(entry)

    # Get designation date from entry
    entry_date = ""
    date_elem = entry.find("dateOfBirthList/dateOfBirthItem/dateOfBirth")
    if date_elem is not None and date_elem.text:
        entry_date = date_elem.text.strip()

    vessels.append({
        "imo": imo,
        "name": name,
        "programs": programs,
        "designation_date": entry_date or pub_date,
        "list_name": "OFAC SDN",
        "vessel_info": vessel_info,
    })


def ingest_ofac(db: Database, force_download: bool = False, xml_path: Optional[Path] = None) -> int:
    """Download and ingest OFAC SDN vessel data into the database.

    Returns count of vessels ingested.
    """
    if xml_path is None:
        xml_path = download_sdn_xml(force=force_download)
    vessels = parse_sdn_vessels(xml_path)

    count = 0
    for v in vessels:
        # Upsert vessel record
        vessel = Vessel(
            imo=v["imo"],
            name=v["name"],
            flag=v["vessel_info"].get("flag"),
            vessel_type=v["vessel_info"].get("vessel_type"),
            dwt=v["vessel_info"].get("dwt"),
        )
        db.upsert_vessel(vessel)

        # Add sanction entry for each program
        for program in v["programs"]:
            entry = SanctionEntry(
                source=SanctionSource.OFAC,
                imo=v["imo"],
                vessel_name=v["name"],
                designation_date=v["designation_date"],
                list_name=f"OFAC SDN ({program})",
                raw_data=str(v),
            )
            db.add_sanction(entry)

        count += 1

    logger.info("Ingested %d OFAC vessels", count)
    return count