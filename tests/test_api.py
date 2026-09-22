from fastapi.testclient import TestClient
from app.api import app


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_empty_download_batch_rejected():
    client = TestClient(app)
    response = client.post("/api/downloads", json={"resources": []})
    assert response.status_code == 400


def test_drm_download_batch_rejected():
    client = TestClient(app)
    response = client.post("/api/downloads", json={"resources": [{"url": "https://example.com/master.m3u8", "type": "playlist", "drm": True}]})
    assert response.status_code == 400
