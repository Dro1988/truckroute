"""Service interfaces. The app calls these; adapters implement them.

Swap providers by changing ROUTING_PROVIDER / GEOCODE_PROVIDER env vars —
no call-site changes needed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class GeoPoint:
    lat: float
    lng: float
    label: str = ""


@dataclass
class PlaceResult:
    label: str
    address: str
    lat: float
    lng: float
    category: str = "place"


@dataclass
class Maneuver:
    index: int
    lat: float
    lng: float
    instruction: str
    distance_m: float = 0.0
    duration_s: float = 0.0


@dataclass
class RouteOption:
    label: str
    distance_m: float
    duration_s: float
    shape: dict  # GeoJSON LineString {"type": "LineString", "coordinates": [[lng,lat],...]}
    maneuvers: list[Maneuver] = field(default_factory=list)
    legs: list[dict] = field(default_factory=list)  # per-leg [{"distance_m":..,"duration_s":..}] incl. waypoints


@dataclass
class RouteResult:
    provider: str
    truck_safe: bool  # True only if provider enforced truck restrictions
    options: list[RouteOption] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class TruckSpec:
    """What the routing provider needs to enforce restrictions."""
    height_cm: int = 411       # 13'6"
    width_cm: int = 259        # 102"
    length_cm: int = 2195      # 72'
    weight_kg: int = 36287     # 80,000 lbs
    axle_count: int = 5
    trailer_count: int = 1
    hazmat: str | None = None  # e.g. "flammable"
    label: str = ""


class GeocodingService(ABC):
    name: str = "base"

    @abstractmethod
    def suggest(self, query: str, near: GeoPoint | None = None, limit: int = 6) -> list[PlaceResult]:
        """Autocomplete / search suggestions."""

    @abstractmethod
    def geocode(self, query: str, limit: int = 5) -> list[PlaceResult]:
        """Full geocode search."""


class RoutingService(ABC):
    name: str = "base"

    @abstractmethod
    def calculate_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        waypoints: list[GeoPoint] | None = None,
        truck: TruckSpec | None = None,
        alternatives: bool = True,
    ) -> RouteResult:
        """Calculate one or more route options."""
