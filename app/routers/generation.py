"""Authenticated gateway between the Lovable server and RunPod Serverless."""

from __future__ import annotations

import os
import re
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

from app.services import runpod_gateway


router = APIRouter()
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def require_gateway_key(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("VIDEO_FACTORY_API_KEY", "")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Não autorizado.")


class GenerationRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=128)
    mode: Literal["keep_original_audio", "new_audio_lipsync", "photo_to_talking_video"]
    character_image_url: AnyHttpUrl
    reference_video_url: AnyHttpUrl | None = None
    new_audio_url: AnyHttpUrl | None = None
    audio_url: AnyHttpUrl | None = None
    text: str | None = Field(default=None, min_length=1, max_length=1000)
    language: Literal["pt-BR", "es", "en"] | None = None
    gender: Literal["male", "female"] | None = None
    width: int | None = Field(default=None, ge=256, le=1280)
    height: int | None = Field(default=None, ge=256, le=1280)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    audio_cfg: int | None = Field(default=None, ge=0, le=2)

    @model_validator(mode="after")
    def validate_mode_fields(self):
        if not JOB_ID.fullmatch(self.job_id):
            raise ValueError("job_id contém caracteres inválidos")
        if self.mode == "photo_to_talking_video":
            if not self.audio_url and not all((self.text, self.language, self.gender)):
                raise ValueError("informe audio_url ou texto, idioma e voz")
        else:
            if not self.reference_video_url:
                raise ValueError("reference_video_url é obrigatório")
            if self.mode == "new_audio_lipsync" and not self.new_audio_url:
                raise ValueError("new_audio_url é obrigatório")
        return self


@router.post("/jobs", dependencies=[Depends(require_gateway_key)], status_code=202)
def create_job(payload: GenerationRequest):
    try:
        result = runpod_gateway.submit(payload.model_dump(mode="json", exclude_none=True))
    except runpod_gateway.RunPodGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    job_id = result.get("id")
    if not job_id:
        raise HTTPException(status_code=502, detail="RunPod não retornou o identificador do trabalho.")
    return {"id": job_id, "status": result.get("status", "IN_QUEUE")}


@router.get("/jobs/{job_id}", dependencies=[Depends(require_gateway_key)])
def get_job(job_id: str):
    if not JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=422, detail="Identificador inválido.")
    try:
        result = runpod_gateway.status(job_id)
    except runpod_gateway.RunPodGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {key: result.get(key) for key in ("id", "status", "output", "error") if result.get(key) is not None}


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_gateway_key)])
def cancel_job(job_id: str):
    if not JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=422, detail="Identificador inválido.")
    try:
        result = runpod_gateway.cancel(job_id)
    except runpod_gateway.RunPodGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"id": result.get("id", job_id), "status": result.get("status", "CANCELLED")}
