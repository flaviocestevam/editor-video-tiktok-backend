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


def probe_video(video_path: str) -> tuple[float, bool, int, int]:
    result = _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", video_path],
        timeout=30,
    )
    if result.returncode != 0:
        raise VideoProcessingError("O arquivo enviado não é um vídeo válido.")
    try:
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        video_stream = next(stream for stream in data["streams"] if stream.get("codec_type") == "video")
        has_video = True
        has_audio = any(stream.get("codec_type") == "audio" for stream in data["streams"])
        width = int(video_stream["width"])
        height = int(video_stream["height"])
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VideoProcessingError("Não foi possível identificar a duração do vídeo.") from exc
    if not has_video or duration <= 0:
        raise VideoProcessingError("O arquivo enviado não contém uma faixa de vídeo válida.")
    return duration, has_audio, width, height


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
    strip_metadata: bool = True,
    sensor_noise: int = 2,
    crop_pixels: int = 4,
    zoom_factor: float = 1.02,
    hue_degrees: float = 1.0,
    color_grade: str = "cinematic",
    output_fps: str = "29.97",
    quality_crf: int = 18,
) -> None:
    """Apply the API editing options and produce a browser-compatible MP4."""
    if not 0 <= sensor_noise <= 4:
        raise VideoProcessingError("O ruído deve estar entre 0 e 4.")
    if not 0 <= crop_pixels <= 8:
        raise VideoProcessingError("O recorte deve estar entre 0 e 8 pixels por borda.")
    if not 1.0 <= zoom_factor <= 1.05:
        raise VideoProcessingError("O zoom deve estar entre 1.00x e 1.05x.")
    if not -3.0 <= hue_degrees <= 3.0:
        raise VideoProcessingError("A matiz deve estar entre -3 e 3 graus.")
    if color_grade not in {"none", "warm", "cool", "cinematic", "vintage"}:
        raise VideoProcessingError("Preset de cor inválido.")
    if output_fps not in {"source", "29.97"}:
        raise VideoProcessingError("FPS de saída inválido.")
    if not 17 <= quality_crf <= 20:
        raise VideoProcessingError("A qualidade CRF deve estar entre 17 e 20.")

    duration, has_audio, source_width, source_height = probe_video(input_path)
    trim = min(duration * 0.025, 0.35) if random_trim and duration > 1 else 0.0
    output_duration = duration - (trim * 2)
    if output_duration <= 0.2:
        raise VideoProcessingError("O vídeo é curto demais para ser processado.")

    speed = random.uniform(0.98, 1.02) if speed_change else 1.0
    filters: list[str] = []
    if flip_horizontal:
        filters.append("hflip")
    # Recorte e zoom são combinados antes da escala final para evitar uma
    # segunda codificação. As dimensões são sempre pares para yuv420p.
    if crop_pixels or zoom_factor > 1.0:
        ratio = 1.0 / zoom_factor
        filters.append(
            "crop="
            f"trunc((iw-{crop_pixels * 2})*{ratio:.8f}/2)*2:"
            f"trunc((ih-{crop_pixels * 2})*{ratio:.8f}/2)*2"
        )
        filters.append(
            f"scale={source_width}:{source_height}:flags=lanczos"
        )
    elif crop_zoom:
        filters.append("crop=trunc(iw*0.96/2)*2:trunc(ih*0.96/2)*2")
        filters.append(f"scale={source_width}:{source_height}:flags=lanczos")
    if color_adjust:
        filters.append("eq=brightness=0.01:contrast=1.03:saturation=1.04")
    if hue_degrees:
        filters.append(f"hue=h={hue_degrees:.3f}")
    grade_filters = {
        "warm": "colorbalance=rs=.025:gs=.008:bs=-.018",
        "cool": "colorbalance=rs=-.018:gs=.004:bs=.025",
        "cinematic": "eq=contrast=1.035:saturation=.96:gamma=.99,colorbalance=rs=.012:bs=.015",
        "vintage": "eq=contrast=.97:saturation=.88:gamma=1.015,colorbalance=rs=.022:bs=-.012",
    }
    if color_grade != "none":
        filters.append(grade_filters[color_grade])
    if sensor_noise:
        filters.append(f"noise=alls={sensor_noise}:allf=t")
    if output_fps == "29.97":
        filters.append("fps=30000/1001")
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
    command.extend([
        "-map", "0:v:0", "-c:v", "libx264", "-profile:v", "high",
        "-preset", "medium", "-crf", str(quality_crf), "-pix_fmt", "yuv420p",
    ])

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

    if strip_metadata:
        command.extend([
            "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact",
            "-flags:v", "+bitexact",
        ])
        if not remove_audio and has_audio:
            command.extend(["-flags:a", "+bitexact"])
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
