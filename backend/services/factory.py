"""Provider factory: picks adapters from env, falls back to dev when no key."""
from config import get_settings
from .base import GeocodingService, RoutingService
from .dev import NominatimGeocoder, OsrmRouter
from .here import HereGeocoder, HereRouter

settings = get_settings()


def get_routing_service() -> RoutingService:
    if settings.ROUTING_PROVIDER == "here" and settings.HERE_API_KEY:
        return HereRouter(settings.HERE_API_KEY)
    return OsrmRouter()


def get_geocoding_service() -> GeocodingService:
    if settings.GEOCODE_PROVIDER == "here" and settings.HERE_API_KEY:
        return HereGeocoder(settings.HERE_API_KEY)
    return NominatimGeocoder()


def routing_provider_name() -> str:
    return get_routing_service().name


def geocode_provider_name() -> str:
    return get_geocoding_service().name


def here_configured() -> bool:
    return bool(settings.HERE_API_KEY)
