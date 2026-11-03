"""Load profiles (v3.1): catalog/mapping units, HERE param plumbing from UN
hazmat class, /calculate honesty output, and OSRM lane-guidance parsing."""
import flexpolyline as fp
import pytest

from conftest import auth_headers, make_user

import services.dev as dev_mod
import services.here as here_mod
import services.loads as loads
from services.base import GeoPoint, TruckSpec


# ---------- catalog + mapping units ----------

def test_normalizers():
    assert loads.normalize_load_type("Oversize") == "oversize"
    assert loads.normalize_load_type("bogus") == "general"
    assert loads.normalize_load_type(None) == "general"
    assert loads.normalize_hazmat_class("3") == "3"
    assert loads.normalize_hazmat_class("42") is None
    assert loads.normalize_hazmat_class("") is None


def test_hazmat_class_maps_to_here_values():
    assert loads.here_hazmat_value("1", None) == "explosive"
    assert loads.here_hazmat_value("3", None) == "flammable"
    assert loads.here_hazmat_value("5", None) == "organic"
    assert loads.here_hazmat_value("9", None) == "other"
    assert loads.here_hazmat_value(None, "flammable") == "flammable"
    assert loads.here_hazmat_value(None, "diesel") is None
    assert loads.here_hazmat_value(None, None) is None


def test_special_and_permit_detection():
    plain = TruckSpec()
    assert loads.is_special_load(plain) is False
    assert loads.needs_permit(plain) is False
    os_ = TruckSpec(load_type="oversize")
    assert loads.is_special_load(os_) is True and loads.needs_permit(os_) is True
    heavy = TruckSpec(weight_kg=42000)
    assert loads.is_special_load(heavy) is True and loads.needs_permit(heavy) is True
    tall = TruckSpec(height_cm=430)
    assert loads.is_special_load(tall) is True
    hz = TruckSpec(load_type="hazmat", hazmat_class="3")
    assert loads.is_special_load(hz) is True and loads.needs_permit(hz) is False


def test_load_warning_honesty():
    os_ = TruckSpec(load_type="oversize", weight_kg=42000)
    w_dev = loads.load_warnings(os_, truck_safe=False)
    assert any("NOT enforced" in w for w in w_dev)
    assert any("NOT a permit route" in w for w in w_dev)
    # With an enforcing provider, no NOT-enforced claim — but permit caveat stays:
    # no routing provider issues state permits.
    w_here = loads.load_warnings(os_, truck_safe=True)
    assert not any("NOT enforced" in w for w in w_here)
    assert any("NOT a permit route" in w for w in w_here)
    assert loads.load_warnings(TruckSpec(), truck_safe=False) == []


# ---------- HERE adapter: UN class reaches the request ----------

class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _here_payload():
    coords = [(33.749, -84.388), (33.760, -84.390)]
    return {"routes": [{"sections": [{
        "polyline": fp.encode(coords),
        "summary": {"length": 1000, "duration": 100},
        "actions": [{"action": "depart", "instruction": "Go.", "offset": 0,
                     "length": 1000, "duration": 100}],
        "notices": [],
    }]}]}


def test_here_uses_hazmat_class(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = dict(params)
        return FakeResp(_here_payload())

    monkeypatch.setattr(here_mod.httpx, "get", fake_get)
    svc = here_mod.HereRouter("K")
    truck = TruckSpec(load_type="hazmat", hazmat_class="3")
    svc.calculate_route(GeoPoint(33.7, -84.3), GeoPoint(33.8, -84.4), truck=truck)
    assert captured["params"]["truck[shippedHazardousGoods]"] == "flammable"

    truck9 = TruckSpec(load_type="tanker", hazmat_class="9")
    svc.calculate_route(GeoPoint(33.7, -84.3), GeoPoint(33.8, -84.4), truck=truck9)
    assert captured["params"]["truck[shippedHazardousGoods]"] == "other"


# ---------- OSRM lane guidance parsing ----------

OSRM_LANES_PAYLOAD = {
    "code": "Ok",
    "routes": [{
        "distance": 5000, "duration": 600,
        "geometry": {"type": "LineString",
                     "coordinates": [[-84.4, 33.7], [-84.45, 33.75], [-84.5, 33.8]]},
        "legs": [{"steps": [
            {"distance": 100, "duration": 10, "name": "Main St",
             "maneuver": {"type": "depart", "modifier": "", "location": [-84.4, 33.7]},
             "intersections": [{"location": [-84.4, 33.7], "lanes": [
                 {"indications": ["left"], "valid": True},
                 {"indications": ["straight"], "valid": True}]}]},
            {"distance": 4900, "duration": 590, "name": "Peachtree St",
             "maneuver": {"type": "turn", "modifier": "left", "location": [-84.45, 33.75]},
             "intersections": [{"location": [-84.45, 33.75], "lanes": [
                 {"indications": ["left"], "valid": True},
                 {"indications": ["straight"], "valid": False},
                 {"indications": ["straight", "right"], "valid": False}]}]},
            {"distance": 0, "duration": 0, "name": "",
             "maneuver": {"type": "arrive", "modifier": "", "location": [-84.5, 33.8]}},
        ]}],
    }],
}


def test_osrm_lane_hint_derived_only_from_data(monkeypatch):
    monkeypatch.setattr(dev_mod.httpx, "get", lambda *a, **k: FakeResp(OSRM_LANES_PAYLOAD))
    res = dev_mod.OsrmRouter().calculate_route(GeoPoint(33.7, -84.4), GeoPoint(33.8, -84.5))
    ms = res.options[0].maneuvers
    assert ms[0].lane_hint == ""            # depart: no guidance
    assert ms[1].lane_hint == "Use the left lane"
    assert len(ms[1].lanes) == 3
    assert ms[2].lane_hint == ""            # arrive / no intersections


def test_osrm_no_lanes_no_hint(monkeypatch):
    payload = {
        "code": "Ok",
        "routes": [{
            "distance": 100, "duration": 10,
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            "legs": [{"steps": [
                {"distance": 100, "duration": 10, "name": "X",
                 "maneuver": {"type": "turn", "modifier": "right", "location": [0, 0]}},
            ]}],
        }],
    }
    monkeypatch.setattr(dev_mod.httpx, "get", lambda *a, **k: FakeResp(payload))
    res = dev_mod.OsrmRouter().calculate_route(GeoPoint(0, 0), GeoPoint(1, 1))
    m = res.options[0].maneuvers[0]
    assert m.lanes == [] and m.lane_hint == ""


# ---------- API: profile persists + /calculate reports enforcement honestly ----------

OSRM_SIMPLE = {
    "code": "Ok",
    "routes": [{
        "distance": 10000, "duration": 900,
        "geometry": {"type": "LineString", "coordinates": [[-84.4, 33.7], [-84.5, 33.8]]},
        "legs": [{"steps": [
            {"distance": 10000, "duration": 900, "name": "I-75",
             "maneuver": {"type": "depart", "modifier": "", "location": [-84.4, 33.7]}},
        ]}],
    }],
}


def test_truck_load_profile_roundtrip(client):
    make_user(client, "load1@examplemail.com")
    h = auth_headers(client, "load1@examplemail.com")
    r = client.post("/api/trucks", headers=h, json={
        "nickname": "Heavy Haul", "load_type": "oversize", "hazmat_class": "3",
        "weight_lbs": 95000,
    })
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["load_type"] == "oversize" and t["hazmat_class"] == "3"
    # invalid values normalize instead of storing garbage
    r2 = client.post("/api/trucks", headers=h, json={
        "nickname": "Odd", "load_type": "spaceship", "hazmat_class": "42"})
    assert r2.json()["load_type"] == "general"
    assert r2.json()["hazmat_class"] is None


def test_calculate_reports_load_enforcement(client, monkeypatch):
    monkeypatch.setattr(dev_mod.httpx, "get", lambda *a, **k: FakeResp(OSRM_SIMPLE))
    make_user(client, "load2@examplemail.com")
    h = auth_headers(client, "load2@examplemail.com")
    truck = client.post("/api/trucks", headers=h, json={
        "nickname": "OS", "load_type": "oversize", "weight_lbs": 95000}).json()
    r = client.post("/api/routes/calculate", headers=h, json={
        "origin": {"lat": 33.7, "lng": -84.4, "label": "A"},
        "destination": {"lat": 33.8, "lng": -84.5, "label": "B"},
        "truck_id": truck["id"], "alternatives": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truck_safe"] is False
    assert body["load"]["type"] == "oversize"
    assert body["load"]["enforced"] is False
    assert body["load"]["permit_required"] is True
    joined = " ".join(body["warnings"])
    assert "NOT enforced" in joined and "NOT a permit route" in joined

    plain = client.post("/api/trucks", headers=h, json={"nickname": "Van"}).json()
    r2 = client.post("/api/routes/calculate", headers=h, json={
        "origin": {"lat": 33.7, "lng": -84.4, "label": "A"},
        "destination": {"lat": 33.8, "lng": -84.5, "label": "B"},
        "truck_id": plain["id"], "alternatives": False,
    })
    body2 = r2.json()
    assert body2["load"]["special"] is False
    assert "NOT enforced" not in " ".join(body2["warnings"])
