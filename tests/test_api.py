import io
import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.routers import video
from app.services.downloader import DownloadError, download_video_from_url
from main import app


client = TestClient(app)


def test_health_and_root():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200


def test_upload_rejects_non_mp4():
    response = client.post("/api/video/upload", files={"file": ("bad.txt", b"bad", "text/plain")})
    assert response.status_code == 400


def test_downloader_rejects_spoofed_domain(tmp_path):
    with pytest.raises(DownloadError):
        download_video_from_url("https://tiktok.com.attacker.example/video", str(tmp_path))


def test_full_upload_process_download_flow(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    temp_dir = tmp_path / "temp"
    for directory in (upload_dir, output_dir, temp_dir):
        directory.mkdir()
    monkeypatch.setattr(video, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(video, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(video, "TEMP_DIR", str(temp_dir))

    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=24", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)],
        check=True,
    )
    with source.open("rb") as stream:
        upload = client.post("/api/video/upload", files={"file": ("clip.mp4", stream, "video/mp4")})
    assert upload.status_code == 200

    processed = client.post(
        "/api/video/process",
        data={"file_id": upload.json()["file_id"], "remove_audio": "true", "fade": "true"},
    )
    assert processed.status_code == 200, processed.text
    result = client.get(processed.json()["download_url"])
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("video/mp4")
    assert len(result.content) > 0


def test_new_editing_options_are_applied_in_one_pass(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    temp_dir = tmp_path / "temp"
    for directory in (upload_dir, output_dir, temp_dir):
        directory.mkdir()
    monkeypatch.setattr(video, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(video, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(video, "TEMP_DIR", str(temp_dir))

    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=size=320x568:rate=30", "-t", "1", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(source)],
        check=True,
    )
    with source.open("rb") as stream:
        upload = client.post("/api/video/upload", files={"file": ("clip.mp4", stream, "video/mp4")})

    processed = client.post("/api/video/process", data={
        "file_id": upload.json()["file_id"],
        "flip_horizontal": "false", "random_trim": "false", "crop_zoom": "false",
        "speed_change": "false", "color_adjust": "false", "fade": "false",
        "sensor_noise": "2", "crop_pixels": "4", "zoom_factor": "1.02",
        "hue_degrees": "1.0", "color_grade": "warm", "output_fps": "29.97",
        "smooth_motion": "true", "adaptive_sharpen": "true",
        "quality_crf": "18",
    })
    assert processed.status_code == 200, processed.text
    result = client.get(processed.json()["download_url"])
    assert result.status_code == 200
    assert len(result.content) > 0

    output_path = output_dir / processed.json()["output_filename"]
    metadata = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream_tags=encoder",
         "-show_entries", "format_tags", "-of", "default=nw=1", str(output_path)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "Lavc" not in metadata
    assert "libx264" not in metadata
    assert "encoder=H.264" in metadata
    assert "com.apple.quicktime.make=Apple" in metadata
    assert "com.apple.quicktime.model=iPhone 15 Pro Max" in metadata
    assert "com.apple.quicktime.software=iOS" in metadata
    assert "com.apple.quicktime.location.name=Rio de Janeiro, Brasil" in metadata
    assert "com.apple.quicktime.location.ISO6709=-22.9068-043.1729+002.0/" in metadata


def test_rejects_unsafe_processing_ranges(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    temp_dir = tmp_path / "temp"
    for directory in (upload_dir, output_dir, temp_dir):
        directory.mkdir()
    monkeypatch.setattr(video, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(video, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(video, "TEMP_DIR", str(temp_dir))

    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=160x284:rate=24", "-t", ".5", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(source)], check=True)
    with source.open("rb") as stream:
        upload = client.post("/api/video/upload", files={"file": ("clip.mp4", stream, "video/mp4")})
    response = client.post("/api/video/process", data={
        "file_id": upload.json()["file_id"], "sensor_noise": "8"
    })
    assert response.status_code == 400
    assert "entre 0 e 4" in response.json()["detail"]
