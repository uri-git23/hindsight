from tests.conftest import auth_headers, make_series, train


def test_signup_login_me(client):
    r = client.post("/auth/signup", json={"email": "Me@Example.com", "password": "password123"})
    assert r.status_code == 201
    assert client.post("/auth/signup", json={"email": "me@example.com", "password": "password123"}).status_code == 409

    bad = client.post("/auth/token", data={"username": "me@example.com", "password": "wrong-password"})
    assert bad.status_code == 401

    headers = auth_headers(client, "me@example.com")
    assert client.get("/auth/me", headers=headers).json()["email"] == "me@example.com"


def test_requires_token(client):
    assert client.get("/series").status_code == 401
    assert client.get("/series", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_other_users_data_is_invisible(client):
    alice = auth_headers(client, "alice@example.com")
    bob = auth_headers(client, "bob@example.com")
    sid, _ = make_series(client, alice, [1.0, 2.0, 3.0])
    model = train(client, alice, sid)

    # 남의 리소스는 404 (존재 여부도 알려주지 않음)
    for path in (f"/series/{sid}", f"/series/{sid}/observations", f"/series/{sid}/leaderboard", f"/models/{model['id']}"):
        r = client.get(path, headers=bob)
        assert r.status_code == 404, path
    assert client.delete(f"/series/{sid}", headers=bob).status_code == 404
    assert client.post("/models", json={"series_id": sid, "name": "x", "method": "naive"}, headers=bob).status_code == 404
    assert client.post("/forecasts", json={"model_id": model["id"], "horizon": 1}, headers=bob).status_code == 404

    assert client.get("/series", headers=bob).json() == []
    assert client.get("/models", headers=bob).json() == []
    assert client.get(f"/series/{sid}", headers=alice).status_code == 200


def test_missing_series_is_404_with_code(client, auth):
    r = client.get("/series/999", headers=auth)
    assert r.status_code == 404
    assert r.json()["code"] == "series_not_found"
