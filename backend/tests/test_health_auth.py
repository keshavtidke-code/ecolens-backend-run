from tests.conftest import register


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/health/ready").json()["status"] == "ready"


def test_register_login_refresh_me(client):
    headers = register(client)
    assert client.get("/api/v1/auth/me", headers=headers).json()["email"] == "a@example.com"

    dup = client.post("/api/v1/auth/register", json={"email": "A@example.com", "password": "supersecret1"})
    assert dup.status_code == 409 and dup.json()["code"] == "email_taken"

    bad = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong-pass"})
    assert bad.status_code == 401 and bad.json()["code"] == "invalid_credentials"

    ok = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "supersecret1"})
    tokens = ok.json()["tokens"]
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    # An access token must not work as a refresh token.
    wrong = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert wrong.status_code == 401


def test_anonymous_and_unauthenticated(client):
    anon = client.post("/api/v1/auth/anonymous").json()
    assert anon["user"]["is_anonymous"] is True
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["code"] == "missing_token"


def test_validation_error_shape(client):
    r = client.post("/api/v1/auth/register", json={"email": "nope", "password": "x"})
    body = r.status_code, r.json()
    assert body[0] == 422 and body[1]["code"] == "validation_error" and body[1]["details"]
    assert "request_id" in body[1]
