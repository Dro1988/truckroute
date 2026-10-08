"""Truck profile tests: CRUD, active-truck logic, unit conversions, fuel range."""
from conftest import auth_headers, make_user


def _mk(client, email):
    make_user(client, email)
    return auth_headers(client, email)


def test_truck_crud_and_active_logic(client):
    h = _mk(client, "trucks1@examplemail.com")
    t1 = client.post("/api/trucks", headers=h, json={
        "nickname": "Big Red", "make": "Peterbilt", "model": "579", "year": 2025,
        "height_ft": 13, "height_in": 6, "width_in": 102, "length_ft": 72,
        "weight_lbs": 80000, "axle_count": 5,
        "fuel_capacity_gal": 240, "fuel_level_pct": 72, "avg_mpg": 7.2,
    }).json()
    assert t1["is_active"] is True  # first truck auto-active
    assert t1["fuel_range_miles"] == round(240 * 0.72 * 7.2, 1)  # 1244.2

    t2 = client.post("/api/trucks", headers=h, json={"nickname": "Little Blue"}).json()
    assert t2["is_active"] is False

    # switch active
    sw = client.put("/api/trucks/active", headers=h, json={"truck_id": t2["id"]}).json()
    assert sw["is_active"] is True
    active = client.get("/api/trucks/active", headers=h).json()
    assert active["id"] == t2["id"]

    # update
    up = client.put(f"/api/trucks/{t1['id']}", headers=h,
                    json={**{k: t1[k] for k in (
                        "nickname", "make", "model", "height_ft", "height_in", "width_in",
                        "length_ft", "weight_lbs", "axle_count", "fuel_capacity_gal",
                        "fuel_level_pct", "avg_mpg", "fuel_type")},
                          "nickname": "Big Red II"}).json()
    assert up["nickname"] == "Big Red II"

    # delete active -> another becomes active
    client.delete(f"/api/trucks/{t2['id']}", headers=h)
    active = client.get("/api/trucks/active", headers=h).json()
    assert active["id"] == t1["id"]


def test_truck_isolation_between_users(client):
    h1 = _mk(client, "iso1@examplemail.com")
    h2 = _mk(client, "iso2@examplemail.com")
    t = client.post("/api/trucks", headers=h1, json={"nickname": "Mine"}).json()
    assert client.get("/api/trucks", headers=h2).json() == []
    assert client.put(f"/api/trucks/{t['id']}", headers=h2, json={"nickname": "x"}).status_code == 404
    assert client.put("/api/trucks/active", headers=h2, json={"truck_id": t["id"]}).status_code == 404


def test_truck_unit_conversions():
    import sys
    sys.path.insert(0, "backend")
    import models
    t = models.Truck(height_ft=13, height_in=6, width_in=102, length_ft=72, weight_lbs=80000)
    assert t.height_cm() == 411  # 13'6" = 162in = 411.48cm
    assert t.width_cm() == 259   # 102in
    assert t.length_cm() == 2195  # 72ft = 864in
    assert t.weight_kg() == 36287  # 80000 lbs


def test_preferences_crud(client):
    h = _mk(client, "prefs1@examplemail.com")
    p = client.get("/api/preferences", headers=h).json()
    assert p["voice_enabled"] is True
    p2 = client.put("/api/preferences", headers=h, json={
        "available_drive_hours": 8.5, "break_interval_hours": 4, "map_style": "satellite"}).json()
    assert p2["available_drive_hours"] == 8.5
    assert p2["map_style"] == "satellite"
