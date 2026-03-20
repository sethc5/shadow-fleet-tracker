"""Static site generator for GitHub Pages dashboard."""

import logging
from datetime import datetime
from pathlib import Path

from ..config import get_config
from ..db import Database
from ..scoring import _get_threshold
from .map import build_map

logger = logging.getLogger(__name__)


def generate_site(output_dir: Path | None = None) -> Path:
    """Generate a complete static site in the output directory.

    Creates:
    - index.html — main dashboard with embedded map, alerts, stats
    - map.html — standalone interactive map
    - data.json — machine-readable fleet data

    Returns: Path to output directory.
    """
    if output_dir is None:
        output_dir = Path("docs")
    output_dir.mkdir(parents=True, exist_ok=True)

    db = Database()
    cfg = get_config()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()

    # Generate standalone map
    map_path = output_dir / "map.html"
    build_map(db, output=map_path)
    logger.info("Map generated: %s", map_path)

    # Generate JSON data endpoint
    _generate_json(db, output_dir / "data.json", today)

    # Generate main index.html
    _generate_index(db, output_dir / "index.html", today, now)

    # Generate archive page
    _generate_archive(db, output_dir / "archive.html", today)

    logger.info("Site generated in %s", output_dir)
    return output_dir


def _generate_json(db: Database, output: Path, today: str):
    """Generate machine-readable fleet data as JSON."""
    import json

    vessels = db.get_all_vessels()
    threshold = _get_threshold()

    data = {
        "generated": today,
        "total_vessels": len(vessels),
        "total_sanctions": db.sanctions_count(),
        "vessels": [],
    }

    for v in vessels:
        sanctions = db.get_sanctions_for_vessel(v.imo)
        positions = db.get_positions(v.imo, limit=1)
        latest = positions[0] if positions else None

        data["vessels"].append({
            "imo": v.imo,
            "name": v.name,
            "flag": v.flag,
            "vessel_type": v.vessel_type,
            "built_year": v.built_year,
            "owner": v.owner,
            "dwt": v.dwt,
            "risk_score": v.risk_score,
            "is_sanctioned": len(sanctions) > 0,
            "sanctions_sources": list(set(s.source.value for s in sanctions)),
            "last_position": {
                "lat": latest.lat,
                "lon": latest.lon,
                "timestamp": latest.timestamp,
                "speed": latest.speed,
            } if latest else None,
        })

    output.write_text(json.dumps(data, indent=2))


def _generate_index(db: Database, output: Path, today: str, now: str):
    """Generate the main dashboard HTML page."""
    threshold = _get_threshold()
    alerts = db.get_alerts(min_score=threshold)
    sanctioned = db.get_sanctioned_vessels()
    all_vessels = db.get_all_vessels()

    with db.connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM positions").fetchone()
        pos_count = row[0]

    # Build alerts table rows
    alert_rows = ""
    for a in alerts[:20]:
        vessel = db.get_vessel(a.imo)
        name = vessel.name if vessel else f"IMO {a.imo}"
        sev = "🔴" if a.score >= 80 else "🟠"
        reasons = "; ".join(a.reasons[:2]) if a.reasons else "—"
        alert_rows += f"""
        <tr>
            <td>{sev}</td>
            <td><a href="map.html">{name}</a></td>
            <td>{a.imo}</td>
            <td><strong>{a.score}</strong></td>
            <td class="reasons">{reasons}</td>
        </tr>"""

    # Build sanctioned vessel rows
    sanction_rows = ""
    for v in sanctioned[:15]:
        sanctions = db.get_sanctions_for_vessel(v.imo)
        sources = ", ".join(set(s.source.value for s in sanctions[:3]))
        flag = v.flag or "—"
        sanction_rows += f"""
        <tr>
            <td><strong>{v.name}</strong></td>
            <td>{v.imo}</td>
            <td>{flag}</td>
            <td>{v.risk_score}</td>
            <td>{sources}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Shadow Fleet Tracker</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0a; color: #e0e0e0; }}
        .header {{ background: #1a1a2e; padding: 2rem; border-bottom: 2px solid #e94560; }}
        .header h1 {{ font-size: 2rem; color: #e94560; }}
        .header p {{ color: #888; margin-top: 0.5rem; }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 2rem; }}
        .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
        .stat {{ background: #1a1a2e; padding: 1.5rem; border-radius: 8px; text-align: center; }}
        .stat .number {{ font-size: 2.5rem; font-weight: bold; color: #e94560; }}
        .stat .label {{ color: #888; margin-top: 0.5rem; }}
        .section {{ margin-bottom: 2rem; }}
        .section h2 {{ color: #e94560; margin-bottom: 1rem; font-size: 1.3rem; }}
        table {{ width: 100%; border-collapse: collapse; background: #1a1a2e; border-radius: 8px; overflow: hidden; }}
        th {{ background: #16213e; padding: 0.75rem; text-align: left; color: #e94560; font-size: 0.85rem; text-transform: uppercase; }}
        td {{ padding: 0.75rem; border-top: 1px solid #2a2a3e; }}
        tr:hover {{ background: #16213e; }}
        .reasons {{ font-size: 0.85rem; color: #888; max-width: 300px; }}
        .map-container {{ background: #1a1a2e; border-radius: 8px; overflow: hidden; height: 500px; }}
        .map-container iframe {{ width: 100%; height: 100%; border: none; }}
        .links {{ display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap; }}
        .links a {{ background: #e94560; color: white; padding: 0.5rem 1rem; border-radius: 4px; text-decoration: none; font-size: 0.9rem; }}
        .links a:hover {{ background: #c73e54; }}
        .links a.secondary {{ background: #16213e; }}
        .footer {{ text-align: center; padding: 2rem; color: #555; font-size: 0.85rem; }}
        .footer a {{ color: #e94560; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>🚢 Shadow Fleet Tracker</h1>
            <p>Open-source monitoring of sanctioned Russian oil tankers — {today}</p>
        </div>
    </div>

    <div class="container">
        <div class="links">
            <a href="map.html">🗺️ Interactive Map</a>
            <a href="data.json" class="secondary">📊 JSON Data</a>
            <a href="archive.html" class="secondary">📁 Archive</a>
            <a href="https://github.com" class="secondary">⭐ GitHub</a>
        </div>

        <div class="stats">
            <div class="stat">
                <div class="number">{len(all_vessels)}</div>
                <div class="label">Vessels Tracked</div>
            </div>
            <div class="stat">
                <div class="number">{len(sanctioned)}</div>
                <div class="label">Sanctioned</div>
            </div>
            <div class="stat">
                <div class="number">{len(alerts)}</div>
                <div class="label">Active Alerts</div>
            </div>
            <div class="stat">
                <div class="number">{pos_count:,}</div>
                <div class="label">Position Records</div>
            </div>
        </div>

        <div class="section">
            <h2>🗺️ Fleet Map</h2>
            <div class="map-container">
                <iframe src="map.html"></iframe>
            </div>
        </div>

        <div class="section">
            <h2>⚠️ Active Alerts (score ≥ {threshold})</h2>
            <table>
                <thead>
                    <tr><th></th><th>Vessel</th><th>IMO</th><th>Score</th><th>Reasons</th></tr>
                </thead>
                <tbody>
                    {alert_rows if alert_rows else '<tr><td colspan="5" style="text-align:center;color:#555;">No alerts — run <code>sft score</code> to generate</td></tr>'}
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2>🚫 Sanctioned Vessels</h2>
            <table>
                <thead>
                    <tr><th>Vessel</th><th>IMO</th><th>Flag</th><th>Score</th><th>Sources</th></tr>
                </thead>
                <tbody>
                    {sanction_rows if sanction_rows else '<tr><td colspan="5" style="text-align:center;color:#555;">No vessels — run <code>sft ingest</code> to populate</td></tr>'}
                </tbody>
            </table>
        </div>
    </div>

    <div class="footer">
        Generated by <a href="https://github.com">Shadow Fleet Tracker</a> — {now}<br>
        Data: OFAC, EU, OpenSanctions, TankerTrackers | Map: OpenStreetMap
    </div>
</body>
</html>"""

    output.write_text(html)


def _generate_archive(db: Database, output: Path, today: str):
    """Generate archive page listing past digests."""
    digest_dir = Path("data") / "digests"
    digests = sorted(digest_dir.glob("digest_*.md"), reverse=True) if digest_dir.exists() else []

    digest_list = ""
    for d in digests[:30]:
        date = d.stem.replace("digest_", "")
        digest_list += f'<li><a href="archive/{d.name}">{date}</a></li>\n'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Archive — Shadow Fleet Tracker</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0a; color: #e0e0e0; padding: 2rem; }}
        h1 {{ color: #e94560; margin-bottom: 1rem; }}
        a {{ color: #e94560; }}
        ul {{ list-style: none; }}
        li {{ padding: 0.5rem 0; border-bottom: 1px solid #2a2a3e; }}
        .back {{ margin-bottom: 1rem; display: inline-block; }}
    </style>
</head>
<body>
    <a href="index.html" class="back">← Back to Dashboard</a>
    <h1>📁 Digest Archive</h1>
    <ul>
        {digest_list if digest_list else '<li style="color:#555;">No digests yet — run <code>sft digest</code></li>'}
    </ul>
</body>
</html>"""

    output.write_text(html)