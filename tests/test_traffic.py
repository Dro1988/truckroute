"""TomTom live traffic tests: service parsing/corridor/cache/budget with
mocked HTTP, plus the /api/traffic/along endpoint in its three states —
no key (disabled), with key (live data), over the daily cap (degraded).

Nothing here calls the real TomTom API.
"""
import pytest

from conftest import auth_headers, make_user

import services.tomtom as tomtom_mod
from config import get_settings
from services.tomtom import TomTomTraffic


def _mk(client, email):
    make_user(client, email)
    return auth_headers(client, email)


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _incident(lng, lat, geom_type="Point", delay=900, cat=1, desc="Accident, one lane closed"):
    if geom_type == "LineString":
        coords = [[lng, lat - 0.05], [lng, lat + 0.05]]
    else:
        coords = [lng, lat]
    return {
        "type": "Feature",
        "geometry": {"type": geom_type, "coordinates": coords},
        "properties": {
            "id": f"inc-{lng}-{lat}-{cat}", "iconCategory": cat,
            "magnitudeOfDelay": 3,
            "events": [{"description": desc, "code": cat, "iconCategory": cat}],
            "from": "Exit 10", "to": "Exit 12", "length": 800,
            "delay": delay, "roadNumbers": ["I-75"], "timeValidity": "present",
        },
    }


class FakeTomTom:
    """Dispatches on URL; records every call so tests can count spend."""

    def __init__(self, incidents=None, flow_speed=25, flow_free=65):
        self.incidents = incidents if incidents is not None else []
        self.flow_speed = flow_speed
        self.flow_free = flow_free
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if "incidentDetails" in url:
            return FakeResp({"incidents": self.incidents})
        if "flowSegmentData" in url:
            return FakeResp({"flowSegmentData": {
                "frc": "FRC2", "currentSpeed": self.flow_speed,
                "freeFlowSpeed": self.flow_free, "currentTravelTime": 300,
                "freeFlowTravelTime": 120, "confidence": 0.9,
                "roadClosure": False,
                "coordinates": {"coordinate": []}}})
        raise AssertionError(f"unexpected URL {url}")


# A straight ~69 mi northbound line (Atlanta-ish), 0.25-degree steps.
NORTH_LINE = [(33.0, -84.0), (33.25, -84.0), (33.5, -84.0), (33.75, -84.0), (34.0, -84.0)]


# ---------------- service level ----------------

def test_service_parses_incidents_flow_and_projects_miles(monkeypatch):
    fake = FakeTomTom(incidents=[_incident(-84.0, 33.5, geom_type="LineString")])
    monkeypatch.setattr(tomtom_mod.httpx, "get", fake.get)
    svc = TomTomTraffic("svc-key-1", cache_ttl_s=300)
    res = svc.along_route(NORTH_LINE, corridor_m=600, call_budget=50)
    assert res.ok and not res.budget_exhausted
    assert res.calls_made == len(fake.calls) > 0
    assert len(res.incidents) == 1
    it = res.incidents[0]
    assert it["kind"] == "accident"
    assert it["delay_min"] == 15.0
    assert it["severity"] == 3
    assert it["road"] == "I-75"
    # LineString midpoint is lat 33.5 -> ~34.5 mi along the 69 mi line.
    assert 30.0 < it["miles_along"] < 40.0
    assert res.slowdowns, "25 mph vs 65 free-flow must register as slowdowns"
    assert all(s["ratio"] < 0.8 for s in res.slowdowns)


def test_service_cache_makes_repeat_query_free(monkeypatch):
    fake = FakeTomTom(incidents=[_incident(-84.0, 33.5)])
    monkeypatch.setattr(tomtom_mod.httpx, "get", fake.get)
    svc = TomTomTraffic("svc-key-2", cache_ttl_s=300)
    first = svc.along_route(NORTH_LINE, corridor_m=600, call_budget=50)
    spent = len(fake.calls)
    assert first.calls_made == spent > 0
    # Same corridor, zero budget: everything must come from cache.
    second = svc.along_route(NORTH_LINE, corridor_m=600, call_budget=0)
    assert len(fake.calls) == spent
    assert second.calls_made == 0
    assert second.ok and not second.budget_exhausted
    assert len(second.incidents) == len(first.incidents) == 1
    assert len(second.slowdowns) == len(first.slowdowns)


def test_service_zero_budget_no_cache_makes_no_calls(monkeypatch):
    fake = FakeTomTom()
    monkeypatch.setattr(tomtom_mod.httpx, "get", fake.get)
    svc = TomTomTraffic("svc-key-3", cache_ttl_s=300)
    savannah = [(32.0, -81.1), (32.15, -81.1), (32.3, -81.1)]
    res = svc.along_route(savannah, corridor_m=600, call_budget=0)
    assert fake.calls == []
    assert res.calls_made == 0
    assert res.ok is False and res.budget_exhausted is True
    assert res.incidents == [] and res.slowdowns == []


def test_service_free_flow_is_not_a_slowdown(monkeypatch):
    fake = FakeTomTom(flow_speed=65, flow_free=65)
    monkeypatch.setattr(tomtom_mod.httpx, "get", fake.get)
    svc = TomTomTraffic("svc-key-4", cache_ttl_s=300)
    res = svc.along_route(NORTH_LINE, corridor_m=600, call_budget=50)
    assert res.ok
    assert res.slowdowns == []


def test_service_incident_off_corridor_excluded(monkeypatch):
    # ~29 mi east of the line: inside the tile bbox, outside the corridor.
    fake = FakeTomTom(incidents=[_incident(-83.5, 33.5)])
    monkeypatch.setattr(tomtom_mod.httpx, "get", fake.get)
    svc = TomTomTraffic("svc-key-5", cache_ttl_s=300)
    res = svc.along_route(NORTH_LINE, corridor_m=600, call_budget=50)
    assert res.ok
    assert res.incidents == []


def test_service_provider_failure_is_honest(monkeypatch):
    def boom(url, params=None, timeout=None):
        raise Exception("connection refused")
    monkeypatch.setattr(tomtom_mod.httpx, "get", boom)
    svc = TomTomTraffic("svc-key-6", cache_ttl_s=300)
    res = svc.along_route(NORTH_LINE, corridor_m=600, call_budget=50)
    assert res.ok is False
    assert res.error
    assert res.incidents == [] and res.slowdowns == []


# ---------------- endpoint level ----------------

ROUTE_BODY = {
    "points": [[-83.6, 32.8], [-83.6, 32.95], [-83.6, 33.1], [-83.6, 33.25], [-83.6, 33.4]],
    "corridor_m": 600,
}


def test_endpoint_no_key_is_cleanly_disabled(client):
    # Test env sets no TOMTOM_API_KEY: the feature must be invisible.
    assert get_settings().TOMTOM_API_KEY == ""
    h = _mk(client, "tt1@examplemail.com")
    r = client.post("/api/traffic/along", headers=h, json=ROUTE_BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False
    assert body["available"] is False
    assert body["incidents"] == [] and body["slowdowns"] == []
    assert body["source"] == "tomtom"
    assert body["source_label"] == "Live traffic — TomTom"


def test_endpoint_with_key_returns_live_data(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "TOMTOM_API_KEY", "endpoint-key-1")
    monkeypatch.setattr(s, "TOMTOM_DAILY_CALL_CAP", 100000)
    fake = FakeTomTom(incidents=[_incident(-83.6, 33.1)])
    monkeypatch.setattr(tomtom_mod.httpx, "get", fake.get)
    h = _mk(client, "tt2@examplemail.com")
    r = client.post("/api/traffic/along", headers=h, json=ROUTE_BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["available"] is True
    assert body["source_label"] == "Live traffic — TomTom"
    assert body["daily_cap"] == 100000
    assert body["calls_today"] >= 1
    assert len(body["incidents"]) == 1
    it = body["incidents"][0]
    assert it["kind"] == "accident"
    assert it["description"] == "Accident, one lane closed"
    assert 15.0 < it["miles_along"] < 26.0  # lat 33.1 on a 32.8->33.4 line
    assert body["slowdowns"], "slow flow samples must surface as slowdowns"


def test_endpoint_over_daily_cap_degrades_without_spending(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "TOMTOM_API_KEY", "endpoint-key-2")
    monkeypatch.setattr(s, "TOMTOM_DAILY_CALL_CAP", 0)
    fake = FakeTomTom(incidents=[_incident(-83.6, 33.1)])
    monkeypatch.setattr(tomtom_mod.httpx, "get", fake.get)
    h = _mk(client, "tt3@examplemail.com")
    r = client.post("/api/traffic/along", headers=h, json=ROUTE_BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["available"] is False
    assert "daily free limit" in body["message"]
    assert body["incidents"] == [] and body["slowdowns"] == []
    assert fake.calls == [], "over-cap requests must not spend provider calls"
