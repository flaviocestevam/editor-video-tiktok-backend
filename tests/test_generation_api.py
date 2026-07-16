from fastapi.testclient import TestClient

from app.services import runpod_gateway
from main import app


client = TestClient(app)
AUTH = {"Authorization": "Bearer test-gateway-key"}
VALID = {
    "job_id": "generation_123",
    "mode": "photo_to_talking_video",
    "character_image_url": "https://assets.example/avatar.png?token=signed",
    "text": "Olá, este é um teste.",
    "language": "pt-BR",
    "gender": "female",
}


def test_generation_requires_configured_auth(monkeypatch):
    monkeypatch.delenv("VIDEO_FACTORY_API_KEY", raising=False)
    assert client.post("/api/generation/jobs", json=VALID).status_code == 401


def test_generation_rejects_wrong_auth(monkeypatch):
    monkeypatch.setenv("VIDEO_FACTORY_API_KEY", "correct")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    assert client.post("/api/generation/jobs", json=VALID, headers=AUTH).status_code == 401


def test_generation_validates_mode_fields(monkeypatch):
    monkeypatch.setenv("VIDEO_FACTORY_API_KEY", "test-gateway-key")
    invalid = {**VALID, "text": None, "language": None, "gender": None}
    assert client.post("/api/generation/jobs", json=invalid, headers=AUTH).status_code == 422


def test_generation_submits_only_validated_payload(monkeypatch):
    monkeypatch.setenv("VIDEO_FACTORY_API_KEY", "test-gateway-key")
    captured = {}

    def fake_submit(payload):
        captured.update(payload)
        return {"id": "runpod-job-1", "status": "IN_QUEUE"}

    monkeypatch.setattr(runpod_gateway, "submit", fake_submit)
    response = client.post("/api/generation/jobs", json=VALID, headers=AUTH)
    assert response.status_code == 202
    assert response.json() == {"id": "runpod-job-1", "status": "IN_QUEUE"}
    assert captured["mode"] == "photo_to_talking_video"


def test_generation_status_and_cancel(monkeypatch):
    monkeypatch.setenv("VIDEO_FACTORY_API_KEY", "test-gateway-key")
    monkeypatch.setattr(runpod_gateway, "status", lambda job_id: {"id": job_id, "status": "COMPLETED", "output": {"output_video_url": "https://assets.example/video.mp4"}})
    monkeypatch.setattr(runpod_gateway, "cancel", lambda job_id: {"id": job_id, "status": "CANCELLED"})
    status = client.get("/api/generation/jobs/job-1", headers=AUTH)
    assert status.status_code == 200
    assert status.json()["status"] == "COMPLETED"
    cancelled = client.post("/api/generation/jobs/job-1/cancel", headers=AUTH)
    assert cancelled.json() == {"id": "job-1", "status": "CANCELLED"}


def test_gateway_fails_closed_without_runpod_config(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    try:
        runpod_gateway.submit(VALID)
    except runpod_gateway.RunPodGatewayError as exc:
        assert "não está configurada" in str(exc)
    else:
        raise AssertionError("gateway must fail closed")
