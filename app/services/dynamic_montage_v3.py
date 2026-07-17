from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from . import dynamic_montage as legacy
from . import dynamic_montage_v2 as v2
from . import video_processor

logger = logging.getLogger(__name__)


def _normalise_rotation(value: Any) -> int:
    """Normaliza metadados de rotação para 0, 90, 180 ou 270 graus."""
    try:
        rotation = float(value)
    except (TypeError, ValueError):
        return 0
    return int(round(rotation / 90.0) * 90) % 360


def _probe_rotation(input_path: str) -> int:
    result = video_processor._run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream_tags=rotate:stream_side_data=rotation",
            "-of",
            "json",
            input_path,
        ],
        timeout=30,
    )
    if result.returncode != 0:
        return 0
    try:
        data = json.loads(result.stdout)
        stream = (data.get("streams") or [{}])[0]
        tag_rotation = (stream.get("tags") or {}).get("rotate")
        if tag_rotation is not None:
            return _normalise_rotation(tag_rotation)
        for side_data in stream.get("side_data_list") or []:
            if side_data.get("rotation") is not None:
                return _normalise_rotation(side_data.get("rotation"))
    except (IndexError, TypeError, ValueError, json.JSONDecodeError):
        return 0
    return 0


def _orientation_filters(rotation: int) -> list[str]:
    if rotation == 90:
        return ["transpose=clock"]
    if rotation == 180:
        return ["hflip", "vflip"]
    if rotation == 270:
        return ["transpose=cclock"]
    return []


def _display_dimensions(width: int, height: int, rotation: int) -> tuple[int, int]:
    if rotation in {90, 270}:
        width, height = height, width
    width = max(2, width - (width % 2))
    height = max(2, height - (height % 2))
    return width, height


def _spatial_filters(
    *,
    rotation: int,
    width: int,
    height: int,
    output_duration: float,
    flip_horizontal: bool,
    crop_zoom: bool,
    crop_pixels: int,
    zoom_factor: float,
    color_adjust: bool,
    hue_degrees: float,
    color_grade: str,
    smooth_motion: bool,
    adaptive_sharpen: bool,
    sensor_noise: int,
    safe_mode: int,
) -> list[str]:
    filters = _orientation_filters(rotation)

    if safe_mode == 0:
        filters.extend(
            legacy._spatial_filters(
                source_width=width,
                source_height=height,
                output_duration=output_duration,
                flip_horizontal=flip_horizontal,
                crop_zoom=crop_zoom,
                crop_pixels=crop_pixels,
                zoom_factor=zoom_factor,
                color_adjust=color_adjust,
                hue_degrees=hue_degrees,
                color_grade=color_grade,
                smooth_motion=smooth_motion,
                adaptive_sharpen=adaptive_sharpen,
                sensor_noise=sensor_noise,
            )
        )
    elif safe_mode == 1:
        filters.extend(
            legacy._spatial_filters(
                source_width=width,
                source_height=height,
                output_duration=output_duration,
                flip_horizontal=flip_horizontal,
                crop_zoom=crop_zoom,
                crop_pixels=crop_pixels,
                zoom_factor=zoom_factor,
                color_adjust=color_adjust,
                hue_degrees=hue_degrees,
                color_grade=color_grade,
                smooth_motion=False,
                adaptive_sharpen=adaptive_sharpen,
                sensor_noise=0,
            )
        )
    else:
        if flip_horizontal:
            filters.append("hflip")
        if crop_zoom or crop_pixels or zoom_factor > 1.0:
            ratio = 1.0 / max(zoom_factor, 1.0)
            filters.extend(
                [
                    "crop="
                    f"trunc(max(2,(iw-{crop_pixels * 2})*{ratio:.8f})/2)*2:"
                    f"trunc(max(2,(ih-{crop_pixels * 2})*{ratio:.8f})/2)*2",
                    f"scale={width}:{height}:flags=lanczos",
                ]
            )
        if color_adjust:
            filters.append("eq=brightness=0.01:contrast=1.03:saturation=1.04")
        if hue_degrees:
            filters.append(f"hue=h={hue_degrees:.3f}")
        grades = {
            "warm": "colorbalance=rs=.025:gs=.008:bs=-.018",
            "cool": "colorbalance=rs=-.018:gs=.004:bs=.025",
            "cinematic": "eq=contrast=1.035:saturation=.96:gamma=.99,colorbalance=rs=.012:bs=.015",
            "vintage": "eq=contrast=.97:saturation=.88:gamma=1.015,colorbalance=rs=.022:bs=-.012",
        }
        if color_grade != "none":
            filters.append(grades[color_grade])

    # Garante geometria uniforme entre todos os trechos do concat, inclusive
    # vídeos de celular com rotação interna ou dimensões ímpares.
    filters.extend([f"scale={width}:{height}:flags=lanczos", "format=yuv420p"])
    return filters


def _run_attempt(
    *,
    input_path: str,
    output_path: str,
    filter_complex: str,
    quality_crf: int,
    strip_metadata: bool,
) -> tuple[bool, str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-noautorotate",
        "-i",
        input_path,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-preset",
        "medium",
        "-crf",
        str(quality_crf),
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-metadata:s:v:0",
        "rotate=0",
    ]
    if strip_metadata:
        command.extend(["-map_metadata", "-1", "-map_chapters", "-1"])
    command.extend(["-movflags", "+faststart", output_path])

    result = video_processor._run(command, timeout=420)
    valid = result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    return valid, result.stderr[-6000:]


def process_dynamic_video(
    *,
    input_path: str,
    output_path: str,
    flip_horizontal: bool = True,
    random_trim: bool = True,
    crop_zoom: bool = True,
    color_adjust: bool = True,
    fade: bool = True,
    strip_metadata: bool = True,
    sensor_noise: int = 2,
    crop_pixels: int = 4,
    zoom_factor: float = 1.02,
    hue_degrees: float = 1.0,
    color_grade: str = "cinematic",
    output_fps: str = "29.97",
    smooth_motion: bool = True,
    adaptive_sharpen: bool = True,
    hard_cuts: bool = True,
    speed_ramp: bool = True,
    short_slowmo: bool = True,
    short_speedup: bool = True,
    freeze_frame: bool = True,
    highlight_replay: bool = True,
    quality_crf: int = 18,
) -> None:
    if not 0 <= sensor_noise <= 4:
        raise video_processor.VideoProcessingError("O ruído deve estar entre 0 e 4.")
    if not 0 <= crop_pixels <= 8:
        raise video_processor.VideoProcessingError("O recorte deve estar entre 0 e 8 pixels por borda.")
    if not 1.0 <= zoom_factor <= 1.05:
        raise video_processor.VideoProcessingError("O zoom deve estar entre 1.00x e 1.05x.")
    if not -3.0 <= hue_degrees <= 3.0:
        raise video_processor.VideoProcessingError("A matiz deve estar entre -3 e 3 graus.")
    if color_grade not in {"none", "warm", "cool", "cinematic", "vintage"}:
        raise video_processor.VideoProcessingError("Preset de cor inválido.")
    if output_fps not in {"source", "29.97"}:
        raise video_processor.VideoProcessingError("FPS de saída inválido.")
    if not 17 <= quality_crf <= 20:
        raise video_processor.VideoProcessingError("A qualidade CRF deve estar entre 17 e 20.")

    duration, _, coded_width, coded_height = video_processor.probe_video(input_path)
    rotation = _probe_rotation(input_path)
    width, height = _display_dimensions(coded_width, coded_height, rotation)
    trim = min(duration * 0.025, 0.35) if random_trim and duration > 1 else 0.0
    output_duration = duration - trim * 2
    if output_duration <= 0.2:
        raise video_processor.VideoProcessingError("O vídeo é curto demais para ser processado.")

    peak = legacy._detect_motion_peak(input_path, duration)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    for safe_mode in (0, 1, 2):
        spatial_filters = _spatial_filters(
            rotation=rotation,
            width=width,
            height=height,
            output_duration=output_duration,
            flip_horizontal=flip_horizontal,
            crop_zoom=crop_zoom,
            crop_pixels=crop_pixels,
            zoom_factor=zoom_factor,
            color_adjust=color_adjust,
            hue_degrees=hue_degrees,
            color_grade=color_grade,
            smooth_motion=smooth_motion,
            adaptive_sharpen=adaptive_sharpen,
            sensor_noise=sensor_noise,
            safe_mode=safe_mode,
        )
        filter_complex, final_duration = v2._build_filter_complex(
            trim=trim,
            source_duration=duration,
            output_duration=output_duration,
            width=width,
            height=height,
            spatial_filters=spatial_filters,
            peak_source_time=peak,
            hard_cuts=hard_cuts,
            speed_ramp=speed_ramp,
            short_slowmo=short_slowmo,
            short_speedup=short_speedup,
            freeze_frame=freeze_frame,
            highlight_replay=highlight_replay,
            output_fps=output_fps,
            fade=fade,
        )
        logger.info(
            "Dynamic v3 attempt=%s rotation=%s geometry=%sx%s peak=%.3fs output=%.3fs",
            safe_mode + 1,
            rotation,
            width,
            height,
            peak,
            final_duration,
        )
        try:
            os.remove(output_path)
        except FileNotFoundError:
            pass
        valid, error = _run_attempt(
            input_path=input_path,
            output_path=output_path,
            filter_complex=filter_complex,
            quality_crf=quality_crf,
            strip_metadata=strip_metadata,
        )
        if valid:
            if safe_mode:
                logger.warning("Dynamic montage succeeded using compatibility fallback %s.", safe_mode)
            return
        errors.append(error)
        logger.error("Dynamic v3 attempt %s failed: %s", safe_mode + 1, error)

    try:
        os.remove(output_path)
    except FileNotFoundError:
        pass
    summary = next((line.strip() for error in reversed(errors) for line in error.splitlines() if line.strip()), "erro desconhecido")
    raise video_processor.VideoProcessingError(
        f"Não foi possível criar a montagem dinâmica. Detalhe técnico: {summary[:280]}"
    )
