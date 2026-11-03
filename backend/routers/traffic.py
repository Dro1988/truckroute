"""Live traffic along a route (TomTom) — a SEPARATE source from the
crowdsourced driver reports in routers/incidents.py. The two are never
blended: this endpoint's payload is labeled "Live traffic — TomTom" and
the driver-reports endpoint keeps its own label.

Dormant without a key: when TOMTOM_API_KEY is unset the endpoint returns
enabled=false with empty lists and the app shows no traffic UI at all.

Free-tier guardrails (2,500 TomTom calls/day):
  * the service only queries the route corridor and caches responses
    for a few minutes (repeat corridor = 0 calls), and
  * this router enforces a daily cap counted from the usage log; past
    the cap the endpoint degrades to cached/empty + an honest
    "traffic unavailable" message instead of spending more calls.
"""
from datetime import datetime, time as dtime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from config import get_settings
from database import get_db
from routers._limits import rate_limit
from services import factory
from services.usage import log_usage

router = APIRouter(prefix="/api/traffic", tags=["traffic"])
settings = get_settings()


def _calls_today(db: Session) -> int:
    start = datetime.combine(datetime.utcnow().date(), dtime.min)
    total = (
        db.query(func.coalesce(func.sum(models.UsageRecord.count), 0))
        .filter(models.UsageRecord.provider == "tomtom",
                models.UsageRecord.created_at >= start)
        .scalar()
    )
    return int(total or 0)


@router.post("/along", response_model=schemas.TrafficAlongOut)
def traffic_along_route(
    request: Request,
    body: schemas.AlongRouteIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rate_limit(request, "traffic", 30)
    cap = settings.TOMTOM_DAILY_CALL_CAP
    svc = factory.get_traffic_service()
    if svc is None:
        # No key on the server: clean disabled response, app hides the
        # feature entirely — exactly the pre-TomTom behavior.
        return schemas.TrafficAlongOut(
            enabled=False, available=False,
            message="Live traffic is not set up on this server.",
            calls_today=0, daily_cap=cap,
        )

    pts = [(float(p[1]), float(p[0])) for p in body.points if len(p) == 2]
    if len(pts) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 route points.")

    calls_today = _calls_today(db)
    budget = max(0, cap - calls_today)
    result = svc.along_route(pts, corridor_m=body.corridor_m, call_budget=budget)

    if result.calls_made:
        log_usage(db, "tomtom", "traffic_along", user.id, count=result.calls_made,
                  meta={"incidents": len(result.incidents),
                        "slowdowns": len(result.slowdowns)})
        calls_today += result.calls_made

    has_data = bool(result.incidents or result.slowdowns)
    if result.ok and not result.budget_exhausted:
        available, message = True, ""
        if result.partial:
            message = "Live traffic shown for parts of this route."
    elif result.ok and result.budget_exhausted:
        # Some data, but the day's allowance ran out mid-fetch.
        available = True
        message = "Some live traffic couldn't load — daily free limit reached."
    elif result.budget_exhausted and not has_data:
        available = False
        message = "Live traffic paused — daily free limit reached. Try again tomorrow."
    else:
        available = False
        message = "Live traffic unavailable right now."

    return schemas.TrafficAlongOut(
        enabled=True, available=available, message=message,
        incidents=[schemas.TrafficIncidentOut(**i) for i in result.incidents],
        slowdowns=[schemas.TrafficSlowdownOut(**s) for s in result.slowdowns],
        calls_today=calls_today, daily_cap=cap,
    )
