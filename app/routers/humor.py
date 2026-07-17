from __future__ import annotations

import os
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Form, HTTPException

from app.services import humor_planner, humor_renderer, video_processor

router = APIRouter()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
UPLOAD_DIR = os.path.join(STORAGE_DIR, "uploads")
OUTPUT_DIR = os.path.join(STORAGE_DIR, "outputs")
TEMP_DIR = os.path.join(STORAGE_DIR, "temp")


def _find_upload_by_id(file_id: str) -> Optional[str]:
    safe_id = os.path.basename(file_id)
    candidate = os.path.join(UPLOAD_DIR, f"{safe_id}.mp4")
    return candidate if os.path.exists(candidate) else None


@router.post("/plan")
async def create_humor_plan(file_id: str = Form(...)):
    input_path = _find_upload_by_id(file_id)
    if not input_path:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado. Faça upload primeiro.")
    try:
        return humor_planner.build_humor_plan(input_path)
    except video_processor.VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/render")
async def render_humor_tutorial(
    file_id: str = Form(...),
    script_json: str = Form(...),
    quality_crf: int = Form(18),
):
    input_path = _find_upload_by_id(file_id)
    if not input_path:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado. Faça upload primeiro.")
    if not 17 <= quality_crf <= 20:
        raise HTTPException(status_code=400, detail="A qualidade CRF deve estar entre 17 e 20.")

    output_filename = f"{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    started = time.monotonic()
    try:
        humor_renderer.render_humor_video(
            input_path=input_path,
            output_path=output_path,
            temp_dir=TEMP_DIR,
            script_json=script_json,
            quality_crf=quality_crf,
        )
    except video_processor.VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "output_filename": output_filename,
        "download_url": f"/api/video/result/{output_filename}",
        "mode": "tutorial_humor",
        "processing_seconds": round(time.monotonic() - started, 2),
    }
