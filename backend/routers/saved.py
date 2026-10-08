"""Saved locations: home, customers, favorite stops/parking."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from database import get_db

router = APIRouter(prefix="/api/saved-locations", tags=["saved"])


@router.get("", response_model=list[schemas.SavedLocationOut])
def list_saved(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(models.SavedLocation)
        .filter(models.SavedLocation.user_id == user.id)
        .order_by(models.SavedLocation.is_favorite.desc(), models.SavedLocation.label)
        .all()
    )


@router.post("", response_model=schemas.SavedLocationOut, status_code=201)
def create_saved(
    body: schemas.SavedLocationIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    loc = models.SavedLocation(user_id=user.id, **body.model_dump())
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


@router.put("/{loc_id}", response_model=schemas.SavedLocationOut)
def update_saved(
    loc_id: str,
    body: schemas.SavedLocationIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    loc = (
        db.query(models.SavedLocation)
        .filter(models.SavedLocation.id == loc_id, models.SavedLocation.user_id == user.id)
        .first()
    )
    if loc is None:
        raise HTTPException(status_code=404, detail="Location not found")
    for k, v in body.model_dump().items():
        setattr(loc, k, v)
    db.commit()
    db.refresh(loc)
    return loc


@router.delete("/{loc_id}")
def delete_saved(
    loc_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    loc = (
        db.query(models.SavedLocation)
        .filter(models.SavedLocation.id == loc_id, models.SavedLocation.user_id == user.id)
        .first()
    )
    if loc is None:
        raise HTTPException(status_code=404, detail="Location not found")
    db.delete(loc)
    db.commit()
    return {"ok": True}
