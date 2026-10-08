"""Truck profiles + driver preferences."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from database import get_db

router = APIRouter(prefix="/api/trucks", tags=["trucks"])


def _truck_out(t: models.Truck) -> schemas.TruckOut:
    data = {c.name: getattr(t, c.name) for c in t.__table__.columns}
    data["fuel_range_miles"] = t.fuel_range_miles()
    return schemas.TruckOut(**data)


def _active_truck(db: Session, user_id: str) -> models.Truck | None:
    return (
        db.query(models.Truck)
        .filter(models.Truck.user_id == user_id, models.Truck.is_active.is_(True))
        .first()
    )


@router.get("", response_model=list[schemas.TruckOut])
def list_trucks(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trucks = db.query(models.Truck).filter(models.Truck.user_id == user.id).order_by(models.Truck.created_at).all()
    return [_truck_out(t) for t in trucks]


@router.post("", response_model=schemas.TruckOut, status_code=201)
def create_truck(
    body: schemas.TruckIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    truck = models.Truck(user_id=user.id, **body.model_dump())
    # First truck becomes the active one automatically.
    if _active_truck(db, user.id) is None:
        truck.is_active = True
    db.add(truck)
    db.commit()
    db.refresh(truck)
    return _truck_out(truck)


@router.get("/active", response_model=schemas.TruckOut | None)
def get_active_truck(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = _active_truck(db, user.id)
    return _truck_out(t) if t else None


@router.put("/active", response_model=schemas.TruckOut)
def set_active_truck(
    body: schemas.ActiveTruckIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    truck = (
        db.query(models.Truck)
        .filter(models.Truck.id == body.truck_id, models.Truck.user_id == user.id)
        .first()
    )
    if truck is None:
        raise HTTPException(status_code=404, detail="Truck not found")
    db.query(models.Truck).filter(models.Truck.user_id == user.id).update({"is_active": False})
    truck.is_active = True
    db.commit()
    db.refresh(truck)
    return _truck_out(truck)


@router.put("/{truck_id}", response_model=schemas.TruckOut)
def update_truck(
    truck_id: str,
    body: schemas.TruckIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    truck = (
        db.query(models.Truck)
        .filter(models.Truck.id == truck_id, models.Truck.user_id == user.id)
        .first()
    )
    if truck is None:
        raise HTTPException(status_code=404, detail="Truck not found")
    for k, v in body.model_dump().items():
        setattr(truck, k, v)
    db.commit()
    db.refresh(truck)
    return _truck_out(truck)


@router.delete("/{truck_id}")
def delete_truck(
    truck_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    truck = (
        db.query(models.Truck)
        .filter(models.Truck.id == truck_id, models.Truck.user_id == user.id)
        .first()
    )
    if truck is None:
        raise HTTPException(status_code=404, detail="Truck not found")
    was_active = truck.is_active
    db.delete(truck)
    if was_active:
        nxt = db.query(models.Truck).filter(models.Truck.user_id == user.id).first()
        if nxt:
            nxt.is_active = True
    db.commit()
    return {"ok": True}


# ---------- preferences ----------
pref_router = APIRouter(prefix="/api/preferences", tags=["preferences"])


@pref_router.get("", response_model=schemas.PreferencesOut)
def get_prefs(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    prefs = db.query(models.DriverPreference).filter(models.DriverPreference.user_id == user.id).first()
    if prefs is None:
        prefs = models.DriverPreference(user_id=user.id)
        db.add(prefs)
        db.commit()
        db.refresh(prefs)
    return prefs


@pref_router.put("", response_model=schemas.PreferencesOut)
def update_prefs(
    body: schemas.PreferencesIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prefs = db.query(models.DriverPreference).filter(models.DriverPreference.user_id == user.id).first()
    if prefs is None:
        prefs = models.DriverPreference(user_id=user.id)
        db.add(prefs)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(prefs, k, v)
    db.commit()
    db.refresh(prefs)
    return prefs
