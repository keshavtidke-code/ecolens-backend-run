from tests.conftest import register, upload


def test_journal_crud(client, auth):
    obs = upload(client, auth).json()
    created = client.post(
        "/api/v1/journal", headers=auth, json={"observation_id": obs["id"], "title": "Pond edge"}
    )
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["observation"]["id"] == obs["id"]

    dup = client.post("/api/v1/journal", headers=auth, json={"observation_id": obs["id"], "title": "x"})
    assert dup.status_code == 409 and dup.json()["code"] == "journal_entry_exists"

    patched = client.patch(
        f"/api/v1/journal/{entry['id']}", headers=auth, json={"is_favorite": True, "personal_notes": "frogs"}
    ).json()
    assert patched["is_favorite"] is True and patched["personal_notes"] == "frogs"

    assert len(client.get("/api/v1/journal?is_favorite=true", headers=auth).json()["items"]) == 1
    assert len(client.get("/api/v1/journal?is_favorite=false", headers=auth).json()["items"]) == 0

    other = register(client, "b@example.com")
    assert client.get(f"/api/v1/journal/{entry['id']}", headers=other).status_code == 404
    assert client.post(
        "/api/v1/journal", headers=other, json={"observation_id": obs["id"], "title": "x"}
    ).status_code == 404

    assert client.delete(f"/api/v1/journal/{entry['id']}", headers=auth).status_code == 204


def test_chat_requires_analysis_and_ownership(client, auth):
    """Full chat behaviour lives in test_chat_api.py; this keeps the original access rules covered."""
    obs = upload(client, auth).json()
    url = f"/api/v1/observations/{obs['id']}/chat"
    assert client.get(url, headers=auth).json() == {"thread_id": None, "messages": []}

    not_analyzed = client.post(url, headers=auth, json={"message": "What plant is this?"})
    assert not_analyzed.status_code == 409 and not_analyzed.json()["code"] == "analysis_required"

    assert client.post(url, headers=auth, json={"message": "   "}).status_code == 422
    other = register(client, "b@example.com")
    assert client.get(url, headers=other).status_code == 404
