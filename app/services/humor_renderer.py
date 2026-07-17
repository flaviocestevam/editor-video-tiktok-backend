from __future__ import annotations

import json
import os
import subprocess
import textwrap
import uuid
from pathlib import Path
from typing import Any

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


def _wrap_text(text: str, width: int) -> list[str]:
    if not text:
        return []
    max_chars = 24 if width <= 720 else 30
    if len(text) <= max_chars:
        return [text]

    words = text.split()
    best: tuple[int, str, str] | None = None
    for index in range(1, len(words)):
        first = " ".join(words[:index])
        second = " ".join(words[index:])
        if len(first) <= max_chars + 5 and len(second) <= max_chars + 5:
            score = abs(len(first) - len(second))
            if best is None or score < best[0]:
                best = (score, first, second)
    if best:
        return [best[1], best[2]]

    lines = textwrap.wrap(text, width=max_chars, break_long_words=False, break_on_hyphens=False)
    if len(lines) > 2:
        second = " ".join(lines[1:])
        if len(second) > max_chars + 5:
            second = second[: max_chars + 2].rstrip() + "…"
        lines = [lines[0], second]
    return lines[:2]


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
        lines = _wrap_text(_clean_text(item.get("text") or item.get("selected_text")), width)
        if not lines:
            continue
        try:
            start = max(0.0, min(float(item.get("start", 0)), duration))
            requested_end = float(item.get("end", start + 1))
            end = min(duration, max(start + 0.15, requested_end))
        except (TypeError, ValueError):
            continue
        if start >= duration or end <= start:
            continue
        position = str(item.get("position", "bottom"))
        if position not in {"top", "middle", "bottom"}:
            position = "bottom"
        safe.append({"lines": lines, "start": start, "end": end, "position": position})
    return safe


def _y_expression(position: str, line_index: int, line_count: int, fontsize: int) -> str:
    gap = max(6, round(fontsize * 0.18))
    if line_count == 1:
        if position == "top":
            return "h*0.10"
        if position == "middle":
            return "(h-text_h)/2"
        return "h-text_h-h*0.13"

    step = fontsize + gap
    if position == "top":
        return f"h*0.10+{line_index * step}"
    if position == "middle":
        return f"h/2-{step / 2:.1f}+{line_index * step}"
    return f"h-h*0.13-{2 * fontsize + gap}+{line_index * step}"


def _drawtext_filters(script: list[dict[str, Any]], temp_dir: str, width: int) -> tuple[list[str], list[str]]:
    font = _font_path().replace("\\", "/").replace(":", "\\:")
    fontsize = max(32, min(52, round(width * 0.061)))
    filters: list[str] = []
    files: list[str] = []
    Path(temp_dir).mkdir(parents=True, exist_ok=True)

    for item in script:
        lines = item["lines"]
        for line_index, line in enumerate(lines):
            path = os.path.join(temp_dir, f"caption-{uuid.uuid4().hex}.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(line)
            files.append(path)
            escaped_path = path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            filters.append(
                "drawtext="
                f"fontfile='{font}':textfile='{escaped_path}':reload=0:"
                f"fontsize={fontsize}:fontcolor=white:"
                "borderw=5:bordercolor=black@0.96:"
                "shadowx=3:shadowy=3:shadowcolor=black@0.75:"
                "box=1:boxcolor=black@0.16:boxborderw=16:"
                "x=(w-text_w)/2:"
                f"y={_y_expression(item['position'], line_index, len(lines), fontsize)}:"
                f"enable='between(t,{item['start']:.3f},{item['end']:.3f})'"
            )
    return filters, files


def _extract_gray_frame(video_path: str, timestamp: float) -> bytes:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
        "-i", video_path, "-frames:v", "1",
        "-vf", "scale=180:-2:flags=bilinear,format=gray",
        "-f", "rawvideo", "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise video_processor.VideoProcessingError("Não foi possível verificar os textos do vídeo final.") from exc
    if result.returncode != 0 or not result.stdout:
        raise video_processor.VideoProcessingError("Não foi possível verificar os textos do vídeo final.")
    return result.stdout


def _verify_caption_output(
    input_path: str,
    output_path: str,
    script: list[dict[str, Any]],
) -> float:
    scores: list[float] = []
    for item in script[:3]:
        timestamp = (float(item["start"]) + float(item["end"])) / 2
        before = _extract_gray_frame(input_path, timestamp)
        after = _extract_gray_frame(output_path, timestamp)
        sample_size = min(len(before), len(after))
        if sample_size <= 0:
            continue
        score = sum(abs(before[index] - after[index]) for index in range(sample_size)) / sample_size
        scores.append(score)

    strongest = max(scores, default=0.0)
    if strongest < 1.20:
        try:
            os.remove(output_path)
        except FileNotFoundError:
            pass
        raise video_processor.VideoProcessingError(
            "O vídeo foi processado, mas os textos não foram detectados no resultado. Tente novamente após o backend atualizar."
        )
    return strongest


def render_captioned_video(
    *,
    input_path: str,
    output_path: str,
    temp_dir: str,
    script_json: str,
    quality_crf: int = 18,
) -> dict[str, int | float | bool]:
    duration, _, width, _ = video_processor.probe_video(input_path)
    script = _safe_script(script_json, duration, width)
    if not script:
        raise video_processor.VideoProcessingError("Selecione ao menos uma frase para o tutorial.")

    filters, text_files = _drawtext_filters(script, temp_dir, width)
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", input_path,
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

        verification_score = _verify_caption_output(input_path, output_path, script)
        return {
            "captions_applied": True,
            "caption_count": len(script),
            "verification_score": round(verification_score, 3),
        }
    finally:
        for path in text_files:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
