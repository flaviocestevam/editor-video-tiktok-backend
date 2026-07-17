from __future__ import annotations

import json
import os
import textwrap
import uuid
from pathlib import Path
from typing import Any

from app.services import dynamic_montage
from app.services import video_processor


FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf",
    "/usr/share/fonts/truetype/lato/Lato-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font_path() -> str:
    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    raise video_processor.VideoProcessingError("Fonte moderna não encontrada no servidor.")


def _clean_text(value: Any) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    return text[:150]


def _wrap_text(text: str, width: int) -> str:
    if not text:
        return ""
    max_chars = 32 if width <= 720 else 38
    lines = textwrap.wrap(text, width=max_chars, break_long_words=False, break_on_hyphens=False)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
        if len(lines[1]) > max_chars + 8:
            lines[1] = lines[1][: max_chars + 5].rstrip() + "…"
    return "\n".join(lines[:2])


def _safe_script(script_json: str, duration: float, width: int) -> list[dict[str, Any]]:
    try:
        raw = json.loads(script_json)
    except json.JSONDecodeError as exc:
        raise video_processor.VideoProcessingError("Roteiro de humor inválido.") from exc
    if not isinstance(raw, list):
        raise video_processor.VideoProcessingError("O roteiro deve ser uma lista de frases.")

    safe: list[dict[str, Any]] = []
    for item in raw[:8]:
        if not isinstance(item, dict) or not bool(item.get("enabled", True)):
            continue
        text = _wrap_text(_clean_text(item.get("text") or item.get("selected_text")), width)
        if not text:
            continue
        try:
            start = max(0.0, min(float(item.get("start", 0)), duration))
            end = max(start + 0.15, min(float(item.get("end", start + 1)), duration))
        except (TypeError, ValueError):
            continue
        position = str(item.get("position", "bottom"))
        if position not in {"top", "middle", "bottom"}:
            position = "bottom"
        safe.append({"text": text, "start": start, "end": end, "position": position})
    return safe


def _y_expression(position: str) -> str:
    if position == "top":
        return "h*0.10"
    if position == "middle":
        return "(h-text_h)/2"
    return "h-text_h-h*0.13"


def _drawtext_filters(script: list[dict[str, Any]], temp_dir: str, width: int) -> tuple[list[str], list[str]]:
    font = _font_path().replace("\\", "/").replace(":", "\\:")
    fontsize = max(34, min(58, round(width * 0.072)))
    filters: list[str] = []
    files: list[str] = []
    Path(temp_dir).mkdir(parents=True, exist_ok=True)

    for item in script:
        path = os.path.join(temp_dir, f"caption-{uuid.uuid4().hex}.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(item["text"])
        files.append(path)
        escaped_path = path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        filters.append(
            "drawtext="
            f"fontfile='{font}':textfile='{escaped_path}':reload=0:"
            f"fontsize={fontsize}:fontcolor=white:line_spacing=10:"
            "borderw=6:bordercolor=black@0.96:"
            "shadowx=3:shadowy=3:shadowcolor=black@0.75:"
            "box=1:boxcolor=black@0.18:boxborderw=18:"
            "x=(w-text_w)/2:"
            f"y={_y_expression(item['position'])}:"
            f"enable='between(t,{item['start']:.3f},{item['end']:.3f})'"
        )
    return filters, files


def render_humor_video(
    *,
    input_path: str,
    output_path: str,
    temp_dir: str,
    script_json: str,
    quality_crf: int = 18,
) -> None:
    duration, _, width, _ = video_processor.probe_video(input_path)
    script = _safe_script(script_json, duration + 3.0, width)
    if not script:
        raise video_processor.VideoProcessingError("Selecione ao menos uma frase para o tutorial.")

    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    intermediate = os.path.join(temp_dir, f"montage-{uuid.uuid4().hex}.mp4")
    text_files: list[str] = []
    try:
        dynamic_montage.process_dynamic_video(
            input_path=input_path,
            output_path=intermediate,
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
            quality_crf=quality_crf,
        )
        edited_duration, _, edited_width, _ = video_processor.probe_video(intermediate)
        script = _safe_script(script_json, edited_duration, edited_width)
        filters, text_files = _drawtext_filters(script, temp_dir, edited_width)
        if not filters:
            raise video_processor.VideoProcessingError("Nenhuma frase válida foi aprovada.")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", intermediate,
            "-vf", ",".join(filters),
            "-map", "0:v:0", "-c:v", "libx264", "-profile:v", "high",
            "-preset", "medium", "-crf", str(quality_crf), "-pix_fmt", "yuv420p",
            "-an", "-map_metadata", "-1", "-map_chapters", "-1",
            "-movflags", "+faststart", output_path,
        ]
        result = video_processor._run(command, timeout=480)
        if result.returncode != 0:
            raise video_processor.VideoProcessingError("Não foi possível aplicar as frases ao vídeo.")
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise video_processor.VideoProcessingError("O tutorial não gerou um arquivo válido.")
    finally:
        for path in [intermediate, *text_files]:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
