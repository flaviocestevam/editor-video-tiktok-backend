from __future__ import annotations

import json
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
API_VERSION = "combined-humor-v4"


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


def _find_output_video(filename: str) -> Optional[str]:
    """Localiza com segurança um MP4 já produzido por /api/video/process."""
    safe_name = os.path.basename(filename)
    if safe_name != filename or not safe_name.lower().endswith(".mp4"):
        return None
    candidate = os.path.join(OUTPUT_DIR, safe_name)
    return candidate if os.path.isfile(candidate) else None


@router.get("/source/{file_id}")
async def preview_source(file_id: str):
    input_path = _find_upload_by_id(file_id)
    if not input_path:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    return FileResponse(input_path, media_type="video/mp4", filename=f"{os.path.basename(file_id)}.mp4")


@router.post("/caption-plan")
async def create_caption_plan(montage_filename: str = Form(...)):
    """Sugere frases sobre o vídeo que já recebeu as 19 opções de edição."""
    montage_path = _find_output_video(montage_filename)
    if not montage_path:
        raise HTTPException(status_code=404, detail="O vídeo editado não foi encontrado.")

    try:
        plan = humor_planner.build_humor_plan(montage_path)
    except video_processor.VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    plan.update({
        "preview_filename": os.path.basename(montage_path),
        "preview_url": f"/api/video/result/{os.path.basename(montage_path)}",
        "api_version": API_VERSION,
        "uses_existing_edit": True,
    })
    return plan


@router.post("/caption-render")
async def render_captions_on_existing_output(
    montage_filename: str = Form(...),
    script_json: str = Form(...),
    quality_crf: int = Form(18),
):
    """Grava somente as frases no MP4 já editado, sem refazer a montagem."""
    montage_path = _find_output_video(montage_filename)
    if not montage_path:
        raise HTTPException(status_code=404, detail="O vídeo editado não foi encontrado.")
    if not 17 <= quality_crf <= 20:
        raise HTTPException(status_code=400, detail="A qualidade CRF deve estar entre 17 e 20.")

    try:
        submitted = json.loads(script_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Roteiro de frases inválido.") from exc
    if not isinstance(submitted, list):
        raise HTTPException(status_code=400, detail="O roteiro de frases precisa ser uma lista.")

    enabled_count = sum(
        1 for item in submitted
        if isinstance(item, dict)
        and bool(item.get("enabled", True))
        and str(item.get("text") or item.get("selected_text") or "").strip()
    )
    if enabled_count <= 0:
        raise HTTPException(status_code=400, detail="Ative ao menos uma frase antes de finalizar.")

    output_filename = f"{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    started = time.monotonic()
    try:
        render_info = humor_renderer.render_captioned_video(
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
        "mode": "legendas_sobre_video_ja_editado",
        "processing_seconds": round(time.monotonic() - started, 2),
        "api_version": API_VERSION,
        "source_montage_filename": os.path.basename(montage_path),
        "caption_count": enabled_count,
        **render_info,
    }


@router.post("/plan")
async def create_humor_plan(
    file_id: str = Form(...),
    remove_audio: bool = Form(True),
    flip_horizontal: bool = Form(True),
    random_trim: bool = Form(True),
    crop_zoom: bool = Form(True),
    speed_change: bool = Form(True),
    color_adjust: bool = Form(True),
    fade: bool = Form(True),
    strip_metadata: bool = Form(True),
    sensor_noise: int = Form(2),
    crop_pixels: int = Form(4),
    zoom_factor: float = Form(1.02),
    hue_degrees: float = Form(1.0),
    color_grade: str = Form("cinematic"),
    output_fps: str = Form("29.97"),
    smooth_motion: bool = Form(True),
    adaptive_sharpen: bool = Form(True),
    dynamic_montage_enabled: bool = Form(True),
    hard_cuts: bool = Form(True),
    speed_ramp: bool = Form(True),
    short_slowmo: bool = Form(True),
    short_speedup: bool = Form(True),
    freeze_frame: bool = Form(True),
    highlight_replay: bool = Form(True),
    quality_crf: int = Form(18),
):
    """Compatibilidade com o fluxo anterior que cria a montagem dentro do tutorial."""
    input_path = _find_upload_by_id(file_id)
    if not input_path:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado. Faça upload primeiro.")

    preview_filename = f"humor-preview-{uuid.uuid4().hex}.mp4"
    preview_path = os.path.join(OUTPUT_DIR, preview_filename)
    try:
        if dynamic_montage_enabled:
            dynamic_montage.process_dynamic_video(
                input_path=input_path,
                output_path=preview_path,
                flip_horizontal=flip_horizontal,
                random_trim=random_trim,
                crop_zoom=crop_zoom,
                color_adjust=color_adjust,
                fade=fade,
                strip_metadata=strip_metadata,
                sensor_noise=sensor_noise,
                crop_pixels=crop_pixels,
                zoom_factor=zoom_factor,
                hue_degrees=hue_degrees,
                color_grade=color_grade,
                output_fps=output_fps,
                smooth_motion=smooth_motion,
                adaptive_sharpen=adaptive_sharpen,
                hard_cuts=hard_cuts,
                speed_ramp=speed_change and speed_ramp,
                short_slowmo=speed_change and short_slowmo,
                short_speedup=speed_change and short_speedup,
                freeze_frame=freeze_frame,
                highlight_replay=highlight_replay,
                quality_crf=quality_crf,
            )
            audio_removed = True
        else:
            video_processor.process_video(
                input_path=input_path,
                output_path=preview_path,
                temp_dir=TEMP_DIR,
                remove_audio=remove_audio,
                flip_horizontal=flip_horizontal,
                random_trim=random_trim,
                crop_zoom=crop_zoom,
                speed_change=speed_change,
                color_adjust=color_adjust,
                fade=fade,
                strip_metadata=strip_metadata,
                sensor_noise=sensor_noise,
                crop_pixels=crop_pixels,
                zoom_factor=zoom_factor,
                hue_degrees=hue_degrees,
                color_grade=color_grade,
                output_fps=output_fps,
                smooth_motion=smooth_motion,
                adaptive_sharpen=adaptive_sharpen,
                quality_crf=quality_crf,
            )
            audio_removed = remove_audio
        plan = humor_planner.build_humor_plan(preview_path)
    except video_processor.VideoProcessingError as exc:
        try:
            os.remove(preview_path)
        except FileNotFoundError:
            pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    plan.update({
        "preview_filename": preview_filename,
        "preview_url": f"/api/video/result/{preview_filename}",
        "combined_flow": True,
        "api_version": API_VERSION,
        "audio_removed": audio_removed,
        "dynamic_montage_enabled": dynamic_montage_enabled,
    })
    return plan


@router.post("/render")
async def render_humor_tutorial(
    file_id: str = Form(...),
    montage_filename: str = Form(...),
    script_json: str = Form(...),
    quality_crf: int = Form(18),
):
    """Compatibilidade com o fluxo anterior de prévia exclusiva do tutorial."""
    if not _find_upload_by_id(file_id):
        raise HTTPException(status_code=404, detail="Arquivo original não encontrado.")
    montage_path = _find_montage(montage_filename)
    if not montage_path:
        raise HTTPException(status_code=404, detail="A montagem de prévia não foi encontrada.")
    if not 17 <= quality_crf <= 20:
        raise HTTPException(status_code=400, detail="A qualidade CRF deve estar entre 17 e 20.")

    try:
        submitted = json.loads(script_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Roteiro de frases inválido.") from exc
    requested_count = len(submitted) if isinstance(submitted, list) else 0
    if requested_count <= 0:
        raise HTTPException(status_code=400, detail="Nenhuma frase foi enviada para o vídeo final.")

    output_filename = f"{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    started = time.monotonic()
    try:
        render_info = humor_renderer.render_captioned_video(
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
        "mode": "montagem_dinamica_com_tutorial_humor",
        "processing_seconds": round(time.monotonic() - started, 2),
        "api_version": API_VERSION,
        "requested_caption_count": requested_count,
        **render_info,
    }
