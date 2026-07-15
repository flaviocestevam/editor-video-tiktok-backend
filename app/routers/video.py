import os
import shutil
import time
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse

from app.services import video_processor
from app.services.downloader import download_video_from_url, DownloadError

logger = logging.getLogger("editor_video_tiktok.video")

router = APIRouter()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
UPLOAD_DIR = os.path.join(STORAGE_DIR, "uploads")
OUTPUT_DIR = os.path.join(STORAGE_DIR, "outputs")
TEMP_DIR = os.path.join(STORAGE_DIR, "temp")

for directory in (STORAGE_DIR, UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR):
    os.makedirs(directory, exist_ok=True)

ALLOWED_EXTENSIONS = {".mp4"}
MAX_FILE_SIZE_MB = 500


def _cleanup_file(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
            logger.info("Arquivo temporário removido: %s", path)
    except Exception as exc:
        logger.warning("Falha ao remover arquivo temporário %s: %s", path, exc)


def _find_upload_by_id(file_id: str) -> Optional[str]:
    safe_id = os.path.basename(file_id)
    for ext in ALLOWED_EXTENSIONS:
        candidate = os.path.join(UPLOAD_DIR, f"{safe_id}{ext}")
        if os.path.exists(candidate):
            return candidate
    return None


@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    """Recebe upload manual de um arquivo MP4."""
    logger.info(
        "POST /upload recebido: filename=%s content_type=%s",
        file.filename, file.content_type,
    )

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        logger.warning("Upload rejeitado: extensão não suportada (%s)", ext)
        raise HTTPException(status_code=400, detail="Apenas arquivos .mp4 são permitidos.")

    file_id = uuid.uuid4().hex
    dest_path = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")

    start = time.monotonic()
    try:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as exc:
        logger.exception("Erro ao salvar upload em %s", dest_path)
        raise HTTPException(
            status_code=500, detail=f"Erro ao salvar o arquivo enviado: {exc}"
        ) from exc
    finally:
        await file.close()

    size_mb = os.path.getsize(dest_path) / (1024 * 1024)
    logger.info(
        "Upload salvo com sucesso: %s (%.2fMB) em %.2fs",
        dest_path, size_mb, time.monotonic() - start,
    )

    if size_mb > MAX_FILE_SIZE_MB:
        logger.warning("Upload excede limite: %.2fMB > %sMB", size_mb, MAX_FILE_SIZE_MB)
        _cleanup_file(dest_path)
        raise HTTPException(
            status_code=400, detail=f"Arquivo excede o limite de {MAX_FILE_SIZE_MB}MB."
        )

    return {"file_id": file_id, "filename": os.path.basename(dest_path)}


@router.post("/download")
async def download_video(url: str = Form(...)):
    """Baixa um vídeo curto (TikTok, YouTube Shorts ou Instagram) a partir de um link, para uso pessoal."""
    logger.info("POST /download recebido: url=%s", url)

    if not url or not url.startswith("http"):
        logger.warning("Download rejeitado: URL inválida (%s)", url)
        raise HTTPException(status_code=400, detail="URL inválida.")

    start = time.monotonic()
    try:
        file_id, dest_path = download_video_from_url(url, UPLOAD_DIR)
    except DownloadError as exc:
        logger.warning(
            "Falha esperada ao baixar vídeo de %s após %.2fs: %s",
            url, time.monotonic() - start, exc,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Erro inesperado ao baixar vídeo de %s após %.2fs",
            url, time.monotonic() - start,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Não foi possível baixar o vídeo a partir do link informado: {exc}",
        ) from exc

    logger.info(
        "Download concluído: %s em %.2fs", dest_path, time.monotonic() - start
    )
    return {"file_id": file_id, "filename": os.path.basename(dest_path)}


@router.post("/process")
async def process_video(
    file_id: str = Form(...),
    remove_audio: bool = Form(False),
    flip_horizontal: bool = Form(True),
    random_trim: bool = Form(True),
    crop_zoom: bool = Form(True),
    speed_change: bool = Form(True),
    color_adjust: bool = Form(True),
    fade: bool = Form(True),
):
    """Aplica melhorias criativas automáticas no vídeo enviado ou baixado anteriormente."""
    logger.info(
        "POST /process recebido: file_id=%s remove_audio=%s flip=%s random_trim=%s "
        "crop_zoom=%s speed_change=%s color_adjust=%s fade=%s",
        file_id, remove_audio, flip_horizontal, random_trim, crop_zoom,
        speed_change, color_adjust, fade,
    )

    input_path = _find_upload_by_id(file_id)
    if not input_path:
        logger.warning("Processamento abortado: file_id não encontrado (%s)", file_id)
        raise HTTPException(
            status_code=404, detail="Arquivo não encontrado. Faça upload ou download primeiro."
        )

    output_filename = f"{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    start = time.monotonic()
    logger.info("Iniciando etapa de processamento (file_id=%s) -> %s", file_id, output_path)

    try:
        video_processor.process_video(
            input_path=input_path,
            output_path=output_path,
            temp_dir=TEMP_DIR,
            remove_audio=remove_audio,
            flip_horizontal=flip_horizontal,
            random_trim=random_trim,
            crop_zoom=crop_zoom,
            speed_change=speed_change,
            color_adjust=color_adjust,
            fade=fade,
        )
    except video_processor.VideoProcessingError as exc:
        logger.error(
            "Falha controlada ao processar vídeo (file_id=%s) após %.2fs: %s",
            file_id, time.monotonic() - start, exc,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Erro inesperado ao processar vídeo (file_id=%s) após %.2fs",
            file_id, time.monotonic() - start,
        )
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao processar o vídeo: {exc}"
        ) from exc

    logger.info(
        "Processamento concluído com sucesso: file_id=%s output=%s em %.2fs",
        file_id, output_filename, time.monotonic() - start,
    )

    return {
        "output_filename": output_filename,
        "download_url": f"/api/video/result/{output_filename}",
    }


@router.get("/result/{filename}")
async def get_result(filename: str):
    """Retorna o vídeo processado para download."""
    safe_name = os.path.basename(filename)
    path = os.path.join(OUTPUT_DIR, safe_name)
    logger.info("GET /result/%s solicitado", safe_name)

    if not os.path.exists(path):
        logger.warning("Resultado não encontrado: %s", path)
        raise HTTPException(status_code=404, detail="Resultado não encontrado.")

    return FileResponse(path, media_type="video/mp4", filename=safe_name)


@router.delete("/cleanup/{file_id}")
async def cleanup_upload(file_id: str):
    """Remove manualmente um arquivo de upload temporário do servidor."""
    logger.info("DELETE /cleanup/%s solicitado", file_id)

    input_path = _find_upload_by_id(file_id)
    if not input_path:
        logger.warning("Cleanup abortado: file_id não encontrado (%s)", file_id)
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")

    _cleanup_file(input_path)
    return {"detail": "Arquivo removido com sucesso."}
