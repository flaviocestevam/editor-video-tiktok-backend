from __future__ import annotations

import logging
import os
from pathlib import Path

from . import dynamic_montage as legacy
from . import dynamic_montage_v2 as v2
from . import dynamic_montage_v3 as v3
from . import video_processor

logger = logging.getLogger(__name__)


def _bounded_dimensions(width: int, height: int) -> tuple[int, int]:
    """Mantém a proporção e limita o processamento ao máximo social 1080×1920."""
    long_side = max(width, height)
    short_side = min(width, height)
    scale = min(1.0, 1920.0 / max(long_side, 1), 1080.0 / max(short_side, 1))
    bounded_width = max(2, int(width * scale) // 2 * 2)
    bounded_height = max(2, int(height * scale) // 2 * 2)
    return bounded_width, bounded_height


def _minimal_spatial_filters(
    *,
    rotation: int,
    width: int,
    height: int,
    flip_horizontal: bool,
    crop_zoom: bool,
    crop_pixels: int,
    zoom_factor: float,
    color_adjust: bool,
    hue_degrees: float,
    color_grade: str,
) -> list[str]:
    filters = v3._orientation_filters(rotation)
    if flip_horizontal:
        filters.append("hflip")
    if crop_zoom or crop_pixels or zoom_factor > 1.0:
        ratio = 1.0 / max(zoom_factor, 1.0)
        filters.extend(
            [
                "crop="
                f"trunc((iw-{crop_pixels * 2})*{ratio:.8f}/2)*2:"
                f"trunc((ih-{crop_pixels * 2})*{ratio:.8f}/2)*2",
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
    filters.extend([f"scale={width}:{height}:flags=lanczos", "format=yuv420p"])
    return filters


def _run_attempt(
    *,
    input_path: str,
    output_path: str,
    filter_complex: str,
    quality_crf: int,
    strip_metadata: bool,
    preset: str,
) -> tuple[bool, str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-noautorotate",
        "-filter_complex_threads",
        "2",
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
        preset,
        "-crf",
        str(quality_crf),
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "2",
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
    rotation = v3._probe_rotation(input_path)
    display_width, display_height = v3._display_dimensions(coded_width, coded_height, rotation)
    bounded_width, bounded_height = _bounded_dimensions(display_width, display_height)
    trim = min(duration * 0.025, 0.35) if random_trim and duration > 1 else 0.0
    output_duration = duration - trim * 2
    if output_duration <= 0.2:
        raise video_processor.VideoProcessingError("O vídeo é curto demais para ser processado.")

    peak = legacy._detect_motion_peak(input_path, duration)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    high_resolution = display_width * display_height > 2_400_000
    attempts = [
        (0, bounded_width if high_resolution else display_width, bounded_height if high_resolution else display_height, "fast"),
        (1, bounded_width, bounded_height, "veryfast"),
        (2, bounded_width, bounded_height, "veryfast"),
    ]
    errors: list[str] = []

    for safe_mode, width, height, preset in attempts:
        if safe_mode < 2:
            spatial_filters = v3._spatial_filters(
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
        else:
            spatial_filters = _minimal_spatial_filters(
                rotation=rotation,
                width=width,
                height=height,
                flip_horizontal=flip_horizontal,
                crop_zoom=crop_zoom,
                crop_pixels=crop_pixels,
                zoom_factor=zoom_factor,
                color_adjust=color_adjust,
                hue_degrees=hue_degrees,
                color_grade=color_grade,
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
            "Dynamic v4 attempt=%s rotation=%s coded=%sx%s output=%sx%s peak=%.3fs duration=%.3fs",
            safe_mode + 1,
            rotation,
            coded_width,
            coded_height,
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
            preset=preset,
        )
        if valid:
            if safe_mode or high_resolution:
                logger.warning(
                    "Dynamic montage completed in compatibility mode=%s resolution=%sx%s.",
                    safe_mode,
                    width,
                    height,
                )
            return
        errors.append(error)
        logger.error("Dynamic v4 attempt %s failed: %s", safe_mode + 1, error)

    try:
        os.remove(output_path)
    except FileNotFoundError:
        pass
    detail = next(
        (line.strip() for error in reversed(errors) for line in reversed(error.splitlines()) if line.strip()),
        "o FFmpeg foi encerrado sem informar detalhes",
    )
    raise video_processor.VideoProcessingError(
        f"Não foi possível criar a montagem dinâmica. Detalhe técnico: {detail[:280]}"
    )
