import os
import logging
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers import generation, humor, video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("editor_video_tiktok")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
UPLOAD_DIR = os.path.join(STORAGE_DIR, "uploads")
OUTPUT_DIR = os.path.join(STORAGE_DIR, "outputs")
TEMP_DIR = os.path.join(STORAGE_DIR, "temp")

for directory in (STORAGE_DIR, UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR):
    os.makedirs(directory, exist_ok=True)

app = FastAPI(
    title="Editor Vídeo TikTok - Backend",
    description="API para upload, download e edição criativa automática de vídeos curtos para uso pessoal.",
    version="1.2.0",
)

cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Loga toda requisição recebida e seu tempo total de resposta.

    Isso ajuda a identificar rapidamente qual chamada do frontend ficou
    presa (por exemplo, em "Aguardando processamento") olhando os logs do
    servidor.
    """
    start = time.monotonic()
    logger.info("--> %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Requisição %s %s levantou exceção não tratada após %.2fs",
            request.method, request.url.path, time.monotonic() - start,
        )
        raise
    elapsed = time.monotonic() - start
    logger.info(
        "<-- %s %s status=%s em %.2fs",
        request.method, request.url.path, response.status_code, elapsed,
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Garante que qualquer erro não previsto retorne uma resposta JSON clara
    para o frontend, em vez de deixar a requisição travada ou sem resposta.
    """
    logger.exception(
        "Erro não tratado em %s %s: %s", request.method, request.url.path, exc
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Erro interno inesperado no servidor. Verifique os logs do backend."
        },
    )


app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

app.include_router(video.router, prefix="/api/video", tags=["video"])
app.include_router(humor.router, prefix="/api/humor", tags=["humor"])
app.include_router(generation.router, prefix="/api/generation", tags=["generation"])


@app.get("/")
async def root():
    return {
        "app": "Editor Vídeo TikTok - Backend",
        "status": "online",
        "docs": "/docs",
        "humor_mode": "/api/humor/plan",
        "dynamic_montage": "v4-mobile-compatible",
    }


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": "1.2.0",
        "dynamic_montage": "v4-mobile-compatible",
    }
