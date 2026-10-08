"""Database models. See spec section 32. UUIDs stored as 32-char hex strings."""
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _uid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    trucks: Mapped[list["Truck"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    preferences: Mapped["DriverPreference | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class Truck(Base):
    __tablename__ = "trucks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    nickname: Mapped[str] = mapped_column(String(120), default="")
    make: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Dimensions
    height_ft: Mapped[int] = mapped_column(Integer, default=13)
    height_in: Mapped[int] = mapped_column(Integer, default=6)
    width_in: Mapped[int] = mapped_column(Integer, default=102)
    length_ft: Mapped[float] = mapped_column(Float, default=72.0)
    weight_lbs: Mapped[int] = mapped_column(Integer, default=80000)
    trailer_length_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    axle_count: Mapped[int] = mapped_column(Integer, default=5)
    # Fuel
    fuel_capacity_gal: Mapped[float] = mapped_column(Float, default=240.0)
    fuel_level_pct: Mapped[float] = mapped_column(Float, default=100.0)
    avg_mpg: Mapped[float] = mapped_column(Float, default=7.0)
    fuel_type: Mapped[str] = mapped_column(String(20), default="diesel")
    hazmat: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_commercial: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="trucks")

    # ---- unit helpers ----
    def height_cm(self) -> int:
        return int(round((self.height_ft * 12 + self.height_in) * 2.54))

    def width_cm(self) -> int:
        return int(round(self.width_in * 2.54))

    def length_cm(self) -> int:
        return int(round(self.length_ft * 12 * 2.54))

    def weight_kg(self) -> int:
        return int(round(self.weight_lbs * 0.453592))

    def fuel_range_miles(self) -> float:
        usable = self.fuel_capacity_gal * max(0.0, min(100.0, self.fuel_level_pct)) / 100.0
        return round(usable * self.avg_mpg, 1)


class DriverPreference(Base):
    __tablename__ = "driver_preferences"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), unique=True, index=True)
    units: Mapped[str] = mapped_column(String(10), default="imperial")
    avoid_tolls: Mapped[bool] = mapped_column(Boolean, default=False)
    avoid_highways: Mapped[bool] = mapped_column(Boolean, default=False)
    map_style: Mapped[str] = mapped_column(String(20), default="dark")
    voice_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_recalculate: Mapped[bool] = mapped_column(Boolean, default=True)
    available_drive_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    break_interval_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="preferences")


class Trip(Base):
    __tablename__ = "trips"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    origin_label: Mapped[str] = mapped_column(String(320), default="")
    origin_lat: Mapped[float] = mapped_column(Float)
    origin_lng: Mapped[float] = mapped_column(Float)
    dest_label: Mapped[str] = mapped_column(String(320), default="")
    dest_lat: Mapped[float] = mapped_column(Float)
    dest_lng: Mapped[float] = mapped_column(Float)
    truck_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("trucks.id"), nullable=True)
    total_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    est_drive_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    est_fuel_gal: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    stops: Mapped[list["TripStop"]] = relationship(
        back_populates="trip", cascade="all, delete-orphan", order_by="TripStop.seq"
    )


class TripStop(Base):
    __tablename__ = "trip_stops"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    trip_id: Mapped[str] = mapped_column(String(32), ForeignKey("trips.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(320), default="")
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    stop_type: Mapped[str] = mapped_column(String(40), default="waypoint")
    notes: Mapped[str] = mapped_column(String(500), default="")

    trip: Mapped[Trip] = relationship(back_populates="stops")


class Route(Base):
    __tablename__ = "routes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    trip_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("trips.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(20), default="dev")
    truck_safe: Mapped[bool] = mapped_column(Boolean, default=False)
    distance_m: Mapped[float] = mapped_column(Float, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0)
    shape: Mapped[dict] = mapped_column(JSON, default=dict)  # GeoJSON LineString
    maneuvers: Mapped[list] = mapped_column(JSON, default=list)
    alternatives: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SavedLocation(Base):
    __tablename__ = "saved_locations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    label: Mapped[str] = mapped_column(String(160))
    address: Mapped[str] = mapped_column(String(320), default="")
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(40), default="place")
    notes: Mapped[str] = mapped_column(String(500), default="")
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NavigationSession(Base):
    __tablename__ = "navigation_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    trip_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("trips.id"), nullable=True)
    route_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("routes.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    distance_m: Mapped[float] = mapped_column(Float, default=0)
    recalculations: Mapped[int] = mapped_column(Integer, default=0)
    events: Mapped[list] = mapped_column(JSON, default=list)


class SubscriptionRecord(Base):
    __tablename__ = "subscription_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    plan: Mapped[str] = mapped_column(String(20), default="free")
    status: Mapped[str] = mapped_column(String(20), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider: Mapped[str] = mapped_column(String(20), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="usd")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    provider: Mapped[str] = mapped_column(String(20), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class UsageRecord(Base):
    __tablename__ = "usage_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    operation: Mapped[str] = mapped_column(String(40), index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class SystemError(Base):
    __tablename__ = "system_errors"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    where: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
