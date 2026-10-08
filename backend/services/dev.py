"""Keyless development adapters: Nominatim (geocode) + OSRM demo (routing).

These are REAL services (not mocks) but they do NOT know about truck
restrictions. Every result is labeled truck_safe=False so the app never
pretends a route is guaranteed truck-safe.
"""
from __future__ import annotations

import httpx

from .base import (
    GeocodingService,
    GeoPoint,
    Maneuver,
    PlaceResult,
    RouteOption,
    RouteResult,
    RoutingService,
    TruckSpec,
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
HEADERS = {"User-Agent": "TruckRoute/1.0 (contact: support@truckroute.app)"}


class NominatimGeocoder(GeocodingService):
    name = "dev-nominatim"

    def _search(self, query: str, limit: int) -> list[PlaceResult]:
        params = {
            "q": query,
            "format": "jsonv2",
            "limit": limit,
            "addressdetails": 1,
        }
        try:
            r = httpx.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=15)
            r.raise_for_status()
            items = r.json()
        except Exception:
            return []
        out = []
        for it in items:
            try:
                out.append(
                    PlaceResult(
                        label=it.get("display_name", "").split(",")[0],
                        address=it.get("display_name", ""),
                        lat=float(it["lat"]),
                        lng=float(it["lon"]),
                        category=it.get("class", "place"),
                    )
                )
            except (KeyError, ValueError):
                continue
        return out

    def suggest(self, query: str, near: GeoPoint | None = None, limit: int = 6) -> list[PlaceResult]:
        if len(query.strip()) < 2:
            return []
        return self._search(query, limit)

    def geocode(self, query: str, limit: int = 5) -> list[PlaceResult]:
        return self._search(query, limit)


_OSRM_INSTRUCTIONS = {
    ("turn", "left"): "Turn left",
    ("turn", "right"): "Turn right",
    ("turn", "slight left"): "Keep slightly left",
    ("turn", "slight right"): "Keep slightly right",
    ("turn", "sharp left"): "Turn sharp left",
    ("turn", "sharp right"): "Turn sharp right",
    ("turn", "uturn"): "Make a U-turn",
    ("turn", "straight"): "Continue straight",
    ("new name", "straight"): "Continue straight",
    ("depart", ""): "Head",
    ("arrive", ""): "Arrive at your destination",
    ("roundabout", ""): "Enter the roundabout",
    ("rotary", ""): "Enter the rotary",
    ("merge", ""): "Merge",
    ("on ramp", ""): "Take the on-ramp",
    ("off ramp", ""): "Take the off-ramp",
    ("fork", "slight left"): "Keep left at the fork",
    ("fork", "slight right"): "Keep right at the fork",
    ("end of road", "left"): "At the end of the road, turn left",
    ("end of road", "right"): "At the end of the road, turn right",
}


def _osrm_instruction(step: dict) -> str:
    man = step.get("maneuver", {})
    mtype = man.get("type", "")
    mod = man.get("modifier", "") or ""
    base = _OSRM_INSTRUCTIONS.get((mtype, mod)) or _OSRM_INSTRUCTIONS.get(
        (mtype, "")
    ) or mtype.replace("_", " ").title()
    name = step.get("name", "")
    if name and mtype not in ("arrive",):
        base = f"{base} onto {name}"
    return base + "."


class OsrmRouter(RoutingService):
    name = "dev-osrm"

    def calculate_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        waypoints: list[GeoPoint] | None = None,
        truck: TruckSpec | None = None,
        alternatives: bool = True,
    ) -> RouteResult:
        pts = [origin] + list(waypoints or []) + [destination]
        coords = ";".join(f"{p.lng},{p.lat}" for p in pts)
        params = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
            "annotations": "false",
        }
        if alternatives:
            params["alternatives"] = "2"
        try:
            r = httpx.get(f"{OSRM_URL}/{coords}", params=params, headers=HEADERS, timeout=25)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return RouteResult(
                provider=self.name, truck_safe=False,
                warnings=[f"Routing service unavailable: {type(e).__name__}"],
            )
        if data.get("code") != "Ok" or not data.get("routes"):
            return RouteResult(
                provider=self.name, truck_safe=False,
                warnings=["No route found for these locations."],
            )
        labels = ["Fastest", "Alternative 1", "Alternative 2"]
        options = []
        for i, rt in enumerate(data["routes"][:3]):
            maneuvers = []
            for leg in rt.get("legs", []):
                for step in leg.get("steps", []):
                    man = step.get("maneuver", {})
                    loc = man.get("location", [0, 0])
                    maneuvers.append(
                        Maneuver(
                            index=len(maneuvers),
                            lat=loc[1],
                            lng=loc[0],
                            instruction=_osrm_instruction(step),
                            distance_m=step.get("distance", 0),
                            duration_s=step.get("duration", 0),
                        )
                    )
            options.append(
                RouteOption(
                    label=labels[i] if i < len(labels) else f"Option {i + 1}",
                    distance_m=rt.get("distance", 0),
                    duration_s=rt.get("duration", 0),
                    shape=rt.get("geometry", {"type": "LineString", "coordinates": []}),
                    maneuvers=maneuvers,
                )
            )
        warnings = [
            "Dev routing mode: route does NOT account for truck height, weight, "
            "length, or hazmat restrictions. Verify clearances yourself."
        ]
        return RouteResult(provider=self.name, truck_safe=False, options=options, warnings=warnings)
