"""TomTom live traffic adapter: incidents + flow along a route corridor.

Requires TOMTOM_API_KEY. The key stays server-side — clients only ever
talk to our backend, never to TomTom directly.

Free tier is 2,500 calls/day, so this service is careful with calls:
  * Incidents are queried per corridor TILE (bbox chunks along the route,
    each well under TomTom's 10,000 km2 bbox cap) — never nationwide.
  * Flow is queried only at sampled points along the route polyline.
  * Every response is cached for a few minutes; an identical corridor
    query inside the TTL costs 0 calls.
  * The caller hands in a call budget (the day's remaining allowance);
    when it runs out the service serves cache only and reports
    budget_exhausted instead of blowing the quota.

Nothing here fabricates data: if TomTom can't be reached and nothing is
cached, the result is empty with ok=False and the UI shows an honest
"live traffic unavailable" state.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import httpx

INCIDENTS_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"
FLOW_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"

# Ask TomTom for everything we render; omitted fields come back empty.
_FIELDS = (
    "{incidents{type,geometry{type,coordinates},properties{id,iconCategory,"
    "magnitudeOfDelay,events{description,code,iconCategory},startTime,endTime,"
    "from,to,length,delay,roadNumbers,timeValidity}}}"
)

# Corridor tiling: each tile stays under TomTom's 10,000 km2 bbox limit
# (~0.55 x 0.75 deg at US latitudes is ~5,000 km2) and the tile count is
# capped — very long routes get evenly sampled coverage, flagged partial.
TILE_LAT_SPAN = 0.55
TILE_LNG_SPAN = 0.75
MAX_TILES = 8
FLOW_SAMPLE_EVERY_MI = 6.0
MAX_FLOW_SAMPLES = 10
SLOW_RATIO = 0.8  # current/free-flow below this = a real slowdown

# TomTom iconCategory -> (kind, label, emoji). Descriptions shown to the
# driver come from TomTom's own event text; kind only groups/colors them.
_CATEGORY: dict[int, tuple[str, str, str]] = {
    1: ("accident", "Accident", "💥"),
    2: ("weather", "Fog", "🌫️"),
    3: ("hazard", "Dangerous conditions", "⚠️"),
    4: ("weather", "Rain", "🌧️"),
    5: ("weather", "Ice on road", "🧊"),
    6: ("jam", "Traffic jam", "🐢"),
    7: ("weather", "Snow", "🌨️"),
    8: ("closure", "Road closed", "⛔"),
    9: ("construction", "Road works", "🚧"),
    10: ("weather", "Wind", "💨"),
    11: ("hazard", "Flooding", "🌊"),
    13: ("hazard", "Broken-down vehicle", "🚗"),
}


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class TrafficFetch:
    incidents: list[dict] = field(default_factory=list)
    slowdowns: list[dict] = field(default_factory=list)
    calls_made: int = 0
    ok: bool = False              # provider answered (or cache satisfied) at least once
    budget_exhausted: bool = False
    partial: bool = False         # long route: coverage sampled, not continuous
    error: str | None = None


class TomTomTraffic:
    name = "tomtom"

    def __init__(self, api_key: str, cache_ttl_s: int = 300):
        self.api_key = api_key
        self.cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[float, object]] = {}

    # ---- cache ----
    def _cache_get(self, key: str):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_ttl_s:
            return hit[1]
        return None

    def _cache_put(self, key: str, value) -> None:
        self._cache[key] = (time.time(), value)

    # ---- low-level GET with budget accounting ----
    def _get(self, url: str, params: dict, state: dict) -> dict | None:
        """One provider call, or None. state tracks calls_made/budget."""
        if state["calls_made"] >= state["budget"]:
            state["exhausted"] = True
            return None
        state["calls_made"] += 1
        try:
            r = httpx.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            state["error"] = f"{type(e).__name__}"
            return None

    # ---- corridor tiling ----
    @staticmethod
    def _tiles(pts: list[tuple[float, float]], corridor_m: float) -> tuple[list[tuple], bool]:
        """Greedy bbox chunks along the polyline, padded by the corridor."""
        pad_lat = corridor_m / 111320.0 + 0.01
        pad_lng = corridor_m / (111320.0 * 0.75) + 0.01  # ~mid-US cos(lat)
        tiles: list[list[float]] = []
        cur: list[float] | None = None  # [min_lat, min_lng, max_lat, max_lng]
        for lat, lng in pts:
            if cur is None:
                cur = [lat, lng, lat, lng]
                continue
            n = [min(cur[0], lat), min(cur[1], lng), max(cur[2], lat), max(cur[3], lng)]
            if n[2] - n[0] <= TILE_LAT_SPAN and n[3] - n[1] <= TILE_LNG_SPAN:
                cur = n
            else:
                tiles.append(cur)
                cur = [lat, lng, lat, lng]
        if cur is not None:
            tiles.append(cur)
        partial = False
        if len(tiles) > MAX_TILES:
            # Evenly sample tiles across the route; coverage becomes
            # windowed rather than continuous — reported as partial.
            step = len(tiles) / MAX_TILES
            tiles = [tiles[int(i * step)] for i in range(MAX_TILES)]
            partial = True
        out = [
            (round(t[0] - pad_lat, 4), round(t[1] - pad_lng, 4),
             round(t[2] + pad_lat, 4), round(t[3] + pad_lng, 4))
            for t in tiles
        ]
        return out, partial

    # ---- main entry ----
    def along_route(
        self,
        pts: list[tuple[float, float]],  # (lat, lng) polyline
        corridor_m: float = 600,
        call_budget: int = 0,
    ) -> TrafficFetch:
        result = TrafficFetch()
        if len(pts) < 2:
            result.error = "Need at least 2 route points."
            return result
        state = {"calls_made": 0, "budget": max(0, call_budget), "exhausted": False, "error": None}

        # Cumulative meters along the polyline for miles_along projection.
        cum = [0.0]
        for i in range(1, len(pts)):
            cum.append(cum[-1] + _haversine_m(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]))
        mid_lat = sum(p[0] for p in pts) / len(pts)
        kx = 111320.0 * max(0.2, math.cos(math.radians(mid_lat)))
        proj = [(lng * kx, lat * 110540.0) for lat, lng in pts]

        def project(lat: float, lng: float) -> tuple[float, float]:
            """(distance-to-route m, meters along route) for a point."""
            px, py = lng * kx, lat * 110540.0
            best_d, best_along = math.inf, 0.0
            for i in range(len(proj) - 1):
                ax, ay = proj[i]
                bx, by = proj[i + 1]
                dx, dy = bx - ax, by - ay
                seg2 = dx * dx + dy * dy
                t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
                if d < best_d:
                    best_d = d
                    best_along = cum[i] + t * (cum[i + 1] - cum[i])
            return best_d, best_along

        # ---- incidents per tile ----
        tiles, partial = self._tiles(pts, corridor_m)
        result.partial = partial
        seen_ids: set[str] = set()
        for (s_lat, s_lng, n_lat, n_lng) in tiles:
            key = f"inc:{s_lat},{s_lng},{n_lat},{n_lng}"
            raw = self._cache_get(key)
            if raw is None:
                data = self._get(INCIDENTS_URL, {
                    "key": self.api_key,
                    "bbox": f"{s_lng},{s_lat},{n_lng},{n_lat}",
                    "fields": _FIELDS,
                    "language": "en-US",
                    "timeValidityFilter": "present",
                }, state)
                if data is None:
                    continue
                raw = data.get("incidents", []) if isinstance(data, dict) else []
                self._cache_put(key, raw)
            result.ok = True
            for inc in raw or []:
                parsed = self._parse_incident(inc, project, corridor_m)
                if parsed and parsed["id"] not in seen_ids:
                    seen_ids.add(parsed["id"])
                    result.incidents.append(parsed)

        # ---- flow at sampled points on the route ----
        total_mi = cum[-1] / 1609.344
        marks: list[float] = []
        m = FLOW_SAMPLE_EVERY_MI
        while m < total_mi - 1.0 and len(marks) < MAX_FLOW_SAMPLES:
            marks.append(m)
            m += FLOW_SAMPLE_EVERY_MI
        for mark_mi in marks:
            target_m = mark_mi * 1609.344
            idx = next((i for i, c in enumerate(cum) if c >= target_m), len(cum) - 1)
            lat, lng = pts[idx]
            key = f"flow:{round(lat, 3)},{round(lng, 3)}"
            raw = self._cache_get(key)
            if raw is None:
                data = self._get(FLOW_URL, {
                    "key": self.api_key,
                    "point": f"{lat},{lng}",
                    "unit": "mph",
                }, state)
                if data is None:
                    continue
                result.ok = True  # provider answered, even if it has no segment here
                raw = data.get("flowSegmentData") if isinstance(data, dict) else None
                if raw is not None:
                    self._cache_put(key, raw)
            if not raw:
                continue
            result.ok = True
            slow = self._parse_flow(raw, lat, lng, cum[idx])
            if slow:
                result.slowdowns.append(slow)

        result.incidents.sort(key=lambda x: x["miles_along"])
        result.slowdowns.sort(key=lambda x: x["miles_along"])
        result.calls_made = state["calls_made"]
        result.budget_exhausted = bool(state["exhausted"])
        if not result.ok:
            result.error = state["error"] or ("budget" if state["exhausted"] else "unavailable")
        return result

    # ---- parsing ----
    @staticmethod
    def _parse_incident(inc: dict, project, corridor_m: float) -> dict | None:
        try:
            geom = inc.get("geometry", {}) or {}
            coords = geom.get("coordinates") or []
            if not coords:
                return None
            if geom.get("type") == "LineString":
                lng, lat = coords[len(coords) // 2]
            else:  # Point
                lng, lat = coords[0], coords[1]
            dist_m, along_m = project(float(lat), float(lng))
            if dist_m > corridor_m:
                return None
            props = inc.get("properties", {}) or {}
            cat = int(props.get("iconCategory") or 0)
            kind, label, emoji = _CATEGORY.get(cat, ("other", "Incident", "📍"))
            events = props.get("events") or []
            desc = (events[0].get("description") if events else "") or label
            roads = props.get("roadNumbers") or []
            road = ", ".join(roads) if roads else ""
            if not road and props.get("from"):
                road = f"{props.get('from', '')} → {props.get('to', '')}".strip(" →")
            delay_s = props.get("delay")
            return {
                "id": str(props.get("id") or f"{lat:.5f},{lng:.5f},{cat}"),
                "kind": kind,
                "label": label,
                "emoji": emoji,
                "description": desc,
                "lat": float(lat),
                "lng": float(lng),
                "miles_along": round(along_m / 1609.344, 1),
                "delay_min": round(delay_s / 60.0, 1) if delay_s else None,
                "severity": int(props.get("magnitudeOfDelay") or 0),
                "road": road,
            }
        except Exception:
            return None

    @staticmethod
    def _parse_flow(seg: dict, lat: float, lng: float, along_m: float) -> dict | None:
        try:
            cur = float(seg.get("currentSpeed") or 0)
            free = float(seg.get("freeFlowSpeed") or 0)
            closed = bool(seg.get("roadClosure"))
            if free <= 0:
                return None
            ratio = 0.0 if closed else cur / free
            if not closed and ratio >= SLOW_RATIO:
                return None
            return {
                "lat": lat,
                "lng": lng,
                "miles_along": round(along_m / 1609.344, 1),
                "current_mph": round(cur),
                "free_flow_mph": round(free),
                "ratio": round(ratio, 2),
                "road_closure": closed,
            }
        except Exception:
            return None
