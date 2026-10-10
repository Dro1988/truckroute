"""Route calculation: provider-abstracted truck routing + trip summary math."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from config import get_settings
from database import get_db
from routers._limits import rate_limit
from services import factory
from services.base import GeoPoint, TruckSpec
from services.planning import fuel_for_trip, hos_estimate, remaining_range_miles
from services.usage import log_usage

router = APIRouter(prefix="/api/routes", tags=["routes"])
settings = get_settings()

MILES_PER_METER = 1 / 1609.344


def _truck_spec(truck: models.Truck | None) -> TruckSpec:
    if truck is None:
        return TruckSpec(label="default 13'6\" / 80,000 lb")
    return TruckSpec(
        height_cm=truck.height_cm(),
        width_cm=truck.width_cm(),
        length_cm=truck.length_cm(),
        weight_kg=truck.weight_kg(),
        axle_count=truck.axle_count,
        trailer_count=1,
        hazmat=truck.hazmat,
        label=truck.nickname or f"{truck.make} {truck.model}".strip() or "your truck",
    )


@router.post("/calculate", response_model=schemas.CalculateRouteOut)
def calculate_route(
    request: Request,
    body: schemas.CalculateRouteIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rate_limit(request, "route", settings.RATE_LIMIT_ROUTE)

    truck = None
    if body.truck_id:
        truck = (
            db.query(models.Truck)
            .filter(models.Truck.id == body.truck_id, models.Truck.user_id == user.id)
            .first()
        )
    if truck is None:
        truck = (
            db.query(models.Truck)
            .filter(models.Truck.user_id == user.id, models.Truck.is_active.is_(True))
            .first()
        )
    spec = _truck_spec(truck)

    svc = factory.get_routing_service()
    result = svc.calculate_route(
        GeoPoint(body.origin.lat, body.origin.lng, body.origin.label),
        GeoPoint(body.destination.lat, body.destination.lng, body.destination.label),
        [GeoPoint(w.lat, w.lng, w.label) for w in body.waypoints],
        truck=spec,
        alternatives=body.alternatives,
    )
    log_usage(
        db, svc.name, "route", user.id,
        meta={"stops": 2 + len(body.waypoints), "truck_safe": result.truck_safe},
    )

    prefs = (
        db.query(models.DriverPreference)
        .filter(models.DriverPreference.user_id == user.id)
        .first()
    )
    mpg = truck.avg_mpg if truck else 7.0

    options: list[schemas.RouteOptionOut] = []
    for i, opt in enumerate(result.options):
        miles = opt.distance_m * MILES_PER_METER
        mins = opt.duration_s / 60.0
        options.append(
            schemas.RouteOptionOut(
                id=f"opt-{i}",
                label=opt.label if result.truck_safe else opt.label,
                distance_miles=round(miles, 1),
                duration_min=round(mins, 1),
                est_fuel_gal=fuel_for_trip(miles, mpg),
                shape=opt.shape,
                legs=[
                    schemas.LegOut(
                        distance_miles=round(leg.get("distance_m", 0) * MILES_PER_METER, 1),
                        duration_min=round(leg.get("duration_s", 0) / 60.0, 1),
                    )
                    for leg in opt.legs
                ],
                maneuvers=[
                    schemas.ManeuverOut(
                        index=m.index, lat=m.lat, lng=m.lng,
                        instruction=m.instruction, distance_m=m.distance_m,
                        duration_s=m.duration_s,
                    )
                    for m in opt.maneuvers
                ],
            )
        )
    # Label the truck-aware option when the provider enforced restrictions.
    if result.truck_safe and options:
        options[0].label = "Truck Preferred"

    warnings = list(result.warnings)
    if not result.truck_safe and not any("dev routing" in w.lower() for w in warnings):
        warnings.append(
            "This route does NOT account for truck height, weight, length, or hazmat "
            "restrictions. Verify clearances yourself."
        )

    route_id = None
    if options:
        best = result.options[0]
        row = models.Route(
            user_id=user.id,
            provider=svc.name,
            truck_safe=result.truck_safe,
            distance_m=best.distance_m,
            duration_s=best.duration_s,
            shape=best.shape,
            maneuvers=[
                {"index": m.index, "lat": m.lat, "lng": m.lng,
                 "instruction": m.instruction, "distance_m": m.distance_m,
                 "duration_s": m.duration_s}
                for m in best.maneuvers
            ],
            alternatives=[
                {"label": o.label, "distance_m": o.distance_m, "duration_s": o.duration_s}
                for o in result.options[1:]
            ],
            warnings=warnings,
        )
        db.add(row)
        db.commit()
        route_id = row.id

    trip_id = None
    fuel_range_miles = None
    hos = None
    if body.save_trip and options:
        best = options[0]
        trip = models.Trip(
            user_id=user.id,
            name=body.trip_name or f"{body.origin.label or 'Start'} → {body.destination.label or 'End'}",
            origin_label=body.origin.label, origin_lat=body.origin.lat, origin_lng=body.origin.lng,
            dest_label=body.destination.label, dest_lat=body.destination.lat, dest_lng=body.destination.lng,
            truck_id=truck.id if truck else None,
            total_miles=best.distance_miles,
            est_drive_min=best.duration_min,
            est_fuel_gal=best.est_fuel_gal,
        )
        db.add(trip)
        db.flush()
        for seq, w in enumerate(body.waypoints):
            db.add(models.TripStop(trip_id=trip.id, seq=seq, label=w.label,
                                   lat=w.lat, lng=w.lng, stop_type="waypoint"))
        db.commit()
        trip_id = trip.id
        row = db.query(models.Route).filter(models.Route.id == route_id).first()
        if row is not None:
            row.trip_id = trip_id
            db.commit()
        fuel_range_miles = (
            remaining_range_miles(truck.fuel_capacity_gal, truck.fuel_level_pct, mpg)
            if truck else None
        )
        hos = hos_estimate(
            best.duration_min,
            prefs.available_drive_hours if prefs else None,
            prefs.break_interval_hours if prefs else None,
        )

    return schemas.CalculateRouteOut(
        provider=svc.name,
        truck_safe=result.truck_safe,
        truck_label=spec.label,
        warnings=warnings,
        options=options,
        route_id=route_id,
        trip_id=trip_id,
        fuel_range_miles=fuel_range_miles,
        hos=hos,
    )
