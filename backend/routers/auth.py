"""Auth routes: register, login, refresh, profile, password, export, delete."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import auth as auth_lib
import models
import schemas
from auth import get_current_user
from config import get_settings
from database import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _is_admin_email(email: str) -> bool:
    admins = [e.strip().lower() for e in get_settings().ADMIN_EMAILS.split(",") if e.strip()]
    return email.lower() in admins


@router.post("/register", response_model=schemas.TokenOut, status_code=201)
def register(body: schemas.RegisterIn, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=400, detail="An account with this email already exists")
    user = models.User(
        email=email,
        password_hash=auth_lib.hash_password(body.password),
        display_name=body.display_name.strip(),
        is_admin=_is_admin_email(email),
    )
    db.add(user)
    db.flush()
    db.add(models.DriverPreference(user_id=user.id))
    db.commit()
    return schemas.TokenOut(
        access_token=auth_lib.create_access_token(user.id),
        refresh_token=auth_lib.create_refresh_token(user.id),
    )


@router.post("/login", response_model=schemas.TokenOut)
def login(body: schemas.LoginIn, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None or not auth_lib.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    user.last_login_at = datetime.utcnow()
    db.commit()
    return schemas.TokenOut(
        access_token=auth_lib.create_access_token(user.id),
        refresh_token=auth_lib.create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=schemas.TokenOut)
def refresh(body: schemas.RefreshIn, db: Session = Depends(get_db)):
    user_id = auth_lib.decode_token(body.refresh_token, "refresh")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return schemas.TokenOut(
        access_token=auth_lib.create_access_token(user.id),
        refresh_token=auth_lib.create_refresh_token(user.id),
    )


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user


@router.put("/me", response_model=schemas.UserOut)
def update_profile(
    body: schemas.ProfileUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.email is not None:
        email = body.email.lower().strip()
        clash = db.query(models.User).filter(
            models.User.email == email, models.User.id != user.id
        ).first()
        if clash:
            raise HTTPException(status_code=400, detail="Email already in use")
        user.email = email
        user.is_admin = _is_admin_email(email)
    db.commit()
    db.refresh(user)
    return user


@router.post("/change-password")
def change_password(
    body: schemas.ChangePasswordIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not auth_lib.verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = auth_lib.hash_password(body.new_password)
    db.commit()
    return {"ok": True}


@router.get("/export")
def export_data(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download everything the account stores (GDPR-style data export)."""
    trucks = db.query(models.Truck).filter(models.Truck.user_id == user.id).all()
    trips = db.query(models.Trip).filter(models.Trip.user_id == user.id).all()
    saved = db.query(models.SavedLocation).filter(models.SavedLocation.user_id == user.id).all()
    prefs = db.query(models.DriverPreference).filter(models.DriverPreference.user_id == user.id).first()
    return {
        "user": {"email": user.email, "display_name": user.display_name,
                 "created_at": str(user.created_at)},
        "preferences": {c.name: getattr(prefs, c.name) for c in prefs.__table__.columns} if prefs else {},
        "trucks": [{c.name: getattr(t, c.name) for c in t.__table__.columns} for t in trucks],
        "trips": [
            {**{c.name: getattr(tr, c.name) for c in tr.__table__.columns},
             "stops": [{c.name: getattr(s, c.name) for c in s.__table__.columns} for s in tr.stops]}
            for tr in trips
        ],
        "saved_locations": [{c.name: getattr(s, c.name) for c in s.__table__.columns} for s in saved],
    }


@router.delete("/me")
def delete_account(
    user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Delete the account and all of its data."""
    for t in db.query(models.Trip).filter(models.Trip.user_id == user.id).all():
        db.delete(t)
    db.query(models.SavedLocation).filter(models.SavedLocation.user_id == user.id).delete()
    db.query(models.SubscriptionRecord).filter(models.SubscriptionRecord.user_id == user.id).delete()
    db.query(models.Payment).filter(models.Payment.user_id == user.id).delete()
    db.query(models.NavigationSession).filter(models.NavigationSession.user_id == user.id).delete()
    db.query(models.UsageRecord).filter(models.UsageRecord.user_id == user.id).delete()
    db.delete(user)
    db.commit()
    return {"ok": True, "message": "Account deleted"}
