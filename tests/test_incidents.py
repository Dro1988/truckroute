"""Incident reporting tests: report, dedupe, nearby feed, voting, expiry,
anti-spam. Plus per-leg routing info on the /calculate endpoint."""
from datetime import datetime, timedelta

import pytest

from conftest import auth_headers, make_user

import models


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    # The in-memory IP rate limiter is process-global; reset between tests
    # so incident tests don't throttle each other.
    from routers import _limits
    _limits._buckets.clear()
    yield
    _limits._buckets.clear()


def _mk(client, email):
    make_user(client, email)
    return auth_headers(client, email)


def test_report_and_nearby(client, db):
    h = _mk(client, "rep1@examplemail.com")
    r = client.post("/api/incidents", headers=h,
                    json={"kind": "police", "lat": 31.05, "lng": -82.75})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "police"
    assert body["emoji"] == "🚔"
    assert body["confirms"] == 1
    assert "Police reported ahead" in body["voice_text"]

    r = client.get("/api/incidents/near", headers=h,
                   params={"lat": 31.05, "lng": -82.75, "radius_m": 5000})
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["id"] == body["id"]

    # Far away: not in feed.
    r = client.get("/api/incidents/near", headers=h,
                   params={"lat": 40.0, "lng": -80.0, "radius_m": 5000})
    assert r.json() == []


def test_report_bad_kind_rejected(client):
    h = _mk(client, "rep2@examplemail.com")
    r = client.post("/api/incidents", headers=h,
                    json={"kind": "ufo", "lat": 31.0, "lng": -82.0})
    assert r.status_code == 400


def test_report_cooldown(client):
    h = _mk(client, "rep3@examplemail.com")
    r = client.post("/api/incidents", headers=h,
                    json={"kind": "hazard", "lat": 31.0, "lng": -82.0})
    assert r.status_code == 200
    # Same user, immediately again (different kind/location) -> 429.
    r = client.post("/api/incidents", headers=h,
                    json={"kind": "accident", "lat": 32.0, "lng": -83.0})
    assert r.status_code == 429


def test_dedupe_confirms_existing(client, db):
    h1 = _mk(client, "rep4a@examplemail.com")
    h2 = _mk(client, "rep4b@examplemail.com")
    r = client.post("/api/incidents", headers=h1,
                    json={"kind": "traffic_jam", "lat": 31.05, "lng": -82.75})
    first_id = r.json()["id"]
    # Second driver, same kind ~100m away -> confirms instead of new row.
    r = client.post("/api/incidents", headers=h2,
                    json={"kind": "traffic_jam", "lat": 31.0505, "lng": -82.7505})
    assert r.status_code == 200
    assert r.json()["id"] == first_id
    assert r.json()["confirms"] == 2
    assert db.query(models.IncidentReport).count() >= 1


def test_vote_confirm_and_deny(client, db):
    h1 = _mk(client, "rep5a@examplemail.com")
    h2 = _mk(client, "rep5b@examplemail.com")
    h3 = _mk(client, "rep5c@examplemail.com")
    h4 = _mk(client, "rep5d@examplemail.com")
    h5 = _mk(client, "rep5e@examplemail.com")
    r = client.post("/api/incidents", headers=h1,
                    json={"kind": "closure", "lat": 31.1, "lng": -82.8})
    iid = r.json()["id"]

    # Reporter can't vote on own report.
    r = client.post(f"/api/incidents/{iid}/confirm", headers=h1)
    assert r.status_code == 400

    r = client.post(f"/api/incidents/{iid}/confirm", headers=h2)
    assert r.status_code == 200
    assert r.json()["confirms"] == 2

    # Double vote rejected.
    r = client.post(f"/api/incidents/{iid}/confirm", headers=h2)
    assert r.status_code == 409

    # Three denies retire the report (denies 3 > confirms 2).
    for h in (h3, h4, h5):
        r = client.post(f"/api/incidents/{iid}/deny", headers=h)
        assert r.status_code == 200
    assert r.json()["denies"] == 3
    row = db.query(models.IncidentReport).filter_by(id=iid).first()
    assert row.active is False

    # Retired report no longer appears in nearby feed.
    r = client.get("/api/incidents/near", headers=h2,
                   params={"lat": 31.1, "lng": -82.8, "radius_m": 5000})
    assert all(i["id"] != iid for i in r.json())


def test_expiry_swept_on_near(client, db):
    h = _mk(client, "rep6@examplemail.com")
    r = client.post("/api/incidents", headers=h,
                    json={"kind": "hazard", "lat": 31.2, "lng": -82.9})
    iid = r.json()["id"]
    # Force expiry in the past.
    row = db.query(models.IncidentReport).filter_by(id=iid).first()
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()

    r = client.get("/api/incidents/near", headers=h,
                   params={"lat": 31.2, "lng": -82.9, "radius_m": 5000})
    assert all(i["id"] != iid for i in r.json())
    db.refresh(row)
    assert row.active is False


def test_truck_specific_types(client):
    h = _mk(client, "rep7@examplemail.com")
    for kind in ("weigh_open", "weigh_closed", "low_clearance", "no_parking"):
        r = client.post("/api/incidents", headers=h,
                        json={"kind": kind, "lat": 31.3, "lng": -83.0})
        # cooldown may trigger after the first; accept 200 or 429
        assert r.status_code in (200, 429), r.text


def test_calculate_returns_legs(client, monkeypatch):
    """Mocked OSRM with 2 legs (one waypoint) -> response carries per-leg info."""
    import services.dev as dev_mod

    payload = {
        "code": "Ok",
        "routes": [{
            "distance": 300000, "duration": 10800,
            "geometry": {"type": "LineString",
                         "coordinates": [[-84.4, 33.7], [-84.0, 32.5], [-83.5, 31.0]]},
            "legs": [
                {"distance": 200000, "duration": 7200, "steps": []},
                {"distance": 100000, "duration": 3600, "steps": []},
            ],
        }],
    }

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr(dev_mod.httpx, "get", lambda *a, **k: FakeResp())
    h = _mk(client, "legs@examplemail.com")
    r = client.post("/api/routes/calculate", headers=h, json={
        "origin": {"lat": 33.7, "lng": -84.4},
        "destination": {"lat": 31.0, "lng": -83.5},
        "waypoints": [{"lat": 32.5, "lng": -84.0, "label": "Stop 1"}],
        "alternatives": False,
    })
    assert r.status_code == 200, r.text
    opt = r.json()["options"][0]
    assert len(opt["legs"]) == 2
    total_leg_miles = sum(leg["distance_miles"] for leg in opt["legs"])
    assert abs(total_leg_miles - opt["distance_miles"]) < 0.2


# ---------- v3: incidents along a route corridor ----------

def _route_points():
    # Straight eastbound line near Homerville, GA: lng -82.80 -> -82.60
    # at lat 31.03. GeoJSON order [lng, lat]. ~0.2 deg lng ~ 19 km ~ 11.8 mi.
    return [[-82.80 + 0.02 * i, 31.03] for i in range(11)]


def test_along_route_finds_corridor_incidents(client, db):
    h = _mk(client, "along1@examplemail.com")
    # A traffic jam ON the line, about halfway (~5.9 mi along).
    r = client.post("/api/incidents", headers=h,
                    json={"kind": "traffic_jam", "lat": 31.0305, "lng": -82.70})
    assert r.status_code == 200, r.text
    jam_id = r.json()["id"]

    r = client.post("/api/incidents/along", headers=h,
                    json={"points": _route_points(), "corridor_m": 600})
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    it = items[0]
    assert it["id"] == jam_id
    assert it["slows_traffic"] is True
    assert 4.0 < it["miles_along"] < 8.0  # roughly halfway along ~11.8 mi


def test_along_route_excludes_off_corridor_and_marks_police_non_slowing(client, db):
    # Own latitude line (32.03): the test DB is session-shared, so corridors
    # must not overlap other tests' reports.
    pts = [[-82.80 + 0.02 * i, 32.03] for i in range(11)]
    h = _mk(client, "along2@examplemail.com")
    # Police ON the route (not a slowing kind)...
    r = client.post("/api/incidents", headers=h,
                    json={"kind": "police", "lat": 32.03, "lng": -82.65})
    assert r.status_code == 200, r.text
    # ...and an accident ~3 km north of the line (outside a 600 m corridor).
    h2 = _mk(client, "along2b@examplemail.com")
    r = client.post("/api/incidents", headers=h2,
                    json={"kind": "accident", "lat": 32.06, "lng": -82.70})
    assert r.status_code == 200, r.text

    r = client.post("/api/incidents/along", headers=h,
                    json={"points": pts, "corridor_m": 600})
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    assert items[0]["kind"] == "police"
    assert items[0]["slows_traffic"] is False


def test_along_route_requires_two_points(client):
    h = _mk(client, "along3@examplemail.com")
    r = client.post("/api/incidents/along", headers=h,
                    json={"points": [[-82.8, 31.03]], "corridor_m": 600})
    assert r.status_code == 422  # schema min_length=2
