import json
import logging
import os
import random
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class VideoProcessingError(Exception):
    """Expected failure while probing or processing a video."""


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise VideoProcessingError(f"Dependência ausente no servidor: {command[0]}.") from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoProcessingError("O processamento excedeu o tempo máximo permitido.") from exc


def probe_video(video_path: str) -> tuple[float, bool]:
    result = _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", video_path],
        timeout=30,
    )
    if result.returncode != 0:
        raise VideoProcessingError("O arquivo enviado não é um vídeo válido.")
    try:
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        has_video = any(stream.get("codec_type") == "video" for stream in data["streams"])
        has_audio = any(stream.get("codec_type") == "audio" for stream in data["streams"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VideoProcessingError("Não foi possível identificar a duração do vídeo.") from exc
    if not has_video or duration <= 0:
        raise VideoProcessingError("O arquivo enviado não contém uma faixa de vídeo válida.")
    return duration, has_audio


def process_video(
    input_path: str,
    output_path: str,
    temp_dir: str,
    remove_audio: bool = False,
    flip_horizontal: bool = True,
    random_trim: bool = True,
    crop_zoom: bool = True,
    speed_change: bool = True,
    color_adjust: bool = True,
    fade: bool = True,
) -> None:
    """Apply the API editing options and produce a browser-compatible MP4."""
    del temp_dir  # Reserved for future multi-pass processing.
    duration, has_audio = probe_video(input_path)
    trim = min(duration * 0.025, 0.35) if random_trim and duration > 1 else 0.0
    output_duration = duration - (trim * 2)
    if output_duration <= 0.2:
        raise VideoProcessingError("O vídeo é curto demais para ser processado.")

    speed = random.uniform(0.98, 1.02) if speed_change else 1.0
    filters: list[str] = []
    if flip_horizontal:
        filters.append("hflip")
    if crop_zoom:
        filters.append("crop=trunc(iw*0.96/2)*2:trunc(ih*0.96/2)*2")
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    if color_adjust:
        filters.append("eq=brightness=0.01:contrast=1.03:saturation=1.04")
    if speed != 1.0:
        filters.append(f"setpts=PTS/{speed:.6f}")
    final_duration = output_duration / speed
    if fade and final_duration > 0.4:
        fade_length = min(0.25, final_duration / 4)
        filters.extend([f"fade=t=in:st=0:d={fade_length:.3f}", f"fade=t=out:st={final_duration-fade_length:.3f}:d={fade_length:.3f}"])

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{trim:.6f}", "-i", input_path, "-t", f"{output_duration:.6f}"]
    if filters:
        command.extend(["-vf", ",".join(filters)])
    command.extend(["-map", "0:v:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"])

    if remove_audio or not has_audio:
        command.append("-an")
    else:
        audio_filters = []
        if speed != 1.0:
            audio_filters.append(f"atempo={speed:.6f}")
        if fade and final_duration > 0.4:
            fade_length = min(0.25, final_duration / 4)
            audio_filters.extend([f"afade=t=in:st=0:d={fade_length:.3f}", f"afade=t=out:st={final_duration-fade_length:.3f}:d={fade_length:.3f}"])
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k"])
        if audio_filters:
            command.extend(["-af", ",".join(audio_filters)])

    command.extend(["-movflags", "+faststart", output_path])
    result = _run(command, timeout=300)
    if result.returncode != 0:
        logger.error("ffmpeg failed: %s", result.stderr[-4000:])
        try:
            os.remove(output_path)
        except FileNotFoundError:
            pass
        raise VideoProcessingError("Não foi possível processar este vídeo.")
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise VideoProcessingError("O processamento não gerou um arquivo de saída válido.")
