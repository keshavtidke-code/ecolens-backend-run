from pathlib import Path

from app.core.settings import get_settings
from tests.conftest import JPEG, register, upload


def test_create_get_list_delete(client, auth):
    r = upload(client, auth, user_note="mossy wall")
    assert r.status_code == 201, r.text
    obs = r.json()
    assert obs["status"] == "pending" and obs["photo_url"].endswith(f"/{obs['id']}/photo")
    assert obs["analysis"] is None and obs["challenges"] == []

    assert client.get(f"/api/v1/observations/{obs['id']}", headers=auth).status_code == 200
    page = client.get("/api/v1/observations", headers=auth).json()
    assert len(page["items"]) == 1 and page["next_cursor"] is None
    photo = client.get(obs["photo_url"], headers=auth)
    assert photo.status_code == 200 and photo.content == JPEG

    stored = list(Path(get_settings().storage_dir).rglob("*.jpg"))
    assert len(stored) == 1
    assert client.delete(f"/api/v1/observations/{obs['id']}", headers=auth).status_code == 204
    assert not stored[0].exists()
    assert client.get(f"/api/v1/observations/{obs['id']}", headers=auth).status_code == 404


def test_pagination(client, auth):
    for _ in range(3):
        assert upload(client, auth).status_code == 201
    first = client.get("/api/v1/observations?limit=2", headers=auth).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = client.get(f"/api/v1/observations?limit=2&cursor={first['next_cursor']}", headers=auth).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None


def test_upload_validation(client, auth):
    assert upload(client, auth, data=b"not an image").status_code == 415
    assert upload(client, auth, data=b"").status_code == 400
    assert upload(client, auth, latitude="123").status_code == 422


def test_ownership_isolated(client, auth):
    obs = upload(client, auth).json()
    other = register(client, "b@example.com")
    assert client.get(f"/api/v1/observations/{obs['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/observations/{obs['id']}", headers=other).status_code == 404


def test_analyze_without_api_key_is_503(client, auth):
    obs = upload(client, auth).json()
    r = client.post(f"/api/v1/observations/{obs['id']}/analyze", headers=auth)
    assert r.status_code == 503 and r.json()["code"] == "ai_not_configured"


def test_account_deletion_removes_everything(client, auth):
    upload(client, auth)
    assert client.delete("/api/v1/auth/me", headers=auth).status_code == 204
    assert not list(Path(get_settings().storage_dir).rglob("*.jpg"))
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 401
