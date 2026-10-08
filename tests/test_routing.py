"""Routing service tests: OSRM adapter parsing (mocked HTTP) + HERE adapter
parsing with a real flexible-polyline round trip. Also the /calculate endpoint."""
import json

import flexpolyline as fp
import pytest

from conftest import auth_headers, make_user

import services.dev as dev_mod
import services.here as here_mod
from services.base import GeoPoint, TruckSpec


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


OSRM_PAYLOAD = {
    "code": "Ok",
    "routes": [{
        "distance": 650000, "duration": 23400,
        "geometry": {"type": "LineString", "coordinates": [[-84.4, 33.7], [-84.5, 33.8], [-84.6, 33.9]]},
        "legs": [{"steps": [
            {"distance": 1000, "duration": 60, "name": "I-75 N",
             "maneuver": {"type": "depart", "modifier": "", "location": [-84.4, 33.7]}},
            {"distance": 649000, "duration": 23340, "name": "I-75 N",
             "maneuver": {"type": "turn", "modifier": "slight right", "location": [-84.5, 33.8]}},
            {"distance": 0, "duration": 0, "name": "",
             "maneuver": {"type": "arrive", "modifier": "", "location": [-84.6, 33.9]}},
        ]}],
    }],
}


def test_osrm_adapter_parses(monkeypatch):
    monkeypatch.setattr(dev_mod.httpx, "get", lambda *a, **k: FakeResp(OSRM_PAYLOAD))
    svc = dev_mod.OsrmRouter()
    res = svc.calculate_route(GeoPoint(33.7, -84.4), GeoPoint(33.9, -84.6), truck=TruckSpec())
    assert res.provider == "dev-osrm"
    assert res.truck_safe is False  # must never claim truck safety
    assert len(res.options) == 1
    opt = res.options[0]
    assert opt.distance_m == 650000
    assert opt.shape["coordinates"][0] == [-84.4, 33.7]
    assert len(opt.maneuvers) == 3
    assert "right" in opt.maneuvers[1].instruction.lower()
    assert any("dev routing" in w.lower() for w in res.warnings)


def test_osrm_adapter_handles_failure(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(dev_mod.httpx, "get", boom)
    svc = dev_mod.OsrmRouter()
    res = svc.calculate_route(GeoPoint(0, 0), GeoPoint(1, 1))
    assert res.options == [] and res.warnings


def test_nominatim_adapter_parses(monkeypatch):
    payload = [{"display_name": "Atlanta, Fulton County, Georgia, USA", "lat": "33.749",
                "lon": "-84.388", "class": "place"}]
    monkeypatch.setattr(dev_mod.httpx, "get", lambda *a, **k: FakeResp(payload))
    svc = dev_mod.NominatimGeocoder()
    out = svc.suggest("atlanta")
    assert len(out) == 1 and out[0].lat == pytest.approx(33.749)
    assert svc.suggest("a") == []  # too short


def _here_payload():
    # Real flexible-polyline round trip: encode known coords, build a HERE-shaped response.
    coords = [(33.749, -84.388), (33.760, -84.390), (33.775, -84.395)]
    poly = fp.encode(coords)
    return {
        "routes": [{
            "sections": [{
                "polyline": poly,
                "summary": {"length": 5200, "duration": 420},
                "actions": [
                    {"action": "depart", "instruction": "Head north.", "offset": 0, "length": 3000, "duration": 240},
                    {"action": "turn", "instruction": "Turn right onto Peachtree St.", "offset": 2, "length": 2200, "duration": 180},
                ],
                "notices": [],
            }]
        }]
    }


def test_here_adapter_parses_truck_params(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = dict(params)
        return FakeResp(_here_payload())

    monkeypatch.setattr(here_mod.httpx, "get", fake_get)
    svc = here_mod.HereRouter("KEY123")
    truck = TruckSpec(height_cm=411, width_cm=259, length_cm=2195, weight_kg=36287,
                      axle_count=5, trailer_count=1, hazmat="flammable")
    res = svc.calculate_route(GeoPoint(33.749, -84.388), GeoPoint(33.775, -84.395), truck=truck)
    p = captured["params"]
    assert captured["url"].startswith("https://router.hereapi.com/v8/routes")
    assert p["transportMode"] == "truck"
    assert p["truck[height]"] == "411" and p["truck[grossWeight]"] == "36287"
    assert p["truck[axleCount]"] == "5" and p["truck[shippedHazardousGoods]"] == "flammable"
    assert p["apiKey"] == "KEY123"
    assert res.truck_safe is True
    opt = res.options[0]
    assert opt.distance_m == 5200
    # polyline decoded back to the original coords (GeoJSON [lng, lat])
    assert opt.shape["coordinates"][0] == [-84.388, 33.749]
    assert len(opt.maneuvers) == 2
    assert opt.maneuvers[1].instruction == "Turn right onto Peachtree St."
    assert opt.maneuvers[1].lat == pytest.approx(33.775)


def test_here_adapter_flags_restriction_notices(monkeypatch):
    payload = _here_payload()
    payload["routes"][0]["sections"][0]["notices"] = [
        {"code": "violatedVehicleRestriction", "title": "Vehicle restriction violated"}]
    monkeypatch.setattr(here_mod.httpx, "get", lambda *a, **k: FakeResp(payload))
    res = here_mod.HereRouter("K").calculate_route(GeoPoint(0, 0), GeoPoint(1, 1))
    assert res.truck_safe is False
    assert any("could not fully honor" in w for w in res.warnings)


def test_here_geocoder_parses(monkeypatch):
    payload = {"items": [{"title": "Atlanta", "address": {"label": "Atlanta, GA, USA"},
                          "position": {"lat": 33.749, "lng": -84.388}, "resultType": "locality"}]}
    monkeypatch.setattr(here_mod.httpx, "get", lambda *a, **k: FakeResp(payload))
    out = here_mod.HereGeocoder("K").geocode("atlanta")
    assert out[0].label == "Atlanta" and out[0].lat == pytest.approx(33.749)


def test_calculate_endpoint_end_to_end(client, monkeypatch):
    monkeypatch.setattr(dev_mod.httpx, "get", lambda *a, **k: FakeResp(OSRM_PAYLOAD))
    make_user(client, "route1@examplemail.com")
    h = auth_headers(client, "route1@examplemail.com")
    truck = client.post("/api/trucks", headers=h, json={"nickname": "Rig"}).json()
    r = client.post("/api/routes/calculate", headers=h, json={
        "origin": {"lat": 33.7, "lng": -84.4, "label": "Atlanta"},
        "destination": {"lat": 33.9, "lng": -84.6, "label": "Marietta"},
        "truck_id": truck["id"], "alternatives": False, "save_trip": True, "trip_name": "Test run",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "dev-osrm"
    assert body["truck_safe"] is False
    assert len(body["options"]) == 1
    opt = body["options"][0]
    assert opt["distance_miles"] == pytest.approx(650000 / 1609.344, rel=0.01)
    assert opt["est_fuel_gal"] == pytest.approx(opt["distance_miles"] / 7.0, rel=0.01)
    assert body["route_id"]
    assert body["trip_id"]
    assert body["hos"]["drive_hours"] == pytest.approx(opt["duration_min"] / 60, rel=0.01)
    # usage was tracked
    u = client.get("/api/admin/usage?days=1", headers=auth_headers(client, "admin@examplemail.com")).json()
    assert u["total_calls"] >= 1
    # saved trip exists
    trips = client.get("/api/trips", headers=h).json()
    assert any(t["id"] == body["trip_id"] for t in trips)
