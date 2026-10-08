"""Navigation sessions: start/stop + events (recalculations, arrivals)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from database import get_db

router = APIRouter(prefix="/api/nav", tags=["navigation"])


@router.post("/start", response_model=schemas.NavSessionOut, status_code=201)
def start_session(
    body: schemas.NavStartIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.trip_id:
        trip = db.query(models.Trip).filter(
            models.Trip.id == body.trip_id, models.Trip.user_id == user.id).first()
        if trip is None:
            raise HTTPException(status_code=404, detail="Trip not found")
    sess = models.NavigationSession(user_id=user.id, trip_id=body.trip_id, route_id=body.route_id)
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


@router.post("/{session_id}/event")
def log_event(
    session_id: str,
    body: schemas.NavEventIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sess = db.query(models.NavigationSession).filter(
        models.NavigationSession.id == session_id,
        models.NavigationSession.user_id == user.id).first()
    if sess is None:
        raise HTTPException(status_code=404, detail="Session not found")
    events = list(sess.events or [])
    events.append({"event": body.event, "at": datetime.utcnow().isoformat()})
    sess.events = events
    if body.event == "recalculated":
        sess.recalculations = (sess.recalculations or 0) + 1
    if body.distance_m is not None:
        sess.distance_m = body.distance_m
    db.commit()
    return {"ok": True, "recalculations": sess.recalculations}


@router.post("/{session_id}/end", response_model=schemas.NavSessionOut)
def end_session(
    session_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sess = db.query(models.NavigationSession).filter(
        models.NavigationSession.id == session_id,
        models.NavigationSession.user_id == user.id).first()
    if sess is None:
        raise HTTPException(status_code=404, detail="Session not found")
    sess.ended_at = datetime.utcnow()
    db.commit()
    db.refresh(sess)
    return sess


@router.get("/recent", response_model=list[schemas.NavSessionOut])
def recent_sessions(
    user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return (
        db.query(models.NavigationSession)
        .filter(models.NavigationSession.user_id == user.id)
        .order_by(models.NavigationSession.started_at.desc())
        .limit(20)
        .all()
    )
