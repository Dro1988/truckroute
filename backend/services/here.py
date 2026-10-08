"""HERE production adapters: truck-aware routing, geocoding, place search.

Requires HERE_API_KEY. Truck specs are passed in cm/kg per HERE Routing v8.
All API calls go through the backend — keys never reach client code.
"""
from __future__ import annotations

import httpx

try:
    import flexpolyline as fp
except ImportError:  # pragma: no cover
    fp = None

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

ROUTER_URL = "https://router.hereapi.com/v8/routes"
GEOCODE_URL = "https://geocode.search.hereapi.com/v1/geocode"
AUTOSUGGEST_URL = "https://autosuggest.search.hereapi.com/v1/autosuggest"
DISCOVER_URL = "https://discover.search.hereapi.com/v1/discover"

_HAZMAT_MAP = {
    "explosive": "explosive",
    "gas": "gas",
    "flammable": "flammable",
    "combustible": "combustible",
    "organic": "organic",
    "poison": "poison",
    "radioactive": "radioactive",
    "corrosive": "corrosive",
    "diesel": None,  # not hazmat for routing purposes
    "none": None,
    "": None,
}


def _hazmat_value(hazmat: str | None) -> str | None:
    if not hazmat:
        return None
    key = hazmat.strip().lower()
    if key in _HAZMAT_MAP:
        return _HAZMAT_MAP[key]
    return key  # pass through; HERE will reject unknown values loudly


class HereRouter(RoutingService):
    name = "here"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def calculate_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        waypoints: list[GeoPoint] | None = None,
        truck: TruckSpec | None = None,
        alternatives: bool = True,
    ) -> RouteResult:
        if fp is None:
            return RouteResult(
                provider=self.name, truck_safe=False,
                warnings=["Server misconfigured: polyline decoder unavailable."],
            )
        truck = truck or TruckSpec()
        params: list[tuple[str, str]] = [
            ("transportMode", "truck"),
            ("origin", f"{origin.lat},{origin.lng}"),
            ("destination", f"{destination.lat},{destination.lng}"),
            ("return", "polyline,summary,actions,instructions,notices"),
            ("truck[height]", str(truck.height_cm)),
            ("truck[width]", str(truck.width_cm)),
            ("truck[length]", str(truck.length_cm)),
            ("truck[grossWeight]", str(truck.weight_kg)),
            ("truck[axleCount]", str(truck.axle_count)),
            ("truck[trailerCount]", str(truck.trailer_count)),
            ("apiKey", self.api_key),
        ]
        for wp in waypoints or []:
            params.append(("via", f"{wp.lat},{wp.lng}"))
        hz = _hazmat_value(truck.hazmat)
        if hz:
            params.append(("truck[shippedHazardousGoods]", hz))
        if alternatives:
            params.append(("alternatives", "2"))
        try:
            r = httpx.get(ROUTER_URL, params=params, timeout=25)
        except Exception as e:
            return RouteResult(
                provider=self.name, truck_safe=False,
                warnings=[f"HERE routing unavailable: {type(e).__name__}"],
            )
        if r.status_code != 200:
            detail = ""
            try:
                detail = r.json().get("title", "")
            except Exception:
                pass
            return RouteResult(
                provider=self.name, truck_safe=False,
                warnings=[f"HERE routing error ({r.status_code}): {detail or 'request rejected'}"],
            )
        try:
            data = r.json()
        except Exception:
            return RouteResult(
                provider=self.name, truck_safe=False, warnings=["HERE returned an unreadable response."]
            )
        routes = data.get("routes") or []
        if not routes:
            return RouteResult(
                provider=self.name, truck_safe=False,
                warnings=["HERE found no route. Your truck's dimensions may make this trip impossible."],
            )
        options: list[RouteOption] = []
        warnings: list[str] = []
        notices_seen: set[str] = set()
        for ri, rt in enumerate(routes[:3]):
            coords: list[list[float]] = []
            maneuvers: list[Maneuver] = []
            total_len = 0.0
            total_dur = 0.0
            for section in rt.get("sections", []):
                poly = section.get("polyline", "")
                try:
                    pts = fp.decode(poly)  # [(lat, lng), ...]
                except Exception:
                    continue
                base_idx = len(coords)
                coords.extend([[p[1], p[0]] for p in pts])  # GeoJSON = [lng, lat]
                ssum = section.get("summary", {})
                total_len += ssum.get("length", 0)
                total_dur += ssum.get("duration", 0)
                for a in section.get("actions", []):
                    off = a.get("offset", 0)
                    ci = min(base_idx + off, len(coords) - 1) if coords else 0
                    lat, lng = (coords[ci][1], coords[ci][0]) if coords else (0, 0)
                    maneuvers.append(
                        Maneuver(
                            index=len(maneuvers),
                            lat=lat,
                            lng=lng,
                            instruction=a.get("instruction", "").strip(),
                            distance_m=a.get("length", 0),
                            duration_s=a.get("duration", 0),
                        )
                    )
                for n in section.get("notices", []):
                    code = n.get("code", "")
                    if code and code not in notices_seen:
                        notices_seen.add(code)
                        warnings.append(f"HERE notice ({code}): {n.get('title', '')}".strip())
            if not coords:
                continue
            options.append(
                RouteOption(
                    label=["Fastest", "Alternative 1", "Alternative 2"][ri]
                    if ri < 3 else f"Option {ri + 1}",
                    distance_m=total_len,
                    duration_s=total_dur,
                    shape={"type": "LineString", "coordinates": coords},
                    maneuvers=maneuvers,
                )
            )
        if not options:
            return RouteResult(
                provider=self.name, truck_safe=False,
                warnings=["HERE returned routes with no usable geometry."],
            )
        violation = any("violat" in w.lower() or "restriction" in w.lower() for w in warnings)
        truck_safe = not violation
        if violation:
            warnings.insert(
                0,
                "HERE reports it could not fully honor your truck's restrictions on this route. "
                "Review before driving.",
            )
        return RouteResult(provider=self.name, truck_safe=truck_safe, options=options, warnings=warnings)


class HereGeocoder(GeocodingService):
    name = "here"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def suggest(self, query: str, near: GeoPoint | None = None, limit: int = 6) -> list[PlaceResult]:
        if len(query.strip()) < 2:
            return []
        params = {"q": query, "limit": limit, "apiKey": self.api_key}
        if near:
            params["at"] = f"{near.lat},{near.lng}"
        try:
            r = httpx.get(AUTOSUGGEST_URL, params=params, timeout=15)
            r.raise_for_status()
            items = r.json().get("items", [])
        except Exception:
            return []
        return [self._to_place(it) for it in items if it.get("position")]

    def geocode(self, query: str, limit: int = 5) -> list[PlaceResult]:
        params = {"q": query, "limit": limit, "apiKey": self.api_key}
        try:
            r = httpx.get(GEOCODE_URL, params=params, timeout=15)
            r.raise_for_status()
            items = r.json().get("items", [])
        except Exception:
            return []
        return [self._to_place(it) for it in items if it.get("position")]

    def discover(self, query: str, near: GeoPoint, limit: int = 10) -> list[PlaceResult]:
        """Truck stops, fuel, parking, repair — HERE Discover."""
        params = {
            "q": query,
            "at": f"{near.lat},{near.lng}",
            "limit": limit,
            "apiKey": self.api_key,
        }
        try:
            r = httpx.get(DISCOVER_URL, params=params, timeout=15)
            r.raise_for_status()
            items = r.json().get("items", [])
        except Exception:
            return []
        out = []
        for it in items:
            if not it.get("position"):
                continue
            cats = ",".join(c.get("name", "") for c in it.get("categories", []))
            p = self._to_place(it)
            p.category = cats or p.category
            out.append(p)
        return out

    @staticmethod
    def _to_place(it: dict) -> PlaceResult:
        pos = it.get("position", {})
        addr = it.get("address", {})
        return PlaceResult(
            label=it.get("title", ""),
            address=addr.get("label", "") if isinstance(addr, dict) else "",
            lat=pos.get("lat", 0),
            lng=pos.get("lng", 0),
            category=it.get("resultType", "place"),
        )
