import os
import random
import subprocess
import logging

logger = logging.getLogger("editor_video_tiktok.processor")


class VideoProcessingError(Exception):
    """Erro ao processar um vídeo com o ffmpeg."""


def _run_ffmpeg(cmd):
    logger.info("Executando comando ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="ignore")
        logger.error("Erro no ffmpeg: %s", stderr)
        raise VideoProcessingError(f"Falha ao processar vídeo com ffmpeg: {stderr[-500:]}")


def _probe_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrapper=1:nokey=1",
        path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise VideoProcessingError("Não foi possível ler a duração do vídeo (ffprobe).")
    try:
        return float(result.stdout.decode().strip())
    except ValueError as exc:
        raise VideoProcessingError("Duração do vídeo inválida.") from exc


def _clamp_atempo(factor: float) -> float:
    """O filtro atempo do ffmpeg aceita valores entre 0.5 e 2.0."""
    return max(0.5, min(2.0, factor))


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
) -> str:
    """Aplica uma série de melhorias criativas automáticas e re-encoda o vídeo.

    Etapas aplicadas (quando habilitadas): cortes aleatórios no início/fim,
    flip horizontal, crop com zoom suave, ajuste sutil de velocidade,
    ajustes de brilho/contraste/saturação, remoção de áudio, fade de
    entrada e saída, e re-encode completo em H.264/AAC.
    """
    if not os.path.exists(input_path):
        raise VideoProcessingError("Arquivo de entrada não encontrado.")

    os.makedirs(temp_dir, exist_ok=True)
    working_path = input_path
    temp_files = []

    try:
        duration = _probe_duration(input_path)

        if random_trim and duration > 3:
            start_cut = round(random.uniform(0.1, 0.6), 2)
            end_cut = round(random.uniform(0.1, 0.6), 2)
            new_duration = max(duration - start_cut - end_cut, 1.0)
            trimmed_path = os.path.join(temp_dir, f"trim_{os.path.basename(output_path)}")

            _run_ffmpeg([
                "ffmpeg", "-y",
                "-ss", str(start_cut),
                "-i", working_path,
                "-t", str(new_duration),
                "-c", "copy",
                trimmed_path,
            ])

            working_path = trimmed_path
            temp_files.append(trimmed_path)
            duration = new_duration

        video_filters = []

        if flip_horizontal:
            video_filters.append("hflip")

        if crop_zoom:
            video_filters.append("crop=iw*0.92:ih*0.92")
            video_filters.append("scale=iw/0.92:ih/0.92")

        if color_adjust:
            brightness = round(random.uniform(-0.03, 0.03), 3)
            contrast = round(random.uniform(0.95, 1.08), 3)
            saturation = round(random.uniform(0.95, 1.15), 3)
            video_filters.append(
                f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}"
            )

        speed_factor = 1.0
        if speed_change:
            speed_factor = round(random.uniform(0.97, 1.05), 3)
            video_filters.append(f"setpts=PTS/{speed_factor}")

        if fade:
            fade_duration = 0.4
            effective_duration = duration / speed_factor if speed_factor else duration
            fade_out_start = max(effective_duration - fade_duration, 0)
            video_filters.append(f"fade=t=in:st=0:d={fade_duration}")
            video_filters.append(f"fade=t=out:st={fade_out_start}:d={fade_duration}")

        video_filter_str = ",".join(video_filters) if video_filters else "null"

        cmd = ["ffmpeg", "-y", "-i", working_path]

        if remove_audio:
            cmd.append("-an")
        elif speed_change:
            cmd += ["-af", f"atempo={_clamp_atempo(speed_factor)}"]

        cmd += [
            "-vf", video_filter_str,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
        ]

        if not remove_audio:
            cmd += ["-c:a", "aac", "-b:a", "128k"]

        cmd.append(output_path)

        _run_ffmpeg(cmd)

        return output_path

    finally:
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    logger.info("Arquivo temporário removido: %s", temp_file)
            except Exception as exc:
                logger.warning("Falha ao limpar arquivo temporário %s: %s", temp_file, exc)
