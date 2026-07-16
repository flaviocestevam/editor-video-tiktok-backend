import os
import uuid
import logging
from urllib.parse import urlparse

import yt_dlp

logger = logging.getLogger("editor_video_tiktok.downloader")

SUPPORTED_DOMAINS = ("tiktok.com", "youtube.com", "youtu.be", "instagram.com")


class DownloadError(Exception):
    """Erro ao baixar um vídeo a partir de um link externo."""


def download_video_from_url(url: str, dest_dir: str):
    """Baixa um vídeo curto a partir de um link usando yt-dlp.

    Suporta links do TikTok, YouTube Shorts e Instagram, para uso pessoal.
    Retorna uma tupla (file_id, caminho_do_arquivo_mp4).
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise DownloadError("URL inválida.") from exc
    if parsed.scheme not in {"http", "https"} or not hostname or not any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in SUPPORTED_DOMAINS
    ):
        raise DownloadError(
            "Link não suportado. Utilize links do TikTok, YouTube Shorts ou Instagram."
        )

    os.makedirs(dest_dir, exist_ok=True)
    file_id = uuid.uuid4().hex
    output_template = os.path.join(dest_dir, f"{file_id}.%(ext)s")

    ydl_opts = {
        "format": "mp4/bestvideo+bestaudio/best",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": 500 * 1024 * 1024,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        logger.error("Erro do yt-dlp ao baixar %s: %s", url, exc)
        raise DownloadError(
            "Não foi possível baixar o vídeo. Verifique o link e tente novamente."
        ) from exc

    final_path = os.path.join(dest_dir, f"{file_id}.mp4")
    if not os.path.exists(final_path):
        raise DownloadError("O download foi concluído, mas o arquivo MP4 não foi encontrado.")

    return file_id, final_path
