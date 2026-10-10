"""Crowdsourced incident reports (Waze-style), with truck-specific types.

One-tap reporting from the app, a nearby feed for the map, confirm/deny
voting so stale reports die, and automatic expiry. Anti-spam: max one
report per user per minute, plus the shared IP rate limiter.
"""
import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from config import get_settings
from database import get_db
from routers._limits import rate_limit
from services.usage import log_usage

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
settings = get_settings()

# kind -> display info, default time-to-live, and voice-alert radius
INCIDENT_TYPES: dict[str, dict] = {
    "police":        {"label": "Police", "emoji": "🚔", "ttl_min": 60,   "alert_m": 1600,
                      "voice": "Police reported ahead"},
    "accident":      {"label": "Accident", "emoji": "💥", "ttl_min": 60,   "alert_m": 1200,
                      "voice": "Accident reported ahead"},
    "hazard":        {"label": "Hazard", "emoji": "⚠️", "ttl_min": 45,    "alert_m": 800,
                      "voice": "Hazard reported on the road ahead"},
    "closure":       {"label": "Road closure", "emoji": "⛔", "ttl_min": 240,  "alert_m": 1600,
                      "voice": "Road closure reported ahead"},
    "construction":  {"label": "Construction", "emoji": "🚧", "ttl_min": 480,  "alert_m": 1200,
                      "voice": "Construction ahead"},
    "weigh_open":    {"label": "Weigh station open", "emoji": "🟢", "ttl_min": 120, "alert_m": 2500,
                      "voice": "Weigh station reported open ahead"},
    "weigh_closed":  {"label": "Weigh station closed", "emoji": "🔴", "ttl_min": 120, "alert_m": 2500,
                      "voice": "Weigh station reported closed ahead"},
    "low_clearance": {"label": "Low clearance", "emoji": "↕️", "ttl_min": 1440, "alert_m": 1600,
                      "voice": "Low clearance reported ahead"},
    "no_parking":    {"label": "No truck parking", "emoji": "🅿️", "ttl_min": 120, "alert_m": 1500,
                      "voice": "No truck parking reported ahead"},
    "traffic_jam":   {"label": "Traffic jam", "emoji": "🐢", "ttl_min": 30,   "alert_m": 1500,
                      "voice": "Traffic jam reported ahead"},
}

REPORT_COOLDOWN_S = 60      # one report per user per minute (Waze does the same)
DEDUPE_RADIUS_M = 250       # same kind this close + recent = confirm it instead
DEDUPE_WINDOW_MIN = 15
DENY_THRESHOLD = 3          # denies beyond this (and > confirms) retire a report


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _sweep_expired(db: Session) -> None:
    now = datetime.utcnow()
    db.query(models.IncidentReport).filter(
        models.IncidentReport.active.is_(True),
        models.IncidentReport.expires_at < now,
    ).update({models.IncidentReport.active: False}, synchronize_session=False)
    db.commit()


def _to_out(row: models.IncidentReport) -> schemas.IncidentOut:
    info = INCIDENT_TYPES.get(row.kind, {"label": row.kind, "emoji": "📍",
                                        "alert_m": 800, "voice": "Incident reported ahead"})
    now = datetime.utcnow()
    return schemas.IncidentOut(
        id=row.id, kind=row.kind, label=info["label"], emoji=info["emoji"],
        lat=row.lat, lng=row.lng, confirms=row.confirms, denies=row.denies,
        created_at=row.created_at, expires_at=row.expires_at,
        age_min=max(0, int((now - row.created_at).total_seconds() // 60)),
        alert_m=info["alert_m"], voice_text=info["voice"],
    )


@router.post("", response_model=schemas.IncidentOut)
def report_incident(
    request: Request,
    body: schemas.IncidentReportIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rate_limit(request, "incident", 10)
    if body.kind not in INCIDENT_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"Unknown incident type. Valid: {', '.join(INCIDENT_TYPES)}")

    now = datetime.utcnow()
    # One report per user per minute.
    last = (
        db.query(models.IncidentReport)
        .filter(models.IncidentReport.user_id == user.id)
        .order_by(models.IncidentReport.created_at.desc())
        .first()
    )
    if last and (now - last.created_at).total_seconds() < REPORT_COOLDOWN_S:
        raise HTTPException(status_code=429,
                            detail="One report per minute. Thanks for keeping it accurate.")

    _sweep_expired(db)

    # Dedupe: same kind very close and recent -> confirm the existing one.
    recent = (
        db.query(models.IncidentReport)
        .filter(
            models.IncidentReport.active.is_(True),
            models.IncidentReport.kind == body.kind,
            models.IncidentReport.created_at > now - timedelta(minutes=DEDUPE_WINDOW_MIN),
        )
        .all()
    )
    for row in recent:
        if _haversine_m(body.lat, body.lng, row.lat, row.lng) <= DEDUPE_RADIUS_M:
            row.confirms += 1
            # extend life a little when reconfirmed
            row.expires_at = max(row.expires_at,
                                 now + timedelta(minutes=INCIDENT_TYPES[body.kind]["ttl_min"] // 2))
            db.commit()
            log_usage(db, "community", "incident_confirm", user.id,
                      meta={"incident_id": row.id, "via": "dedupe"})
            return _to_out(row)

    info = INCIDENT_TYPES[body.kind]
    row = models.IncidentReport(
        user_id=user.id, kind=body.kind, lat=body.lat, lng=body.lng,
        note=body.note or "",
        expires_at=now + timedelta(minutes=info["ttl_min"]),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log_usage(db, "community", "incident_report", user.id,
              meta={"incident_id": row.id, "kind": body.kind})
    return _to_out(row)


@router.get("/near", response_model=list[schemas.IncidentOut])
def incidents_near(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_m: float = Query(8000, gt=0, le=20000),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rate_limit(request, "incident", 30)
    _sweep_expired(db)

    # Rough bounding box first (keeps the table scan cheap), then haversine.
    d_lat = radius_m / 111320.0
    d_lng = radius_m / (111320.0 * max(0.2, math.cos(math.radians(lat))))
    candidates = (
        db.query(models.IncidentReport)
        .filter(
            models.IncidentReport.active.is_(True),
            models.IncidentReport.lat.between(lat - d_lat, lat + d_lat),
            models.IncidentReport.lng.between(lng - d_lng, lng + d_lng),
        )
        .order_by(models.IncidentReport.created_at.desc())
        .limit(200)
        .all()
    )
    out = [_to_out(r) for r in candidates
           if _haversine_m(lat, lng, r.lat, r.lng) <= radius_m]
    log_usage(db, "community", "incident_query", user.id,
              meta={"count": len(out), "radius_m": radius_m})
    return out[:100]


@router.post("/{incident_id}/confirm", response_model=schemas.IncidentOut)
@router.post("/{incident_id}/deny", response_model=schemas.IncidentOut)
def vote_incident(
    request: Request,
    incident_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rate_limit(request, "incident", 20)
    vote = "deny" if request.url.path.endswith("/deny") else "confirm"
    row = (
        db.query(models.IncidentReport)
        .filter(models.IncidentReport.id == incident_id,
                models.IncidentReport.active.is_(True))
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found or expired.")
    if row.user_id == user.id:
        raise HTTPException(status_code=400, detail="You can't vote on your own report.")
    existing = (
        db.query(models.IncidentVote)
        .filter(models.IncidentVote.incident_id == incident_id,
                models.IncidentVote.user_id == user.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="You already voted on this report.")

    db.add(models.IncidentVote(incident_id=incident_id, user_id=user.id, vote=vote))
    if vote == "confirm":
        row.confirms += 1
    else:
        row.denies += 1
        if row.denies >= DENY_THRESHOLD and row.denies > row.confirms:
            row.active = False  # community retired it
    db.commit()
    db.refresh(row)
    log_usage(db, "community", f"incident_{vote}", user.id,
              meta={"incident_id": incident_id})
    return _to_out(row)
