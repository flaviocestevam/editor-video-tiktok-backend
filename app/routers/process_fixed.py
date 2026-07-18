import logging
import os
import time
import uuid

from fastapi import APIRouter, Form, HTTPException

from app.routers import video as legacy_video
from app.services import dynamic_montage, video_processor

logger = logging.getLogger("editor_video_tiktok.process_fixed")
router = APIRouter()


@router.post("/process")
async def process_video_fixed(
    file_id: str = Form(...),
    remove_audio: bool = Form(False),
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
    dynamic_montage_enabled: bool = Form(False),
    hard_cuts: bool = Form(True),
    speed_ramp: bool = Form(True),
    short_slowmo: bool = Form(True),
    short_speedup: bool = Form(True),
    freeze_frame: bool = Form(True),
    highlight_replay: bool = Form(True),
    dynamic_reframe: bool = Form(True),
    animated_grain_overlay: bool = Form(True),
    scene_color_variation: bool = Form(True),
    light_texture_overlay: bool = Form(True),
    remove_text_overlays: bool = Form(True),
    quality_crf: int = Form(18),
):
    input_path = legacy_video._find_upload_by_id(file_id)
    if not input_path:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado. Faça upload ou download primeiro.")

    output_filename = f"{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(legacy_video.OUTPUT_DIR, output_filename)
    started = time.monotonic()

    try:
        if dynamic_montage_enabled:
            report = dynamic_montage.process_dynamic_video(
                input_path=input_path,
                output_path=output_path,
                remove_audio=remove_audio,
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
                dynamic_reframe=dynamic_reframe,
                animated_grain_overlay=animated_grain_overlay,
                scene_color_variation=scene_color_variation,
                light_texture_overlay=light_texture_overlay,
                remove_text_overlays=remove_text_overlays,
                quality_crf=quality_crf,
            )
        else:
            video_processor.process_video(
                input_path=input_path,
                output_path=output_path,
                temp_dir=legacy_video.TEMP_DIR,
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
            report = {
                "engine": "standard_processor",
                "attempt": 1,
                "compatibility_mode": 0,
                "applied_effects": {
                    "remove_audio": remove_audio,
                    "flip_horizontal": flip_horizontal,
                    "random_trim": random_trim,
                    "crop_zoom": crop_zoom,
                    "speed_change": speed_change,
                    "color_adjust": color_adjust,
                    "fade_in_out": fade,
                    "custom_metadata": strip_metadata,
                    "sensor_noise": bool(sensor_noise),
                    "output_29_97_fps": output_fps == "29.97",
                    "smooth_motion": smooth_motion,
                    "adaptive_sharpen": adaptive_sharpen,
                    "text_bands_removed": False,
                },
                "warnings": [],
            }
    except video_processor.VideoProcessingError as exc:
        try:
            os.remove(output_path)
        except FileNotFoundError:
            pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    report["processing_seconds"] = round(time.monotonic() - started, 2)
    logger.info("Processamento corrigido concluído: %s", output_filename)
    return {
        "output_filename": output_filename,
        "download_url": f"/api/video/result/{output_filename}",
        "dynamic_montage": dynamic_montage_enabled,
        "processing_report": report,
    }
