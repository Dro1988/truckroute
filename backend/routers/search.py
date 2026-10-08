"""Destination search: autocomplete suggestions + truck-place search."""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from config import get_settings
from database import get_db
from routers._limits import rate_limit
from services import factory
from services.base import GeoPoint
from services.usage import log_usage

router = APIRouter(prefix="/api/search", tags=["search"])
settings = get_settings()


@router.get("/suggest", response_model=list[schemas.SuggestOut])
def suggest(
    request: Request,
    q: str = Query(min_length=2, max_length=200),
    lat: float | None = None,
    lng: float | None = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rate_limit(request, "suggest", settings.RATE_LIMIT_SEARCH)
    svc = factory.get_geocoding_service()
    near = GeoPoint(lat=lat, lng=lng) if lat is not None and lng is not None else None
    results = svc.suggest(q, near=near)
    log_usage(db, svc.name, "suggest", user.id, meta={"q": q[:80]})
    return [
        schemas.SuggestOut(
            label=r.label, address=r.address, lat=r.lat, lng=r.lng,
            category=r.category, provider=svc.name,
        )
        for r in results
    ]


@router.get("/places", response_model=list[schemas.SuggestOut])
def places(
    request: Request,
    q: str = Query(min_length=2, max_length=200),
    lat: float = Query(...),
    lng: float = Query(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Truck stops, fuel, parking, repair... near a point. HERE Discover when available."""
    rate_limit(request, "places", settings.RATE_LIMIT_SEARCH)
    svc = factory.get_geocoding_service()
    near = GeoPoint(lat=lat, lng=lng)
    if hasattr(svc, "discover"):
        results = svc.discover(q, near)  # type: ignore[attr-defined]
    else:
        results = svc.geocode(f"{q} near {lat},{lng}")
    log_usage(db, svc.name, "discover", user.id, meta={"q": q[:80]})
    return [
        schemas.SuggestOut(
            label=r.label, address=r.address, lat=r.lat, lng=r.lng,
            category=r.category, provider=svc.name,
        )
        for r in results
    ]
