import os
import shutil
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
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Apenas arquivos .mp4 são permitidos.")

    file_id = uuid.uuid4().hex
    dest_path = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")

    try:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as exc:
        logger.exception("Erro ao salvar upload")
        raise HTTPException(status_code=500, detail="Erro ao salvar o arquivo enviado.") from exc
    finally:
        await file.close()

    size_mb = os.path.getsize(dest_path) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        _cleanup_file(dest_path)
        raise HTTPException(status_code=400, detail=f"Arquivo excede o limite de {MAX_FILE_SIZE_MB}MB.")

    return {"file_id": file_id, "filename": os.path.basename(dest_path)}


@router.post("/download")
async def download_video(url: str = Form(...)):
    """Baixa um vídeo curto (TikTok, YouTube Shorts ou Instagram) a partir de um link, para uso pessoal."""
    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="URL inválida.")

    try:
        file_id, dest_path = download_video_from_url(url, UPLOAD_DIR)
    except DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Erro ao baixar vídeo")
        raise HTTPException(status_code=500, detail="Não foi possível baixar o vídeo a partir do link informado.") from exc

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
    input_path = _find_upload_by_id(file_id)
    if not input_path:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado. Faça upload ou download primeiro.")

    output_filename = f"{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Erro ao processar vídeo")
        raise HTTPException(status_code=500, detail="Erro interno ao processar o vídeo.") from exc

    return {
        "output_filename": output_filename,
        "download_url": f"/api/video/result/{output_filename}",
    }


@router.get("/result/{filename}")
async def get_result(filename: str):
    """Retorna o vídeo processado para download."""
    safe_name = os.path.basename(filename)
    path = os.path.join(OUTPUT_DIR, safe_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Resultado não encontrado.")
    return FileResponse(path, media_type="video/mp4", filename=safe_name)


@router.delete("/cleanup/{file_id}")
async def cleanup_upload(file_id: str):
    """Remove manualmente um arquivo de upload temporário do servidor."""
    input_path = _find_upload_by_id(file_id)
    if not input_path:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    _cleanup_file(input_path)
    return {"detail": "Arquivo removido com sucesso."}
