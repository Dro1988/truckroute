"""Provider factory: picks adapters from env, falls back to dev when no key."""
from config import get_settings
from .base import GeocodingService, RoutingService
from .dev import NominatimGeocoder, OsrmRouter
from .here import HereGeocoder, HereRouter
from .tomtom import TomTomTraffic

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


# TomTom live traffic is a singleton: its corridor cache must survive
# across requests or the free-tier call savings disappear.
_traffic_service: TomTomTraffic | None = None


def get_traffic_service() -> TomTomTraffic | None:
    """TomTom traffic adapter, or None when no key is configured."""
    global _traffic_service
    if not settings.TOMTOM_API_KEY:
        return None
    if _traffic_service is None or _traffic_service.api_key != settings.TOMTOM_API_KEY:
        _traffic_service = TomTomTraffic(
            settings.TOMTOM_API_KEY, cache_ttl_s=settings.TOMTOM_CACHE_TTL_S
        )
    return _traffic_service


def tomtom_configured() -> bool:
    return bool(settings.TOMTOM_API_KEY)
