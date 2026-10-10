"""Pydantic request/response schemas."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


# ---------- Auth ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    is_admin: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ProfileUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[EmailStr] = None


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


# ---------- Trucks ----------
class TruckIn(BaseModel):
    nickname: str = Field(default="", max_length=120)
    make: str = Field(default="", max_length=80)
    model: str = Field(default="", max_length=80)
    year: Optional[int] = Field(default=None, ge=1950, le=2100)
    height_ft: int = Field(default=13, ge=6, le=20)
    height_in: int = Field(default=6, ge=0, le=11)
    width_in: int = Field(default=102, ge=60, le=120)
    length_ft: float = Field(default=72.0, ge=10, le=120)
    weight_lbs: int = Field(default=80000, ge=5000, le=200000)
    trailer_length_ft: Optional[float] = Field(default=None, ge=10, le=60)
    axle_count: int = Field(default=5, ge=2, le=12)
    fuel_capacity_gal: float = Field(default=240.0, ge=20, le=500)
    fuel_level_pct: float = Field(default=100.0, ge=0, le=100)
    avg_mpg: float = Field(default=7.0, ge=2, le=20)
    fuel_type: str = Field(default="diesel", max_length=20)
    hazmat: Optional[str] = Field(default=None, max_length=80)
    is_commercial: bool = True


class TruckOut(TruckIn):
    id: str
    is_active: bool
    fuel_range_miles: float
    created_at: datetime

    model_config = {"from_attributes": True}


class ActiveTruckIn(BaseModel):
    truck_id: str


# ---------- Preferences ----------
class PreferencesIn(BaseModel):
    units: Optional[str] = "imperial"
    avoid_tolls: Optional[bool] = None
    avoid_highways: Optional[bool] = None
    map_style: Optional[str] = None
    voice_enabled: Optional[bool] = None
    auto_recalculate: Optional[bool] = None
    available_drive_hours: Optional[float] = None
    break_interval_hours: Optional[float] = None


class PreferencesOut(PreferencesIn):
    user_id: str

    model_config = {"from_attributes": True}


# ---------- Search ----------
class SuggestOut(BaseModel):
    label: str
    address: str = ""
    lat: float
    lng: float
    category: str = "place"
    provider: str


# ---------- Routes ----------
class RoutePoint(BaseModel):
    lat: float
    lng: float
    label: str = ""


class CalculateRouteIn(BaseModel):
    origin: RoutePoint
    destination: RoutePoint
    waypoints: list[RoutePoint] = Field(default_factory=list, max_length=10)
    truck_id: Optional[str] = None
    alternatives: bool = True
    save_trip: bool = False
    trip_name: str = ""


class ManeuverOut(BaseModel):
    index: int
    lat: float
    lng: float
    instruction: str
    distance_m: float = 0
    duration_s: float = 0


class RouteOptionOut(BaseModel):
    id: str
    label: str
    distance_miles: float
    duration_min: float
    est_fuel_gal: Optional[float] = None
    shape: dict  # GeoJSON LineString
    maneuvers: list[ManeuverOut]
    legs: list["LegOut"] = Field(default_factory=list)  # per-leg incl. waypoints


class LegOut(BaseModel):
    distance_miles: float
    duration_min: float


class CalculateRouteOut(BaseModel):
    provider: str
    truck_safe: bool
    truck_label: str = ""
    warnings: list[str] = Field(default_factory=list)
    options: list[RouteOptionOut]
    route_id: Optional[str] = None
    # Trip summary (present when save_trip=true)
    trip_id: Optional[str] = None
    fuel_range_miles: Optional[float] = None
    hos: Optional[dict] = None


# ---------- Trips ----------
class TripStopIn(BaseModel):
    label: str
    lat: float
    lng: float
    stop_type: str = "waypoint"
    notes: str = ""


class TripCreateIn(BaseModel):
    name: str = ""
    origin: RoutePoint
    destination: RoutePoint
    stops: list[TripStopIn] = Field(default_factory=list)
    truck_id: Optional[str] = None


class TripStopOut(TripStopIn):
    id: str
    seq: int

    model_config = {"from_attributes": True}


class TripOut(BaseModel):
    id: str
    name: str
    origin_label: str
    origin_lat: float
    origin_lng: float
    dest_label: str
    dest_lat: float
    dest_lng: float
    truck_id: Optional[str]
    total_miles: Optional[float]
    est_drive_min: Optional[float]
    est_fuel_gal: Optional[float]
    status: str
    created_at: datetime
    stops: list[TripStopOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ---------- Saved locations ----------
class SavedLocationIn(BaseModel):
    label: str = Field(max_length=160)
    address: str = ""
    lat: float
    lng: float
    category: str = "place"
    notes: str = ""
    is_favorite: bool = False


class SavedLocationOut(SavedLocationIn):
    id: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- Navigation sessions ----------
class NavStartIn(BaseModel):
    trip_id: Optional[str] = None
    route_id: Optional[str] = None


class NavEventIn(BaseModel):
    event: str = Field(max_length=60)  # recalculated | arrived | stopped | ...
    distance_m: Optional[float] = None


class NavSessionOut(BaseModel):
    id: str
    trip_id: Optional[str]
    route_id: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    distance_m: float
    recalculations: int

    model_config = {"from_attributes": True}


# ---------- Subscription ----------
class SubscriptionOut(BaseModel):
    plan: str
    status: str
    is_premium: bool
    ends_at: Optional[datetime]
    price_monthly_usd: Optional[float]
    price_annual_usd: Optional[float]


# ---------- Admin ----------
class UsageSummaryOut(BaseModel):
    period_days: int
    total_calls: int
    total_cost_usd: float
    active_users: int
    by_provider: list[dict[str, Any]]
    by_operation: list[dict[str, Any]]
    avg_calls_per_user: float


# ---- incident reports (crowdsourced) ----
class IncidentReportIn(BaseModel):
    kind: str = Field(min_length=2, max_length=30)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    note: str = Field(default="", max_length=200)


class IncidentOut(BaseModel):
    id: str
    kind: str
    label: str
    emoji: str
    lat: float
    lng: float
    confirms: int
    denies: int
    created_at: datetime
    expires_at: datetime
    age_min: int
    alert_m: int
    voice_text: str
