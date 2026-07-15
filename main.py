main.pyimport os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import video

logging.basicConfig(level=logging.INFO)
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
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

app.include_router(video.router, prefix="/api/video", tags=["video"])


@app.get("/")
async def root():
    return {
        "app": "Editor Vídeo TikTok - Backend",
        "status": "online",
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    return {"status": "ok"}
