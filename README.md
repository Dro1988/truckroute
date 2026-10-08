# TruckRoute (MVP)

Affordable commercial-truck GPS & navigation. Android app + responsive web dashboard
on one backend. Working name is configurable via `APP_NAME`.

## Architecture

- **Backend:** FastAPI + Postgres (`backend/`). Serves the web dashboard statically.
- **Web:** vanilla JS single page (`web/`) — MapLibre GL + Carto basemaps.
- **Android:** framework-only WebView shell (`android/`, `com.truckroute.app`) bundling
  the same `web/` code; native GPS (LocationManager), TTS, foreground nav service.
- **Mapping abstraction:** the app only calls `RoutingService` / `GeocodingService`
  interfaces (`backend/services/base.py`). Adapters:
  - `here.py` — production truck routing (`transportMode=truck` + dimensions/weight/
    hazmat), geocode, autosuggest, place discover. Needs `HERE_API_KEY`.
  - `dev.py` — keyless Nominatim + OSRM demo adapters. Real services, but **not**
    truck-aware; every result is labeled `truck_safe: false`.

## Quick start (backend)

```bash
cd backend
pip install -r requirements.txt
uvicorn app_main:app --reload   # serves API + web UI at http://localhost:8000
```

Env vars: `DATABASE_URL`, `JWT_SECRET`, `HERE_API_KEY`, `ROUTING_PROVIDER`
(`here`|`dev`), `GEOCODE_PROVIDER` (`here`|`dev`), `ADMIN_EMAILS`,
`BETA_ALL_PREMIUM` (default true), `APP_NAME`, `RATE_LIMIT_SEARCH`,
`RATE_LIMIT_ROUTE`, `HERE_COST_PER_1K`.

Without `HERE_API_KEY`, routing/geocoding automatically use the dev providers.

## Tests

```bash
python -m pytest tests/ -q
```

## Android build

```bash
API_BASE=https://truckroute-api.onrender.com bash android/build-truckroute.sh
# -> /home/hatch/workspace/.tools/truckroute-build/TruckRoute.apk
```

## Deploy (Render)

- Postgres database + free web service pointing at `backend/`:
  build `pip install -r backend/requirements.txt`, start
  `uvicorn app_main:app --host 0.0.0.0 --port $PORT --app-dir backend`.
- The web dashboard and `/install/` page are served by the API itself.
