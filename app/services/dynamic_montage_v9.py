from __future__ import annotations

import logging
import os
import threading
from typing import Any

from . import dynamic_montage_v5 as v5
from . import dynamic_montage_v7 as v7
from . import dynamic_montage_v8 as v8
from . import video_processor

logger = logging.getLogger(__name__)
_PATCH_LOCK = threading.Lock()


def _parse_fps(video_stream: dict[str, Any]) -> float:
    return v7._parse_fps(
        str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0")
    )


def _build_filters(
    *,
    width: int,
    height: int,
    duration: float,
    source_fps: float,
    remove_text_overlays: bool,
    output_fps: str,
    fade: bool,
    reduced: bool,
) -> tuple[list[str], bool]:
    filters: list[str] = []
    frame_rate_conversion = False

    if remove_text_overlays and not reduced:
        filters.extend([
            "crop=iw:trunc(ih*0.90/2)*2:0:trunc(ih*0.05/2)*2",
            f"scale={width}:{height}:flags=lanczos",
        ])

    if not reduced and output_fps == "29.97" and abs(source_fps - (30000 / 1001)) > 0.02:
        filters.append("fps=30000/1001")
        frame_rate_conversion = True

    if fade and duration > 0.5:
        fade_in = min(0.18, duration / 6)
        fade_out = min(0.28, duration / 5)
        filters.extend([
            f"fade=t=in:st=0:d={fade_in:.3f}",
            f"fade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}",
        ])

    filters.append("format=yuv420p")
    return filters, frame_rate_conversion


def _run_final_pass(
    *,
    output_path: str,
    remove_audio: bool,
    remove_text_overlays: bool,
    output_fps: str,
    fade: bool,
    quality_crf: int,
) -> dict[str, Any]:
    """Executa a etapa final com tentativas leves e nunca perde a montagem já pronta."""
    probe = v5._probe(output_path)
    streams = probe.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    has_audio = any(item.get("codec_type") == "audio" for item in streams)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    duration = float((probe.get("format") or {}).get("duration") or 0.0)
    source_fps = _parse_fps(video_stream)

    if width <= 0 or height <= 0 or duration <= 0:
        return {
            "applied": False,
            "fallback_kept_montage": True,
            "failure_reason": "Não foi possível medir a montagem antes da etapa final.",
            "text_bands_removed": False,
            "temporal_interpolation": False,
            "frame_rate_conversion": False,
            "source_fps": round(source_fps, 4),
            "output_fps": round(source_fps, 4),
            "fade_in_out": False,
            "final_crf": None,
            "final_duration": round(duration, 3),
        }

    attempts = [
        {"preset": "fast", "crf": max(15, quality_crf - 2), "reduced": False, "timeout": 300},
        {"preset": "veryfast", "crf": max(17, quality_crf), "reduced": True, "timeout": 240},
    ]
    errors: list[str] = []

    for index, attempt in enumerate(attempts, start=1):
        filters, frame_rate_conversion = _build_filters(
            width=width,
            height=height,
            duration=duration,
            source_fps=source_fps,
            remove_text_overlays=remove_text_overlays,
            output_fps=output_fps,
            fade=fade,
            reduced=bool(attempt["reduced"]),
        )
        temp_path = f"{output_path}.v9-final-{index}.mp4"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", output_path,
            "-map", "0:v:0",
            "-vf", ",".join(filters),
            "-c:v", "libx264", "-profile:v", "high",
            "-preset", str(attempt["preset"]),
            "-crf", str(attempt["crf"]),
            "-pix_fmt", "yuv420p",
            "-threads", "2",
        ]
        if remove_audio or not has_audio:
            command.append("-an")
        else:
            command.extend(["-map", "0:a:0?", "-c:a", "copy"])
        command.extend(["-movflags", "+faststart", temp_path])

        try:
            result = video_processor._run(command, timeout=int(attempt["timeout"]))
            valid = result.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0
            error_text = (result.stderr or result.stdout or "FFmpeg encerrado sem detalhes")[-500:]
        except video_processor.VideoProcessingError as exc:
            valid = False
            error_text = str(exc)

        if valid:
            os.replace(temp_path, output_path)
            final_probe = v5._probe(output_path)
            final_stream = next(
                (item for item in final_probe.get("streams", []) if item.get("codec_type") == "video"),
                {},
            )
            return {
                "applied": True,
                "fallback_kept_montage": False,
                "attempt": index,
                "compatibility_mode": index - 1,
                "text_bands_removed": remove_text_overlays and not bool(attempt["reduced"]),
                "temporal_interpolation": False,
                "frame_rate_conversion": frame_rate_conversion,
                "source_fps": round(source_fps, 4),
                "output_fps": round(_parse_fps(final_stream), 4),
                "fade_in_out": fade,
                "final_crf": int(attempt["crf"]),
                "final_duration": round(
                    float((final_probe.get("format") or {}).get("duration") or duration), 3
                ),
            }

        errors.append(error_text)
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        logger.warning("Etapa final v9 tentativa %s falhou: %s", index, error_text)

    return {
        "applied": False,
        "fallback_kept_montage": True,
        "failure_reason": errors[-1] if errors else "Etapa final indisponível.",
        "text_bands_removed": False,
        "temporal_interpolation": False,
        "frame_rate_conversion": False,
        "source_fps": round(source_fps, 4),
        "output_fps": round(source_fps, 4),
        "fade_in_out": False,
        "final_crf": None,
        "final_duration": round(duration, 3),
    }


def process_dynamic_video(**kwargs: Any) -> dict[str, Any]:
    with _PATCH_LOCK:
        original_final_pass = v8._run_final_pass
        v8._run_final_pass = _run_final_pass
        try:
            report = v8.process_dynamic_video(**kwargs)
        finally:
            v8._run_final_pass = original_final_pass

    report["engine"] = "dynamic_montage_v9_resilient"
    final_pass = report.setdefault("final_pass", {})
    warnings = report.setdefault("warnings", [])
    if not final_pass.get("applied", True):
        warnings.append(
            "A montagem foi concluída, mas a etapa final de qualidade/fade não coube no servidor. "
            "O vídeo foi preservado e entregue sem descartar o processamento."
        )
    elif int(final_pass.get("compatibility_mode", 0)) > 0:
        warnings.append(
            "A etapa final usou o modo leve para concluir no servidor; o fade foi mantido e os efeitos opcionais foram reduzidos."
        )
    return report
