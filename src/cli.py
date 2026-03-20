"""CLI entry point — ingest, lookup, score, and digest commands."""

import argparse
import logging
import sys
from pathlib import Path

from .db import Database
from .scoring import run_scoring

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sft")


def cmd_ingest(args):
    """Run all ingestion sources."""
    db = Database()
    force = args.force

    from .ingest.ofac import ingest_ofac
    from .ingest.opensanctions import ingest_opensanctions
    from .ingest.tankertrackers import ingest_tankertrackers

    total = 0

    if args.source in ("all", "ofac"):
        logger.info("=== Ingesting OFAC SDN ===")
        n = ingest_ofac(db, force_download=force)
        logger.info("OFAC: %d vessels ingested", n)
        total += n

    if args.source in ("all", "opensanctions"):
        logger.info("=== Ingesting OpenSanctions ===")
        n = ingest_opensanctions(db)
        logger.info("OpenSanctions: %d vessels ingested", n)
        total += n

    if args.source in ("all", "tankertrackers"):
        logger.info("=== Ingesting TankerTrackers ===")
        n = ingest_tankertrackers(db, force_download=force)
        logger.info("TankerTrackers: %d vessels ingested", n)
        total += n

    if args.source in ("all", "eu"):
        logger.info("=== Ingesting EU Sanctions ===")
        from .ingest.eu_sanctions import ingest_eu_sanctions
        n = ingest_eu_sanctions(db, force_download=force)
        logger.info("EU: %d vessels ingested", n)
        total += n

    logger.info("=== Total: %d vessels ingested ===", total)
    logger.info("Database: %d vessels, %d sanctions", db.vessel_count(), db.sanctions_count())


def cmd_score(args):
    """Run scoring on all vessels."""
    db = Database()
    alerts = run_scoring(db)
    logger.info("Generated %d alerts (score >= 60)", alerts)


def cmd_lookup(args):
    """Look up a vessel by IMO number."""
    db = Database()
    imo = args.imo

    vessel = db.get_vessel(imo)
    if vessel is None:
        logger.info("Vessel IMO %d not in local DB, querying OpenSanctions...", imo)
        from .ingest.opensanctions import lookup_by_imo
        results = lookup_by_imo(imo)
        if results:
            logger.info("Found %d result(s) on OpenSanctions:", len(results))
            for r in results:
                props = r.get("properties", {})
                name = props.get("name", ["?"])[0]
                flag = props.get("flag", ["?"])[0] if props.get("flag") else "?"
                print(f"  {name} | Flag: {flag}")
                print(f"  Source: {r.get('id', '?')}")
                programs = props.get("program", [])
                if programs:
                    print(f"  Programs: {', '.join(programs)}")
                print()
        else:
            print(f"No results found for IMO {imo}")
        return

    print(f"=== {vessel.name} (IMO {vessel.imo}) ===")
    print(f"Flag:       {vessel.flag or 'Unknown'}")
    print(f"Type:       {vessel.vessel_type or 'Unknown'}")
    print(f"Built:      {vessel.built_year or 'Unknown'}")
    print(f"Owner:      {vessel.owner or 'Unknown'}")
    print(f"DWT:        {vessel.dwt or 'Unknown'}")
    print(f"Risk Score: {vessel.risk_score}/100")
    print(f"Updated:    {vessel.last_updated or 'Never'}")

    sanctions = db.get_sanctions_for_vessel(imo)
    if sanctions:
        print(f"\n--- Sanctions ({len(sanctions)}) ---")
        for s in sanctions:
            print(f"  [{s.source.value.upper()}] {s.list_name}")
            if s.designation_date:
                print(f"    Designated: {s.designation_date}")

    positions = db.get_positions(imo, limit=5)
    if positions:
        print(f"\n--- Recent Positions ({len(positions)} shown) ---")
        for p in positions:
            print(f"  {p.timestamp} | {p.lat:.4f}, {p.lon:.4f} | {p.source or '?'}")

    vessel_alerts = db.get_alerts_for_vessel(imo, limit=5)
    if vessel_alerts:
        print(f"\n--- Alerts ({len(vessel_alerts)}) ---")
        for a in vessel_alerts:
            print(f"  Score {a.score}: {'; '.join(a.reasons)}")





def cmd_export(args):
    """Export vessel data to CSV."""
    import csv
    from datetime import datetime

    db = Database()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    vessels = db.get_all_vessels()

    fieldnames = [
        "imo", "name", "flag", "vessel_type", "built_year", "owner", "dwt",
        "risk_score", "is_sanctioned", "sanctions_sources",
        "last_position_lat", "last_position_lon", "last_seen",
        "near_port", "dark_events_count",
    ]

    from .ingest.ais import detect_dark_events, detect_port_calls

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for v in vessels:
            sanctions = db.get_sanctions_for_vessel(v.imo)
            is_sanctioned = len(sanctions) > 0
            sources = list(set(s.source.value for s in sanctions))

            positions = db.get_positions(v.imo, limit=500)
            lat, lon, last_seen = "", "", ""
            near_port = ""
            dark_count = 0

            if positions:
                p = positions[0]
                lat, lon = p.lat, p.lon
                last_seen = p.timestamp

                # Convert Position objects to dicts for detection functions
                pos_dicts = [
                    {"lat": pos.lat, "lon": pos.lon, "timestamp": pos.timestamp, "source": pos.source}
                    for pos in positions
                ]
                port_calls = detect_port_calls(pos_dicts)
                if port_calls:
                    near_port = port_calls[0]["port_name"]

                dark_events = detect_dark_events(pos_dicts)
                dark_count = len(dark_events)

            writer.writerow({
                "imo": v.imo,
                "name": v.name,
                "flag": v.flag or "",
                "vessel_type": v.vessel_type or "",
                "built_year": v.built_year or "",
                "owner": v.owner or "",
                "dwt": v.dwt or "",
                "risk_score": v.risk_score,
                "is_sanctioned": is_sanctioned,
                "sanctions_sources": "; ".join(sources),
                "last_position_lat": lat,
                "last_position_lon": lon,
                "last_seen": last_seen,
                "near_port": near_port,
                "dark_events_count": dark_count,
            })

    print(f"Exported {len(vessels)} vessels to {output}")


def cmd_track(args):
    """Fetch and display live AIS position for a vessel."""
    db = Database()
    imo = args.imo

    from .ingest.ais import ingest_positions, detect_dark_events, detect_port_calls

    vessel = db.get_vessel(imo)
    if vessel:
        print(f"=== TRACKING: {vessel.name} (IMO {vessel.imo}) ===")
    else:
        print(f"=== TRACKING: IMO {imo} ===")

    count = ingest_positions(db, imo)
    if count == 0:
        print("No positions found (MMSI unresolved or vessel not reporting AIS)")
        # Still show what we know
        if vessel:
            print(f"Flag:       {vessel.flag or 'Unknown'}")
            print(f"Risk Score: {vessel.risk_score}/100")
        return

    print(f"Positions:  {count} new stored")

    # Show latest position
    positions = db.get_positions(imo, limit=1)
    if positions:
        p = positions[0]
        print(f"Last seen:  {p.timestamp}")
        print(f"Position:   {p.lat:.4f}, {p.lon:.4f}")
        if p.speed is not None:
            print(f"Speed:      {p.speed:.1f} kn")
        if p.course is not None:
            print(f"Course:     {p.course:.0f}°")
        print(f"Source:     {p.source or 'unknown'}")

    # Check for port proximity
    all_positions = db.get_positions(imo, limit=500)
    port_calls = detect_port_calls(all_positions)
    if port_calls:
        for pc in port_calls:
            print(f"Near port:  {pc['port_name']} ({pc['distance_km']} km) ⚠️")

    # Check for dark events
    dark_events = detect_dark_events(all_positions)
    significant_gaps = [g for g in dark_events if g["duration_hours"] >= 6]
    if significant_gaps:
        print(f"Dark events: {len(significant_gaps)} gap(s) ≥ 6h detected")

    # Show risk score
    if vessel:
        print(f"Risk Score: {vessel.risk_score}/100")


def cmd_track_all(args):
    """Batch track top N vessels by risk score."""
    from .ingest.ais import ingest_all_positions

    db = Database()
    limit = args.limit

    logger.info("Batch tracking top %d vessels...", limit)
    total = ingest_all_positions(db, limit=limit)
    logger.info("Done: %d positions stored across all vessels", total)


def cmd_status(args):
    """Show database statistics."""
    db = Database()

    print("=== Shadow Fleet Tracker Status ===")
    print(f"Vessels tracked:    {db.vessel_count()}")
    print(f"Sanctions records:  {db.sanctions_count()}")

    alerts = db.get_alerts(min_score=60)
    print(f"Active alerts:      {len(alerts)}")

    # Last ingestion hint from vessel updates
    vessels = db.get_all_vessels()
    if vessels:
        latest = max(
            (v.last_updated for v in vessels if v.last_updated),
            default="Never",
        )
        print(f"Last updated:       {latest}")
    else:
        print("Last updated:       Never (run 'sft ingest' first)")


def cmd_serve(args):
    """Start the FastAPI server."""
    import uvicorn
    from .api.main import app
    uvicorn.run(app, host=args.host, port=args.port)



def _severity(score: int) -> str:
    if score >= 80:
        return "🔴"
    elif score >= 60:
        return "🟠"
    else:
        return "🟡"


def cmd_digest(args):
    """Generate a daily digest in Markdown."""
    from datetime import datetime
    from .config import get_config

    db = Database()
    cfg = get_config()
    today = datetime.now().strftime("%Y-%m-%d")

    output_dir = Path("data") / "digests"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"digest_{today}.md"

    max_vessels = cfg["output"]["digest_max_vessels"]
    max_alerts = cfg["output"]["digest_max_alerts"]

    lines = []
    lines.append("# Shadow Fleet Tracker — Daily Digest")
    lines.append(f"**Date:** {today}")
    lines.append(f"**Vessels tracked:** {db.vessel_count()}")
    lines.append(f"**Sanctions records:** {db.sanctions_count()}")

    # Stats
    from .scoring import _get_threshold
    all_alerts = db.get_alerts(min_score=_get_threshold())
    lines.append(f"**Active alerts:** {len(all_alerts)}")

    positions_count = 0
    with db.connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM positions").fetchone()
        positions_count = row[0]
    lines.append(f"**Position records:** {positions_count}")
    lines.append("")

    # Alerts section
    alerts = db.get_alerts(min_score=_get_threshold(), limit=max_alerts)
    if alerts:
        lines.append(f"## Active Alerts ({len(alerts)})")
        lines.append("")
        lines.append("| Severity | Vessel | IMO | Score | Reasons |")
        lines.append("|----------|--------|-----|-------|---------|")
        for a in alerts:
            vessel = db.get_vessel(a.imo)
            name = vessel.name if vessel else f"IMO {a.imo}"
            reasons = "; ".join(a.reasons) if a.reasons else "—"
            sev = _severity(a.score)

            # Add position info if available
            positions = db.get_positions(a.imo, limit=1)
            pos_str = ""
            if positions:
                p = positions[0]
                pos_str = f" [{p.lat:.2f},{p.lon:.2f}](https://www.google.com/maps?q={p.lat},{p.lon})"

            lines.append(f"| {sev} | {name}{pos_str} | {a.imo} | {a.score} | {reasons} |")
        lines.append("")

    # Sanctioned vessels
    sanctioned = db.get_sanctioned_vessels()
    if sanctioned:
        lines.append(f"## Sanctioned Vessels ({len(sanctioned)})")
        lines.append("")
        for v in sanctioned[:max_vessels]:
            sanctions = db.get_sanctions_for_vessel(v.imo)
            sources = list(set(s.source.value for s in sanctions))
            flag = v.flag or "—"

            pos_str = ""
            positions = db.get_positions(v.imo, limit=1)
            if positions:
                p = positions[0]
                pos_str = f" | Last seen: [{p.lat:.2f},{p.lon:.2f}]({p.timestamp})"

            lines.append(f"- **{v.name}** (IMO {v.imo}) | Flag: {flag} | Sources: {', '.join(sources)}{pos_str}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by Shadow Fleet Tracker — {datetime.now().isoformat()}*")

    content = "\n".join(lines)
    output_file.write_text(content)
    print(content)
    print(f"\nDigest saved to {output_file}")







def cmd_map(args):
    """Generate an interactive map of tracked vessels."""
    from .viz.map import build_map

    db = Database()
    output = Path(args.output) if args.output else None

    path = build_map(
        db,
        output=output,
        imo=args.imo,
        alerts_only=args.alerts_only,
        show_tracks=not args.no_tracks,
        show_ports=not args.no_ports,
        show_dark_zones=not args.no_dark,
    )
    print(f"Map saved to {path}")


def cmd_site(args):
    """Generate the full GitHub Pages dashboard."""
    from .viz.site import generate_site

    output = Path(args.output) if args.output else None
    path = generate_site(output_dir=output)
    print(f"Site generated in {path}")


def cmd_cleanup(args):
    """Clean up old position data."""
    db = Database()
    days = args.days
    deleted = db.cleanup_old_positions(days=days)
    print(f"Deleted {deleted} positions older than {days} days")


def main():
    parser = argparse.ArgumentParser(
        prog="sft",
        description="Shadow Fleet Tracker — monitor sanctioned Russian oil tankers",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress info output")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    p_ingest = subparsers.add_parser("ingest", help="Ingest sanctions data")
    p_ingest.add_argument(
        "--source", choices=["all", "ofac", "eu", "opensanctions", "tankertrackers"],
        default="all", help="Source to ingest (default: all)",
    )
    p_ingest.add_argument("--force", action="store_true", help="Force re-download")
    p_ingest.set_defaults(func=cmd_ingest)

    p_score = subparsers.add_parser("score", help="Run risk scoring")
    p_score.set_defaults(func=cmd_score)

    p_lookup = subparsers.add_parser("lookup", help="Look up vessel by IMO")
    p_lookup.add_argument("imo", type=int, help="IMO number")
    p_lookup.set_defaults(func=cmd_lookup)

    p_export = subparsers.add_parser("export", help="Export vessel data to CSV")
    p_export.add_argument("--output", default="data/export.csv", help="Output file path")
    p_export.set_defaults(func=cmd_export)

    p_track = subparsers.add_parser("track", help="Fetch live AIS position for a vessel")
    p_track.add_argument("imo", type=int, help="IMO number")
    p_track.set_defaults(func=cmd_track)

    p_track_all = subparsers.add_parser("track-all", help="Batch track top vessels by risk score")
    p_track_all.add_argument("--limit", type=int, default=20, help="Max vessels to track (default: 20)")
    p_track_all.set_defaults(func=cmd_track_all)

    p_digest = subparsers.add_parser("digest", help="Generate daily digest")
    p_digest.set_defaults(func=cmd_digest)

    p_serve = subparsers.add_parser("serve", help="Start the API server")
    p_serve.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    p_serve.set_defaults(func=cmd_serve)

    p_status = subparsers.add_parser("status", help="Show database statistics")
    p_status.set_defaults(func=cmd_status)

    p_site = subparsers.add_parser("site", help="Generate full GitHub Pages dashboard")
    p_site.add_argument("--output", help="Output directory (default: docs/)")
    p_site.set_defaults(func=cmd_site)

    p_map = subparsers.add_parser("map", help="Generate interactive vessel map")
    p_map.add_argument("--output", help="Output HTML path (default: data/map.html)")
    p_map.add_argument("--imo", type=int, help="Show single vessel with track")
    p_map.add_argument("--alerts-only", action="store_true", help="Only show alert vessels")
    p_map.add_argument("--no-tracks", action="store_true", help="Hide vessel tracks")
    p_map.add_argument("--no-ports", action="store_true", help="Hide Russian port zones")
    p_map.add_argument("--no-dark", action="store_true", help="Hide AIS dark zones")
    p_map.set_defaults(func=cmd_map)

    p_cleanup = subparsers.add_parser("cleanup", help="Delete old position data")
    p_cleanup.add_argument("--days", type=int, default=90, help="Delete positions older than N days (default: 90)")
    p_cleanup.set_defaults(func=cmd_cleanup)

    args = parser.parse_args()

    # Configure logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()