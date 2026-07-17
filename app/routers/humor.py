from __future__ import annotations

import os
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse

from app.services import dynamic_montage, humor_planner, humor_renderer, video_processor

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


def _find_montage(filename: str) -> Optional[str]:
    safe_name = os.path.basename(filename)
    if not safe_name.startswith("humor-preview-") or not safe_name.endswith(".mp4"):
        return None
    candidate = os.path.join(OUTPUT_DIR, safe_name)
    return candidate if os.path.exists(candidate) else None


@router.get("/source/{file_id}")
async def preview_source(file_id: str):
    input_path = _find_upload_by_id(file_id)
    if not input_path:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    return FileResponse(input_path, media_type="video/mp4", filename=f"{os.path.basename(file_id)}.mp4")


@router.post("/plan")
async def create_humor_plan(file_id: str = Form(...)):
    input_path = _find_upload_by_id(file_id)
    if not input_path:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado. Faça upload primeiro.")

    preview_filename = f"humor-preview-{uuid.uuid4().hex}.mp4"
    preview_path = os.path.join(OUTPUT_DIR, preview_filename)
    try:
        dynamic_montage.process_dynamic_video(
            input_path=input_path,
            output_path=preview_path,
            flip_horizontal=True,
            random_trim=True,
            crop_zoom=True,
            color_adjust=True,
            fade=True,
            strip_metadata=False,
            sensor_noise=2,
            crop_pixels=4,
            zoom_factor=1.02,
            hue_degrees=1.0,
            color_grade="cinematic",
            output_fps="29.97",
            smooth_motion=True,
            adaptive_sharpen=True,
            hard_cuts=True,
            speed_ramp=True,
            short_slowmo=True,
            short_speedup=True,
            freeze_frame=True,
            highlight_replay=True,
            quality_crf=18,
        )
        plan = humor_planner.build_humor_plan(preview_path)
    except video_processor.VideoProcessingError as exc:
        try:
            os.remove(preview_path)
        except FileNotFoundError:
            pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    plan["preview_filename"] = preview_filename
    plan["preview_url"] = f"/api/video/result/{preview_filename}"
    return plan


@router.post("/render")
async def render_humor_tutorial(
    file_id: str = Form(...),
    montage_filename: str = Form(...),
    script_json: str = Form(...),
    quality_crf: int = Form(18),
):
    if not _find_upload_by_id(file_id):
        raise HTTPException(status_code=404, detail="Arquivo original não encontrado.")
    montage_path = _find_montage(montage_filename)
    if not montage_path:
        raise HTTPException(status_code=404, detail="A montagem de prévia não foi encontrada.")
    if not 17 <= quality_crf <= 20:
        raise HTTPException(status_code=400, detail="A qualidade CRF deve estar entre 17 e 20.")

    output_filename = f"{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    started = time.monotonic()
    try:
        humor_renderer.render_captioned_video(
            input_path=montage_path,
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
