"""Small, fail-closed client for the RunPod Serverless queue API."""

from __future__ import annotations

import json
import os
from urllib import error, request


class RunPodGatewayError(RuntimeError):
    """A safe-to-surface failure returned by the RunPod API."""


def _config() -> tuple[str, str, str]:
    api_key = os.getenv("RUNPOD_API_KEY", "").strip()
    endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID", "").strip()
    base_url = os.getenv("RUNPOD_API_BASE_URL", "https://api.runpod.ai/v2").rstrip("/")
    if not api_key or not endpoint_id:
        raise RunPodGatewayError("Geração de vídeo ainda não está configurada.")
    return api_key, endpoint_id, base_url


def _call(method: str, path: str, payload: dict | None = None) -> dict:
    api_key, endpoint_id, base_url = _config()
    body = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(
        f"{base_url}/{endpoint_id}/{path.lstrip('/')}",
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        # Never expose upstream bodies: they may contain signed URLs or internal details.
        raise RunPodGatewayError(f"RunPod recusou a solicitação (HTTP {exc.code}).") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RunPodGatewayError("RunPod está temporariamente indisponível.") from exc
    if not isinstance(result, dict):
        raise RunPodGatewayError("RunPod retornou uma resposta inválida.")
    return result


def submit(job_input: dict) -> dict:
    return _call("POST", "run", {"input": job_input, "policy": {"executionTimeout": 3600, "ttl": 7200}})


def status(job_id: str) -> dict:
    return _call("GET", f"status/{job_id}")


def cancel(job_id: str) -> dict:
    return _call("POST", f"cancel/{job_id}", {})
