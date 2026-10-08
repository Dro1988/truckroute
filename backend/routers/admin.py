"""Admin dashboard: users, usage/cost, system health. Admin role required."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_admin_user
from database import get_db
from services import factory

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/health")
def health(admin=Depends(get_admin_user)):
    return {
        "ok": True,
        "routing_provider": factory.routing_provider_name(),
        "geocode_provider": factory.geocode_provider_name(),
        "here_configured": factory.here_configured(),
        "time": datetime.utcnow().isoformat(),
    }


@router.get("/users")
def users(limit: int = 100, admin=Depends(get_admin_user), db: Session = Depends(get_db)):
    rows = db.query(models.User).order_by(models.User.created_at.desc()).limit(limit).all()
    return [
        {
            "id": u.id, "email": u.email, "display_name": u.display_name,
            "is_admin": u.is_admin, "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "trucks": db.query(models.Truck).filter(models.Truck.user_id == u.id).count(),
            "trips": db.query(models.Trip).filter(models.Trip.user_id == u.id).count(),
        }
        for u in rows
    ]


@router.get("/usage", response_model=schemas.UsageSummaryOut)
def usage(
    days: int = 30,
    admin=Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    since = datetime.utcnow() - timedelta(days=days)
    q = db.query(models.UsageRecord).filter(models.UsageRecord.created_at >= since)
    total_calls = q.with_entities(func.coalesce(func.sum(models.UsageRecord.count), 0)).scalar() or 0
    total_cost = q.with_entities(func.coalesce(func.sum(models.UsageRecord.est_cost_usd), 0.0)).scalar() or 0.0
    active_users = q.with_entities(func.count(func.distinct(models.UsageRecord.user_id))).scalar() or 0
    by_provider = [
        {"provider": p, "calls": c, "cost_usd": round(cost, 4)}
        for p, c, cost in q.with_entities(
            models.UsageRecord.provider,
            func.sum(models.UsageRecord.count),
            func.sum(models.UsageRecord.est_cost_usd),
        ).group_by(models.UsageRecord.provider).all()
    ]
    by_operation = [
        {"operation": o, "calls": c, "cost_usd": round(cost, 4)}
        for o, c, cost in q.with_entities(
            models.UsageRecord.operation,
            func.sum(models.UsageRecord.count),
            func.sum(models.UsageRecord.est_cost_usd),
        ).group_by(models.UsageRecord.operation).all()
    ]
    return schemas.UsageSummaryOut(
        period_days=days,
        total_calls=int(total_calls),
        total_cost_usd=round(float(total_cost), 4),
        active_users=int(active_users),
        by_provider=by_provider,
        by_operation=by_operation,
        avg_calls_per_user=round(total_calls / active_users, 1) if active_users else 0.0,
    )


@router.get("/errors")
def errors(limit: int = 50, admin=Depends(get_admin_user), db: Session = Depends(get_db)):
    rows = (
        db.query(models.SystemError)
        .order_by(models.SystemError.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {"id": e.id, "where": e.where, "message": e.message,
         "created_at": e.created_at.isoformat() if e.created_at else None}
        for e in rows
    ]


@router.get("/subscribers")
def subscribers(admin=Depends(get_admin_user), db: Session = Depends(get_db)):
    rows = (
        db.query(models.SubscriptionRecord)
        .order_by(models.SubscriptionRecord.started_at.desc())
        .limit(200)
        .all()
    )
    return [
        {"id": r.id, "user_id": r.user_id, "plan": r.plan, "status": r.status,
         "provider": r.provider,
         "started_at": r.started_at.isoformat() if r.started_at else None,
         "ends_at": r.ends_at.isoformat() if r.ends_at else None}
        for r in rows
    ]
