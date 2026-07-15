import subprocess
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class VideoProcessingError(Exception):
    """Erro esperado durante o processamento de video (ex: ffmpeg falhou, arquivo invalido)."""
    pass


def get_video_duration(video_path: str) -> float:
    """Obtém duração do vídeo com fallback."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json', 
            '-show_format', '-show_streams', video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            return float(data['format']['duration'])
    except Exception as e:
        logger.warning(f"Ffprobe falhou, usando fallback: {e}")

    # Fallback
    return 10.0  # duração padrão para vídeos curtos

def process_video(input_path: str, output_path: str, options: dict):
    """Processa o vídeo com comandos otimizados."""
    try:
        duration = get_video_duration(input_path)
        trim_start = max(0.1, duration * 0.05)
        trim_end = max(0.1, duration * 0.05)
        cmd = [
            'ffmpeg', '-i', input_path,
            '-vf', 'hflip,scale=iw*0.95:ih*0.95',
            '-af', 'atempo=1.0',
            '-ss', str(trim_start),
            '-t', str(duration - trim_start - trim_end),
            '-preset', 'veryfast',
            '-crf', '23',
            '-movflags', '+faststart',
            '-y', output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise Exception(result.stderr)
        logger.info("Vídeo processado com sucesso")
        return True
    except Exception as e:
        logger.error(f"Erro no processamento: {e}")
        raisesim
