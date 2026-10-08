"""Subscriptions: config-driven plans, server-side status. Play billing lands in Phase 5."""
import os
from datetime import datetime

import yaml
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from config import get_settings
from database import get_db

router = APIRouter(prefix="/api/subscription", tags=["subscription"])
settings = get_settings()


def _pricing() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "pricing.yaml")
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def is_premium(user: models.User, db: Session) -> tuple[bool, models.SubscriptionRecord | None]:
    if settings.BETA_ALL_PREMIUM:
        return True, None
    rec = (
        db.query(models.SubscriptionRecord)
        .filter(models.SubscriptionRecord.user_id == user.id)
        .order_by(models.SubscriptionRecord.started_at.desc())
        .first()
    )
    if rec is None:
        return False, None
    active = rec.status == "active" and rec.plan == "premium" and (
        rec.ends_at is None or rec.ends_at > datetime.utcnow()
    )
    return active, rec


@router.get("/status", response_model=schemas.SubscriptionOut)
def status(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    pricing = _pricing()
    premium = pricing.get("plans", {}).get("premium", {})
    ok, rec = is_premium(user, db)
    plan = "premium" if ok else "free"
    return schemas.SubscriptionOut(
        plan=plan,
        status=(rec.status if rec else "active") if ok else "active",
        is_premium=ok,
        ends_at=rec.ends_at if rec else None,
        price_monthly_usd=premium.get("price_monthly"),
        price_annual_usd=premium.get("price_annual"),
    )


@router.get("/plans")
def plans():
    return _pricing().get("plans", {})
