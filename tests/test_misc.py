"""Planning math, usage/admin, subscription, saved locations, nav sessions."""
import pytest

from conftest import auth_headers, make_user
from services.planning import (
    fuel_for_trip,
    fuel_stops_needed,
    hos_estimate,
    remaining_range_miles,
)


def test_fuel_math():
    # spec example: 240 gal, 72%, 7.2 mpg -> ~1244 mi
    assert remaining_range_miles(240, 72, 7.2) == pytest.approx(1244.2, rel=0.001)
    assert fuel_for_trip(412, 7.2) == pytest.approx(57.2, rel=0.01)
    assert remaining_range_miles(240, 0, 7.2) == 0
    assert remaining_range_miles(240, 150, 7.2) == pytest.approx(240 * 7.2)  # clamped


def test_fuel_stops_needed():
    assert fuel_stops_needed(400, 1244) == 0
    assert fuel_stops_needed(1500, 1244) == 1
    assert fuel_stops_needed(2600, 1244) == 2


def test_hos_estimate():
    h = hos_estimate(402, 11.0, 4.0)  # 6.7h drive, 11h available, break every 4h
    assert h["fits_drive_window"] is True
    assert h["breaks_needed"] == 1
    assert "estimate" in h["summary"].lower()
    h2 = hos_estimate(800, 8.0)
    assert h2["fits_drive_window"] is False
    assert "exceeds" in h2["summary"].lower()
    h3 = hos_estimate(300, None)
    assert h3["fits_drive_window"] is None


def test_usage_tracking_and_admin_dashboard(client):
    make_user(client, "usage1@examplemail.com")
    h = auth_headers(client, "usage1@examplemail.com")
    # generate some usage
    client.get("/api/search/suggest?q=atlanta%20ga", headers=h)
    admin_h = auth_headers(client, "admin@examplemail.com")
    u = client.get("/api/admin/usage?days=1", headers=admin_h).json()
    assert u["total_calls"] >= 1
    assert u["active_users"] >= 1
    assert any(p["provider"] == "dev-nominatim" for p in u["by_provider"])
    assert u["avg_calls_per_user"] > 0
    health = client.get("/api/admin/health", headers=admin_h).json()
    assert health["routing_provider"] in ("here", "dev-osrm")
    assert health["here_configured"] is False  # no key in tests


def test_subscription_status_beta(client):
    make_user(client, "sub1@examplemail.com")
    h = auth_headers(client, "sub1@examplemail.com")
    s = client.get("/api/subscription/status", headers=h).json()
    assert s["is_premium"] is True  # BETA_ALL_PREMIUM=true in tests
    assert s["price_monthly_usd"] == 7.99  # from pricing.yaml, not hard-coded
    plans = client.get("/api/subscription/plans", headers=h).json()
    assert plans["premium"]["price_monthly"] == 7.99


def test_saved_locations_crud(client):
    make_user(client, "saved1@examplemail.com")
    h = auth_headers(client, "saved1@examplemail.com")
    loc = client.post("/api/saved-locations", headers=h, json={
        "label": "Home terminal", "address": "Atlanta GA", "lat": 33.7, "lng": -84.4,
        "category": "terminal", "is_favorite": True}).json()
    assert loc["id"]
    items = client.get("/api/saved-locations", headers=h).json()
    assert len(items) == 1
    up = client.put(f"/api/saved-locations/{loc['id']}", headers=h,
                    json={**{k: loc[k] for k in ("label", "address", "lat", "lng", "category", "notes")},
                          "label": "HQ", "is_favorite": False}).json()
    assert up["label"] == "HQ"
    client.delete(f"/api/saved-locations/{loc['id']}", headers=h)
    assert client.get("/api/saved-locations", headers=h).json() == []


def test_nav_session_lifecycle(client):
    make_user(client, "nav1@examplemail.com")
    h = auth_headers(client, "nav1@examplemail.com")
    s = client.post("/api/nav/start", headers=h, json={}).json()
    assert s["id"]
    e = client.post(f"/api/nav/{s['id']}/event", headers=h,
                    json={"event": "recalculated"}).json()
    assert e["recalculations"] == 1
    done = client.post(f"/api/nav/{s['id']}/end", headers=h).json()
    assert done["ended_at"] is not None
    recent = client.get("/api/nav/recent", headers=h).json()
    assert any(x["id"] == s["id"] for x in recent)


def test_trips_crud(client):
    make_user(client, "trips1@examplemail.com")
    h = auth_headers(client, "trips1@examplemail.com")
    t = client.post("/api/trips", headers=h, json={
        "name": "ATL run",
        "origin": {"lat": 33.7, "lng": -84.4, "label": "Atlanta"},
        "destination": {"lat": 35.2, "lng": -80.8, "label": "Charlotte"},
        "stops": [{"label": "Fuel", "lat": 34.0, "lng": -83.0, "stop_type": "fuel"}],
    }).json()
    assert len(t["stops"]) == 1 and t["stops"][0]["seq"] == 0
    got = client.get(f"/api/trips/{t['id']}", headers=h).json()
    assert got["name"] == "ATL run"
    client.delete(f"/api/trips/{t['id']}", headers=h)
    assert client.get(f"/api/trips/{t['id']}", headers=h).status_code == 404
