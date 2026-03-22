"""Shared constants for Shadow Fleet Tracker."""

# Major Russian oil export ports (lat, lon, name)
RUSSIAN_PORTS = [
    (59.88, 29.88, "Primorsk"),
    (59.68, 28.31, "Ust-Luga"),
    (44.72, 37.77, "Novorossiysk"),
    (45.34, 36.67, "Kavkaz"),
    (43.08, 131.89, "Vladivostok"),
    (69.00, 33.02, "Murmansk"),
]

# High-risk flag registries commonly used by shadow fleet
HIGH_RISK_FLAGS = {
    "CM",  # Cameroon
    "SL",  # Sierra Leone
    "KM",  # Comoros
    "PW",  # Palau
    "CK",  # Cook Islands
    "TZ",  # Tanzania
    "PA",  # Panama (some registrations)
    "MH",  # Marshall Islands (some registrations)
}

# Database schema version - increment when adding/changing tables
SCHEMA_VERSION = 1

# AIS gap threshold for dark event detection (hours)
AIS_DARK_THRESHOLD_HOURS = 6.0

# Port proximity detection radius (km)
PORT_PROXIMITY_RADIUS_KM = 30.0

# STS transfer detection proximity (km)
STS_PROXIMITY_KM = 10.0

# OFAC Russia-related sanctions programs
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
