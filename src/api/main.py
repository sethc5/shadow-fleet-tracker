"""FastAPI application — query API for vessel status, positions, alerts, and export."""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..db import Database
from .auth import APIKeyAuthMiddleware, RateLimitMiddleware

app = FastAPI(
    title="Shadow Fleet Tracker API",
    description="Query vessel sanctions status, risk scores, alerts, and positions. "
    "Monitor sanctioned Russian oil tankers with real-time AIS tracking.",
    version="0.4.0",
    tags_info=[
        {"name": "vessels", "description": "Vessel lookups and position history"},
        {"name": "alerts", "description": "Active risk alerts"},
        {"name": "export", "description": "Data export"},
    ],
)

# Middleware
app.add_middleware(RateLimitMiddleware)
app.add_middleware(APIKeyAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

db = Database()


# --- Response models ---

class VesselResponse(BaseModel):
    imo: int
    name: str
    mmsi: int | None = None
    flag: str | None = None
    vessel_type: str | None = None
    built_year: int | None = None
    owner: str | None = None
    dwt: int | None = None
    risk_score: int = 0
    is_sanctioned: bool = False
    sanctions: list[dict] = []


class PositionResponse(BaseModel):
    lat: float
    lon: float
    timestamp: str
    speed: float | None = None
    course: float | None = None
    source: str | None = None


class VesselPositionsResponse(BaseModel):
    imo: int
    positions: list[PositionResponse] = []
    dark_events: list[dict] = []
    port_calls: list[dict] = []


class AlertResponse(BaseModel):
    imo: int
    vessel_name: str | None = None
    score: int
    reasons: list[str] = []
    created_at: str | None = None


class FleetSummary(BaseModel):
    total_vessels: int
    total_sanctions: int
    total_alerts: int
    total_positions: int
    sources: dict[str, int] = {}


# --- Endpoints ---

@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "vessels": db.vessel_count(), "sanctions": db.sanctions_count()}


@app.get("/vessel/{imo}", response_model=VesselResponse, tags=["vessels"])
async def get_vessel(imo: int):
    """Get vessel details including sanctions and risk score."""
    vessel = db.get_vessel(imo)
    if vessel is None:
        raise HTTPException(status_code=404, detail=f"Vessel IMO {imo} not found")

    sanctions = db.get_sanctions_for_vessel(imo)
    return VesselResponse(
        imo=vessel.imo,
        name=vessel.name,
        mmsi=vessel.mmsi,
        flag=vessel.flag,
        vessel_type=vessel.vessel_type,
        built_year=vessel.built_year,
        owner=vessel.owner,
        dwt=vessel.dwt,
        risk_score=vessel.risk_score,
        is_sanctioned=len(sanctions) > 0,
        sanctions=[
            {"source": s.source.value, "list_name": s.list_name, "designation_date": s.designation_date}
            for s in sanctions
        ],
    )


@app.get("/vessel/{imo}/positions", response_model=VesselPositionsResponse, tags=["vessels"])
async def get_vessel_positions(imo: int, limit: int = Query(100, le=500)):
    """Get recent position history, dark events, and port calls for a vessel."""
    from ..ingest.ais import detect_dark_events, detect_port_calls

    vessel = db.get_vessel(imo)
    if vessel is None:
        raise HTTPException(status_code=404, detail=f"Vessel IMO {imo} not found")

    positions = db.get_positions(imo, limit=limit)
    pos_responses = [
        PositionResponse(lat=p.lat, lon=p.lon, timestamp=p.timestamp, speed=p.speed, course=p.course, source=p.source)
        for p in positions
    ]

    # Detect evasion behaviors
    pos_dicts = [{"lat": p.lat, "lon": p.lon, "timestamp": p.timestamp, "source": p.source} for p in positions]
    dark_events = detect_dark_events(pos_dicts)
    port_calls = detect_port_calls(pos_dicts)

    return VesselPositionsResponse(
        imo=imo,
        positions=pos_responses,
        dark_events=dark_events,
        port_calls=port_calls,
    )


@app.get("/sanctioned", response_model=list[VesselResponse], tags=["vessels"])
async def list_sanctioned(limit: int = Query(100, le=500)):
    """List all sanctioned vessels."""
    vessels = db.get_sanctioned_vessels()[:limit]
    results = []
    for v in vessels:
        sanctions = db.get_sanctions_for_vessel(v.imo)
        results.append(
            VesselResponse(
                imo=v.imo, name=v.name, mmsi=v.mmsi, flag=v.flag,
                vessel_type=v.vessel_type, built_year=v.built_year,
                owner=v.owner, dwt=v.dwt, risk_score=v.risk_score,
                is_sanctioned=True,
                sanctions=[{"source": s.source.value, "list_name": s.list_name} for s in sanctions],
            )
        )
    return results


@app.get("/alerts/today", response_model=list[AlertResponse], tags=["alerts"])
async def today_alerts(
    min_score: int = Query(60, ge=0, le=100),
    limit: int = Query(50, le=200),
):
    """Get active alerts above a minimum risk score."""
    alerts = db.get_alerts(min_score=min_score, limit=limit)
    results = []
    for a in alerts:
        vessel = db.get_vessel(a.imo)
        results.append(
            AlertResponse(
                imo=a.imo,
                vessel_name=vessel.name if vessel else None,
                score=a.score,
                reasons=a.reasons,
                created_at=a.created_at,
            )
        )
    return results


@app.get("/fleet/summary", response_model=FleetSummary, tags=["vessels"])
async def fleet_summary():
    """Get aggregate statistics about the tracked fleet."""
    all_vessels = db.get_all_vessels()
    alerts = db.get_alerts(min_score=60)

    sources = {}
    for v in all_vessels:
        sanctions = db.get_sanctions_for_vessel(v.imo)
        for s in sanctions:
            src = s.source.value
            sources[src] = sources.get(src, 0) + 1

    with db.connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM positions").fetchone()
        total_positions = row[0]

    return FleetSummary(
        total_vessels=len(all_vessels),
        total_sanctions=db.sanctions_count(),
        total_alerts=len(alerts),
        total_positions=total_positions,
        sources=sources,
    )


@app.get("/export/csv", tags=["export"])
async def export_csv():
    """Export all vessel data as CSV download."""
    import csv
    import io
    from fastapi.responses import StreamingResponse
    from ..ingest.ais import detect_dark_events, detect_port_calls

    vessels = db.get_all_vessels()
    fieldnames = [
        "imo", "name", "flag", "vessel_type", "built_year", "owner", "dwt",
        "risk_score", "is_sanctioned", "sanctions_sources",
        "last_position_lat", "last_position_lon", "last_seen",
        "near_port", "dark_events_count",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for v in vessels:
        sanctions = db.get_sanctions_for_vessel(v.imo)
        is_sanctioned = len(sanctions) > 0
        sources = list(set(s.source.value for s in sanctions))

        positions = db.get_positions(v.imo, limit=500)
        lat, lon, last_seen, near_port, dark_count = "", "", "", "", 0

        if positions:
            p = positions[0]
            lat, lon, last_seen = p.lat, p.lon, p.timestamp
            pos_dicts = [{"lat": pos.lat, "lon": pos.lon, "timestamp": pos.timestamp, "source": pos.source} for pos in positions]
            port_calls = detect_port_calls(pos_dicts)
            if port_calls:
                near_port = port_calls[0]["port_name"]
            dark_count = len(detect_dark_events(pos_dicts))

        writer.writerow({
            "imo": v.imo, "name": v.name, "flag": v.flag or "",
            "vessel_type": v.vessel_type or "", "built_year": v.built_year or "",
            "owner": v.owner or "", "dwt": v.dwt or "",
            "risk_score": v.risk_score, "is_sanctioned": is_sanctioned,
            "sanctions_sources": "; ".join(sources),
            "last_position_lat": lat, "last_position_lon": lon,
            "last_seen": last_seen, "near_port": near_port,
            "dark_events_count": dark_count,
        })

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=shadow_fleet_export.csv"},
    )