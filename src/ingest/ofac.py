"""OFAC SDN List parser — downloads and extracts vessel entries with IMO numbers."""

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import requests

from ..constants import RUSSIA_PROGRAMS
from ..db import Database
from ..models import SanctionEntry, SanctionSource, Vessel

logger = logging.getLogger(__name__)

SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
CACHE_DIR = Path("data") / "raw"
CACHE_FILE = CACHE_DIR / "sdn.xml"

# OFAC programs related to Russia / Ukraine sanctions


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
            # OFAC format evolved in 2026 to use:
            # idType="Vessel Registration Identification", idNumber="IMO 7406784"
            imo = _parse_imo_number(num_text)
            if imo is None:
                continue
            if (
                "IMO" in type_text
                or "IMO" in num_text.upper()
                or "VESSEL REGISTRATION" in type_text
            ):
                return imo
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


def _extract_addresses(entry_elem: ET.Element) -> list[dict]:
    """Extract address snippets for optional downstream geocoding/map enrichment."""
    addresses: list[dict] = []
    address_list = entry_elem.find("addressList")
    if address_list is None:
        return addresses

    for address in address_list.findall("address"):
        address1 = address.find("address1")
        city = address.find("city")
        country = address.find("country")
        if not any(node is not None and node.text for node in (address1, city, country)):
            continue
        addresses.append({
            "address1": address1.text.strip() if address1 is not None and address1.text else "",
            "city": city.text.strip() if city is not None and city.text else "",
            "country": country.text.strip() if country is not None and country.text else "",
        })
    return addresses


def _parse_imo_number(value: str) -> Optional[int]:
    """Extract and validate a 7-digit IMO number from mixed text."""
    if not value:
        return None

    # Preferred explicit format: "IMO 7406784"
    explicit = re.search(r"\bIMO\W*(\d{7})\b", value, flags=re.IGNORECASE)
    if explicit:
        return int(explicit.group(1))

    # Fallback for legacy plain digits in idNumber (e.g. "9876543")
    if value.strip().isdigit() and len(value.strip()) == 7:
        return int(value.strip())

    # Last fallback for mixed registration strings containing a 7-digit IMO.
    fallback = re.search(r"\b(\d{7})\b", value)
    if fallback:
        return int(fallback.group(1))

    return None


def _strip_elem_namespace(elem: ET.Element):
    """Strip namespace in-place for one parsed element."""
    if isinstance(elem.tag, str) and "}" in elem.tag:
        elem.tag = elem.tag.split("}", 1)[1]
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
    vessels = []
    pub_date = ""

    # Stream parse to avoid loading the full OFAC XML tree into memory.
    context = ET.iterparse(str(xml_path), events=("start", "end"))
    for event, elem in context:
        if event != "end":
            continue

        _strip_elem_namespace(elem)

        if elem.tag == "Publish_Date" and elem.text and not pub_date:
            pub_date = elem.text.strip()
        elif elem.tag == "sdnEntry":
            _process_sdn_entry(elem, pub_date, vessels)
            elem.clear()

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
        "addresses": _extract_addresses(entry),
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
