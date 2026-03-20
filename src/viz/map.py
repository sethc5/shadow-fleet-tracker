"""Interactive map visualization using Folium (OpenStreetMap)."""

import logging
from pathlib import Path
from typing import Optional

import folium
from folium import plugins

from ..db import Database
from ..scoring import RUSSIAN_PORTS

logger = logging.getLogger(__name__)

# Risk score color mapping
def _score_color(score: int) -> str:
    if score >= 80:
        return "red"
    elif score >= 60:
        return "orange"
    elif score >= 40:
        return "beige"
    elif score >= 20:
        return "green"
    return "blue"


def _score_icon(score: int) -> str:
    if score >= 60:
        return "exclamation-sign"
    return "ship"


def build_map(
    db: Database,
    output: Path | None = None,
    imo: Optional[int] = None,
    alerts_only: bool = False,
    show_tracks: bool = True,
    show_ports: bool = True,
    show_dark_zones: bool = True,
) -> Path:
    """Build an interactive map of tracked vessels.

    Args:
        db: Database instance
        output: Output HTML path (default: data/map.html)
        imo: If set, show only this vessel with its track
        alerts_only: If True, only show vessels with score >= 60
        show_tracks: Show vessel position tracks as lines
        show_ports: Show Russian port zones
        show_dark_zones: Show AIS dark event zones

    Returns: Path to generated HTML file.
    """
    if output is None:
        output = Path("data") / "map.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    # Determine center point
    if imo:
        positions = db.get_positions(imo, limit=500)
        if positions:
            center_lat = sum(p.lat for p in positions) / len(positions)
            center_lon = sum(p.lon for p in positions) / len(positions)
        else:
            vessel = db.get_vessel(imo)
            center_lat, center_lon = 55.0, 20.0  # Baltic default
    else:
        center_lat, center_lon = 55.0, 20.0

    m = folium.Map(location=[center_lat, center_lon], zoom_start=5, tiles="OpenStreetMap")

    # Add Russian ports
    if show_ports:
        port_group = folium.FeatureGroup(name="Russian Oil Ports", show=True)
        for lat, lon, name in RUSSIAN_PORTS:
            folium.Circle(
                location=[lat, lon],
                radius=30000,  # 30km
                color="red",
                fill=True,
                fill_opacity=0.1,
                popup=folium.Popup(f"<b>{name}</b><br>Russian oil export port", max_width=200),
            ).add_to(port_group)
            folium.Marker(
                location=[lat, lon],
                icon=folium.Icon(color="red", icon="anchor", prefix="glyphicon"),
                popup=name,
            ).add_to(port_group)
        port_group.add_to(m)

    # Get vessels to plot
    if imo:
        vessels = [db.get_vessel(imo)] if db.get_vessel(imo) else []
    elif alerts_only:
        from ..scoring import _get_threshold
        alerts = db.get_alerts(min_score=_get_threshold())
        alert_imos = {a.imo for a in alerts}
        vessels = [db.get_vessel(i) for i in alert_imos if db.get_vessel(i)]
    else:
        vessels = db.get_all_vessels()

    # Vessel markers group
    marker_group = folium.FeatureGroup(name="Vessels", show=True)

    for vessel in vessels:
        positions = db.get_positions(vessel.imo, limit=100)
        sanctions = db.get_sanctions_for_vessel(vessel.imo)
        is_sanctioned = len(sanctions) > 0

        # Get latest position
        latest = positions[0] if positions else None
        if latest is None:
            continue

        color = _score_color(vessel.risk_score)
        icon = _score_icon(vessel.risk_score)

        # Build popup HTML
        popup_html = f"""
        <b>{vessel.name}</b><br>
        IMO: {vessel.imo}<br>
        Flag: {vessel.flag or 'Unknown'}<br>
        Risk Score: <b style='color:{color}'>{vessel.risk_score}/100</b><br>
        Sanctioned: {'Yes' if is_sanctioned else 'No'}<br>
        Last seen: {latest.timestamp}<br>
        Position: {latest.lat:.4f}, {latest.lon:.4f}
        """

        if latest.speed is not None:
            popup_html += f"<br>Speed: {latest.speed:.1f} kn"

        # Sanctioned vessels get a special marker
        if is_sanctioned:
            folium.Marker(
                location=[latest.lat, latest.lon],
                icon=folium.Icon(color="darkred", icon="warning-sign", prefix="glyphicon"),
                popup=folium.Popup(popup_html, max_width=300),
            ).add_to(marker_group)
        else:
            folium.CircleMarker(
                location=[latest.lat, latest.lon],
                radius=8,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                popup=folium.Popup(popup_html, max_width=300),
            ).add_to(marker_group)

        # Draw track
        if show_tracks and len(positions) > 1:
            track_coords = [[p.lat, p.lon] for p in reversed(positions)]
            folium.PolyLine(
                track_coords,
                color=color,
                weight=2,
                opacity=0.6,
                popup=f"{vessel.name} track",
            ).add_to(marker_group)

    marker_group.add_to(m)

    # Dark zones (areas where vessel went dark near Russia)
    if show_dark_zones:
        from ..ingest.ais import detect_dark_events
        dark_group = folium.FeatureGroup(name="AIS Dark Zones", show=True)

        for vessel in vessels[:50]:  # Limit to avoid clutter
            positions = db.get_positions(vessel.imo, limit=500)
            if len(positions) < 2:
                continue

            pos_dicts = [
                {"lat": p.lat, "lon": p.lon, "timestamp": p.timestamp, "source": p.source}
                for p in positions
            ]
            dark_events = detect_dark_events(pos_dicts)

            for de in dark_events:
                if de["near_russia"]:
                    folium.Circle(
                        location=[de["start_lat"], de["start_lon"]],
                        radius=50000,  # 50km
                        color="red",
                        fill=True,
                        fill_opacity=0.15,
                        popup=f"AIS dark: {de['duration_hours']}h near Russia",
                    ).add_to(dark_group)

        dark_group.add_to(m)

    # Add layer control
    folium.LayerControl().add_to(m)

    # Add fullscreen button
    plugins.Fullscreen().add_to(m)

    # Save
    m.save(str(output))
    logger.info("Map saved to %s", output)
    return output