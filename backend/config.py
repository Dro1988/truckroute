"""TruckRoute backend configuration. All secrets come from the environment."""
import os
from functools import lru_cache


class Settings:
    APP_NAME: str = os.environ.get("APP_NAME", "TruckRoute")
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", "sqlite:///./truckroute_dev.db"
    )
    JWT_SECRET: str = os.environ.get("JWT_SECRET", "dev-only-change-me")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = int(os.environ.get("ACCESS_TOKEN_MINUTES", "1440"))
    REFRESH_TOKEN_DAYS: int = int(os.environ.get("REFRESH_TOKEN_DAYS", "30"))

    # Mapping providers. Values: "here" | "dev" (keyless dev fallback).
    # If a HERE_API_KEY is not set, "here" automatically falls back to "dev".
    ROUTING_PROVIDER: str = os.environ.get("ROUTING_PROVIDER", "here").lower()
    GEOCODE_PROVIDER: str = os.environ.get("GEOCODE_PROVIDER", "here").lower()
    HERE_API_KEY: str = os.environ.get("HERE_API_KEY", "")
    # Cost model: estimated USD per 1k provider calls, per operation.
    HERE_COST_PER_1K: float = float(os.environ.get("HERE_COST_PER_1K", "0.50"))

    # TomTom live traffic (incidents + flow), server-side only. Dormant
    # until TOMTOM_API_KEY is set — no key, no traffic feature anywhere.
    # Free tier is 2,500 calls/day; the daily cap stays under it and the
    # service caches corridor results so repeat queries cost 0 calls.
    TOMTOM_API_KEY: str = os.environ.get("TOMTOM_API_KEY", "")
    TOMTOM_DAILY_CALL_CAP: int = int(os.environ.get("TOMTOM_DAILY_CALL_CAP", "2400"))
    TOMTOM_CACHE_TTL_S: int = int(os.environ.get("TOMTOM_CACHE_TTL_S", "300"))

    # Beta: everyone gets premium features while the MVP is being validated.
    BETA_ALL_PREMIUM: bool = os.environ.get("BETA_ALL_PREMIUM", "true").lower() == "true"
    # Comma-separated admin emails.
    ADMIN_EMAILS: str = os.environ.get("ADMIN_EMAILS", "")

    # Rate limiting (requests per minute per IP) for expensive endpoints.
    RATE_LIMIT_SEARCH: int = int(os.environ.get("RATE_LIMIT_SEARCH", "30"))
    RATE_LIMIT_ROUTE: int = int(os.environ.get("RATE_LIMIT_ROUTE", "20"))

    CORS_ORIGINS: str = os.environ.get("CORS_ORIGINS", "*")


@lru_cache
def get_settings() -> Settings:
    return Settings()
