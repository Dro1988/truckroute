"""Trips: saved trip plans with stops."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from database import get_db

router = APIRouter(prefix="/api/trips", tags=["trips"])


def _trip_out(t: models.Trip) -> schemas.TripOut:
    out = schemas.TripOut.model_validate(t)
    out.stops = [
        schemas.TripStopOut(
            id=s.id, seq=s.seq, label=s.label, lat=s.lat, lng=s.lng,
            stop_type=s.stop_type, notes=s.notes,
        )
        for s in sorted(t.stops, key=lambda s: s.seq)
    ]
    return out


@router.get("", response_model=list[schemas.TripOut])
def list_trips(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trips = (
        db.query(models.Trip)
        .filter(models.Trip.user_id == user.id)
        .order_by(models.Trip.created_at.desc())
        .limit(100)
        .all()
    )
    return [_trip_out(t) for t in trips]


@router.post("", response_model=schemas.TripOut, status_code=201)
def create_trip(
    body: schemas.TripCreateIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trip = models.Trip(
        user_id=user.id,
        name=body.name or f"{body.origin.label or 'Start'} → {body.destination.label or 'End'}",
        origin_label=body.origin.label, origin_lat=body.origin.lat, origin_lng=body.origin.lng,
        dest_label=body.destination.label, dest_lat=body.destination.lat, dest_lng=body.destination.lng,
        truck_id=body.truck_id,
    )
    db.add(trip)
    db.flush()
    for seq, s in enumerate(body.stops):
        db.add(models.TripStop(trip_id=trip.id, seq=seq, label=s.label,
                               lat=s.lat, lng=s.lng, stop_type=s.stop_type, notes=s.notes))
    db.commit()
    db.refresh(trip)
    return _trip_out(trip)


@router.get("/{trip_id}", response_model=schemas.TripOut)
def get_trip(trip_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trip = db.query(models.Trip).filter(models.Trip.id == trip_id, models.Trip.user_id == user.id).first()
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return _trip_out(trip)


@router.delete("/{trip_id}")
def delete_trip(trip_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trip = db.query(models.Trip).filter(models.Trip.id == trip_id, models.Trip.user_id == user.id).first()
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    db.delete(trip)
    db.commit()
    return {"ok": True}
