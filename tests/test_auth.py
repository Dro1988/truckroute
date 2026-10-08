"""Auth flow tests."""
from conftest import auth_headers, make_user


def test_register_and_login(client):
    toks = make_user(client, "driver1@examplemail.com")
    assert toks["access_token"] and toks["refresh_token"]
    h = auth_headers(client, "driver1@examplemail.com")
    me = client.get("/api/auth/me", headers=h)
    assert me.status_code == 200
    assert me.json()["email"] == "driver1@examplemail.com"


def test_duplicate_register_rejected(client):
    make_user(client, "dup@examplemail.com")
    r = client.post("/api/auth/register", json={
        "email": "dup@examplemail.com", "password": "password123"})
    assert r.status_code == 400


def test_bad_login_rejected(client):
    r = client.post("/api/auth/login", json={"email": "nobody@examplemail.com", "password": "x" * 10})
    assert r.status_code == 401


def test_unauthenticated_me_rejected(client):
    assert client.get("/api/auth/me").status_code == 401


def test_refresh_flow(client):
    toks = make_user(client, "refresh@examplemail.com")
    r = client.post("/api/auth/refresh", json={"refresh_token": toks["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_profile_update_and_email_clash(client):
    make_user(client, "profa@examplemail.com")
    make_user(client, "profb@examplemail.com")
    h = auth_headers(client, "profa@examplemail.com")
    r = client.put("/api/auth/me", headers=h, json={"display_name": "Pro A"})
    assert r.status_code == 200 and r.json()["display_name"] == "Pro A"
    r = client.put("/api/auth/me", headers=h, json={"email": "profb@examplemail.com"})
    assert r.status_code == 400


def test_change_password(client):
    make_user(client, "pwchange@examplemail.com", password="oldpassword1")
    h = auth_headers(client, "pwchange@examplemail.com", password="oldpassword1")
    r = client.post("/api/auth/change-password", headers=h,
                    json={"current_password": "oldpassword1", "new_password": "newpassword2"})
    assert r.status_code == 200
    # old password no longer works, new one does
    assert client.post("/api/auth/login",
                       json={"email": "pwchange@examplemail.com", "password": "oldpassword1"}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"email": "pwchange@examplemail.com", "password": "newpassword2"}).status_code == 200


def test_export_and_delete(client):
    make_user(client, "exportme@examplemail.com")
    h = auth_headers(client, "exportme@examplemail.com")
    client.post("/api/trucks", headers=h, json={"nickname": "Big Red"})
    ex = client.get("/api/auth/export", headers=h)
    assert ex.status_code == 200
    data = ex.json()
    assert data["user"]["email"] == "exportme@examplemail.com"
    assert len(data["trucks"]) == 1
    d = client.delete("/api/auth/me", headers=h)
    assert d.status_code == 200
    assert client.post("/api/auth/login",
                       json={"email": "exportme@examplemail.com", "password": "password123"}).status_code == 401


def test_admin_flag_from_env(client):
    make_user(client, "admin@examplemail.com")
    h = auth_headers(client, "admin@examplemail.com")
    me = client.get("/api/auth/me", headers=h).json()
    assert me["is_admin"] is True
    # admin-only endpoint reachable
    r = client.get("/api/admin/health", headers=h)
    assert r.status_code == 200
    # non-admin blocked
    make_user(client, "pleb@examplemail.com")
    h2 = auth_headers(client, "pleb@examplemail.com")
    assert client.get("/api/admin/health", headers=h2).status_code == 403
