from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import dynamic_montage_v4 as v4
from . import video_processor

_PROCESS_LOCK = threading.Lock()
_BASE_BUILDER = v4._build_originality_filter_complex


def _fade_last_builder(*args: Any, **kwargs: Any) -> tuple[str, float]:
    """Usa o plano v4, mas aplica o fade depois de grão/luz/vinheta."""
    requested_fade = bool(kwargs.get("fade", False))
    kwargs["fade"] = False
    graph, final_duration = _BASE_BUILDER(*args, **kwargs)
    if requested_fade and final_duration > 0.4:
        length = min(0.22, final_duration / 4)
        fade_filters = (
            f"fade=t=in:st=0:d={length:.3f},"
            f"fade=t=out:st={max(0.0, final_duration - length):.3f}:d={length:.3f},"
        )
        marker = "format=yuv420p[vout]"
        if marker not in graph:
            raise video_processor.VideoProcessingError("Não foi possível posicionar o fade final.")
        graph = graph.replace(marker, fade_filters + marker, 1)
    return graph, final_duration


def _metadata_args() -> list[str]:
    processed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return [
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-metadata", f"creation_time={processed_at}",
        "-metadata", "com.apple.quicktime.make=Apple",
        "-metadata", "com.apple.quicktime.model=iPhone 15 Pro Max",
        "-metadata", "com.apple.quicktime.software=iOS",
        "-metadata", "com.apple.quicktime.location.ISO6709=-22.9068-043.1729+002.0/",
        "-metadata", "com.apple.quicktime.location.name=Rio de Janeiro, Brasil",
    ]


def _probe(path: str) -> dict[str, Any]:
    result = video_processor._run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        timeout=30,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def _metadata_report(path: str) -> dict[str, Any]:
    probe = _probe(path)
    tags = (probe.get("format") or {}).get("tags") or {}
    normalized = {str(key).lower(): str(value) for key, value in tags.items()}
    fields = {
        "make": normalized.get("com.apple.quicktime.make") == "Apple",
        "model": normalized.get("com.apple.quicktime.model") == "iPhone 15 Pro Max",
        "software": normalized.get("com.apple.quicktime.software") == "iOS",
        "location": "rio de janeiro" in normalized.get("com.apple.quicktime.location.name", "").lower(),
        "gps": normalized.get("com.apple.quicktime.location.iso6709", "").startswith("-22.9068-043.1729"),
        "creation_time": bool(normalized.get("creation_time")),
    }
    return {"written": all(fields.values()), "fields": fields, "tags": tags}


def _inject_metadata(output_path: str) -> dict[str, Any]:
    temp_path = f"{output_path}.metadata.mp4"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", output_path, "-map", "0", "-c", "copy",
        *_metadata_args(),
        "-movflags", "+faststart+use_metadata_tags", temp_path,
    ]
    result = video_processor._run(command, timeout=120)
    if result.returncode != 0 or not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        raise video_processor.VideoProcessingError(
            f"O vídeo foi editado, mas os metadados não puderam ser gravados: {result.stderr[-280:]}"
        )
    os.replace(temp_path, output_path)
    report = _metadata_report(output_path)
    if not report["written"]:
        raise video_processor.VideoProcessingError("Os metadados personalizados não foram confirmados no arquivo final.")
    return report


def _standard_audio_safe(
    *,
    input_path: str,
    output_path: str,
    flip_horizontal: bool,
    random_trim: bool,
    crop_zoom: bool,
    color_adjust: bool,
    fade: bool,
    strip_metadata: bool,
    sensor_noise: int,
    crop_pixels: int,
    zoom_factor: float,
    hue_degrees: float,
    color_grade: str,
    output_fps: str,
    smooth_motion: bool,
    adaptive_sharpen: bool,
    quality_crf: int,
) -> dict[str, Any]:
    temp_dir = str(Path(output_path).parent)
    video_processor.process_video(
        input_path=input_path,
        output_path=output_path,
        temp_dir=temp_dir,
        remove_audio=False,
        flip_horizontal=flip_horizontal,
        random_trim=random_trim,
        crop_zoom=crop_zoom,
        speed_change=False,
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
    metadata = _metadata_report(output_path) if strip_metadata else {"written": False, "fields": {}}
    return {
        "engine": "audio_safe_standard",
        "attempt": 1,
        "compatibility_mode": 0,
        "metadata": metadata,
        "applied_effects": {
            "flip_horizontal": flip_horizontal,
            "random_trim": random_trim,
            "crop_zoom": crop_zoom,
            "color_adjust": color_adjust,
            "fade_in_out": fade,
            "sensor_noise": bool(sensor_noise),
            "output_29_97_fps": output_fps == "29.97",
            "smooth_motion": smooth_motion,
            "adaptive_sharpen": adaptive_sharpen,
            "audio_preserved": True,
            "audio_removed": False,
            "custom_metadata": bool(metadata.get("written")),
            "hard_cuts": False,
            "speed_ramp": False,
            "short_slowmo": False,
            "short_speedup": False,
            "freeze_frame": False,
            "highlight_replay": False,
        },
        "warnings": [
            "Áudio preservado: efeitos temporais da montagem foram desativados para manter sincronização."
        ],
    }


def process_dynamic_video(
    *,
    input_path: str,
    output_path: str,
    remove_audio: bool = True,
    strip_metadata: bool = True,
    **options: Any,
) -> dict[str, Any]:
    duration, has_audio, source_width, source_height = video_processor.probe_video(input_path)
    if has_audio and not remove_audio:
        return _standard_audio_safe(
            input_path=input_path,
            output_path=output_path,
            strip_metadata=strip_metadata,
            flip_horizontal=bool(options.get("flip_horizontal", True)),
            random_trim=bool(options.get("random_trim", True)),
            crop_zoom=bool(options.get("crop_zoom", True)),
            color_adjust=bool(options.get("color_adjust", True)),
            fade=bool(options.get("fade", True)),
            sensor_noise=int(options.get("sensor_noise", 2)),
            crop_pixels=int(options.get("crop_pixels", 4)),
            zoom_factor=float(options.get("zoom_factor", 1.02)),
            hue_degrees=float(options.get("hue_degrees", 1.0)),
            color_grade=str(options.get("color_grade", "cinematic")),
            output_fps=str(options.get("output_fps", "29.97")),
            smooth_motion=bool(options.get("smooth_motion", True)),
            adaptive_sharpen=bool(options.get("adaptive_sharpen", True)),
            quality_crf=int(options.get("quality_crf", 18)),
        )

    with _PROCESS_LOCK:
        original_builder = v4._build_originality_filter_complex
        original_attempt = v4._run_attempt
        state = {"count": 0, "successful_attempt": 0}

        def tracked_attempt(*args: Any, **kwargs: Any) -> tuple[bool, str]:
            state["count"] += 1
            valid, error = original_attempt(*args, **kwargs)
            if valid:
                state["successful_attempt"] = state["count"]
            return valid, error

        v4._build_originality_filter_complex = _fade_last_builder
        v4._run_attempt = tracked_attempt
        try:
            v4.process_dynamic_video(
                input_path=input_path,
                output_path=output_path,
                strip_metadata=False,
                **options,
            )
        finally:
            v4._build_originality_filter_complex = original_builder
            v4._run_attempt = original_attempt

    attempt = state["successful_attempt"] or state["count"] or 1
    safe_mode = max(0, attempt - 1)
    metadata = _inject_metadata(output_path) if strip_metadata else {"written": False, "fields": {}}
    probe = _probe(output_path)
    video_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    warnings: list[str] = []
    if safe_mode == 1:
        warnings.append("Modo de compatibilidade: movimento suave e ruído de sensor foram reduzidos.")
    elif safe_mode >= 2:
        warnings.append("Modo de compatibilidade máximo: movimento suave, nitidez e ruído de sensor foram reduzidos.")

    applied = {
        key: bool(value)
        for key, value in options.items()
        if key in {
            "flip_horizontal", "random_trim", "crop_zoom", "color_adjust", "fade",
            "hard_cuts", "speed_ramp", "short_slowmo", "short_speedup",
            "freeze_frame", "highlight_replay", "dynamic_reframe",
            "animated_grain_overlay", "scene_color_variation", "light_texture_overlay",
        }
    }
    applied.update({
        "sensor_noise": bool(options.get("sensor_noise", 2)) and safe_mode == 0,
        "output_29_97_fps": options.get("output_fps", "29.97") == "29.97",
        "smooth_motion": bool(options.get("smooth_motion", True)) and safe_mode == 0,
        "adaptive_sharpen": bool(options.get("adaptive_sharpen", True)) and safe_mode < 2,
        "audio_removed": True,
        "audio_preserved": False,
        "custom_metadata": bool(metadata.get("written")),
    })
    return {
        "engine": "dynamic_montage_v6",
        "attempt": attempt,
        "compatibility_mode": safe_mode,
        "source_duration": round(duration, 3),
        "source_resolution": {"width": source_width, "height": source_height},
        "output_resolution": {
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
        },
        "metadata": metadata,
        "applied_effects": applied,
        "warnings": warnings,
    }
