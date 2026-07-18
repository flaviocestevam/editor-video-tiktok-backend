from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

from . import dynamic_montage_v2 as v2
from . import dynamic_montage_v5 as v5
from . import dynamic_montage_v7 as v7
from . import video_processor

logger = logging.getLogger(__name__)
_PATCH_LOCK = threading.Lock()
_CONTEXT: dict[str, Any] = {}
_ORIGINAL_PLAN_BUILDER = v2._build_plan


def _scene_cuts(video_path: str, duration: float) -> list[float]:
    result = video_processor._run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", video_path,
            "-vf", "select='gt(scene,0.30)',showinfo",
            "-an", "-f", "null", "-",
        ],
        timeout=min(150, max(45, int(duration * 5))),
    )
    if result.returncode != 0:
        return []
    cuts: list[float] = []
    for line in f"{result.stderr}\n{result.stdout}".splitlines():
        match = re.search(r"pts_time:([0-9.]+)", line)
        if not match:
            continue
        timestamp = float(match.group(1))
        if 0.45 <= timestamp <= max(0.45, duration - 0.45):
            if not cuts or timestamp - cuts[-1] >= 0.28:
                cuts.append(timestamp)
    return cuts


def _audio_samples(video_path: str, duration: float) -> list[tuple[float, float]]:
    result = video_processor._run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", video_path,
            "-af",
            "asetnsamples=n=2048:p=1,astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-vn", "-f", "null", "-",
        ],
        timeout=min(150, max(45, int(duration * 5))),
    )
    if result.returncode != 0:
        return []
    current_time: float | None = None
    samples: list[tuple[float, float]] = []
    for line in f"{result.stderr}\n{result.stdout}".splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
            continue
        level_match = re.search(r"lavfi\.astats\.Overall\.RMS_level=([-0-9.]+)", line)
        if level_match and current_time is not None:
            samples.append((current_time, float(level_match.group(1))))
    return samples


def _nearest_motion(samples: list[tuple[float, float]], timestamp: float) -> float:
    values = [value for moment, value in samples if abs(moment - timestamp) <= 0.18]
    return max(values) if values else 0.0


def _detect_principal_impact(video_path: str, duration: float) -> tuple[float, dict[str, Any]]:
    cuts = _scene_cuts(video_path, duration)
    points = [0.0, *cuts, duration]
    scenes = [
        (points[index], points[index + 1])
        for index in range(len(points) - 1)
        if points[index + 1] - points[index] >= 0.35
    ] or [(0.0, duration)]
    scene_start, scene_end = max(scenes, key=lambda item: item[1] - item[0])
    margin = min(0.34, max(0.18, (scene_end - scene_start) * 0.07))
    eligible_start = scene_start + margin
    eligible_end = scene_end - margin

    motion = v7._motion_samples(video_path, duration)
    audio = _audio_samples(video_path, duration)
    levels = [level for _, level in audio]
    candidates: list[tuple[float, float, float, float, float]] = []
    for index, (timestamp, level) in enumerate(audio):
        if not eligible_start <= timestamp <= eligible_end:
            continue
        neighborhood = levels[max(0, index - 5):index] + levels[index + 1:min(len(levels), index + 6)]
        if not neighborhood:
            continue
        immediate = levels[max(0, index - 2):index] + levels[index + 1:min(len(levels), index + 3)]
        baseline = sum(neighborhood) / len(neighborhood)
        immediate_mean = sum(immediate) / len(immediate) if immediate else baseline
        prominence = level - baseline
        sharpness = level - immediate_mean
        motion_support = _nearest_motion(motion, timestamp)
        score = prominence + 0.45 * sharpness + 0.40 * motion_support
        if prominence >= 3.5 and level > -38.0:
            candidates.append((score, timestamp, prominence, sharpness, motion_support))

    method = "scene_limited_audio_transient"
    if candidates:
        best_score = max(item[0] for item in candidates)
        near_best = [item for item in candidates if item[0] >= best_score * 0.72]
        score, impact_time, prominence, sharpness, motion_support = max(near_best, key=lambda item: item[1])
        details = {
            "impact_score": round(score, 4),
            "audio_prominence": round(prominence, 4),
            "audio_sharpness": round(sharpness, 4),
            "motion_support": round(motion_support, 4),
        }
    else:
        method = "scene_limited_motion_peak"
        motion_candidates: list[tuple[float, float]] = []
        values = [value for _, value in motion]
        for index, (timestamp, value) in enumerate(motion):
            if not eligible_start <= timestamp <= eligible_end:
                continue
            future = values[index + 1:min(len(values), index + 4)]
            local = values[max(0, index - 2):min(len(values), index + 3)]
            if not future or not local:
                continue
            drop = max(0.0, value - sum(future) / len(future))
            prominence = max(0.0, value - sum(local) / len(local))
            motion_candidates.append((value + 0.8 * drop + 0.45 * prominence, timestamp))
        if motion_candidates:
            best_score = max(item[0] for item in motion_candidates)
            near_best = [item for item in motion_candidates if item[0] >= best_score * 0.78]
            score, impact_time = max(near_best, key=lambda item: item[1])
            details = {"impact_score": round(score, 4)}
        else:
            method = "fallback_longest_scene"
            impact_time = min(eligible_end, max(eligible_start, scene_start + (scene_end - scene_start) * 0.72))
            details = {}

    _CONTEXT.clear()
    _CONTEXT.update({
        "source_duration": duration,
        "scene_start": scene_start,
        "scene_end": scene_end,
    })
    return impact_time, {
        "method": method,
        "scene_start": round(scene_start, 3),
        "scene_end": round(scene_end, 3),
        "scene_cuts": [round(value, 3) for value in cuts],
        **details,
    }


def _build_emphasized_plan(
    duration: float,
    peak: float,
    *,
    hard_cuts: bool,
    speed_ramp: bool,
    short_slowmo: bool,
    short_speedup: bool,
    freeze_frame: bool,
    highlight_replay: bool,
) -> list[dict[str, float | str]]:
    plan = _ORIGINAL_PLAN_BUILDER(
        duration,
        peak,
        hard_cuts=hard_cuts,
        speed_ramp=speed_ramp,
        short_slowmo=short_slowmo,
        short_speedup=short_speedup,
        freeze_frame=freeze_frame,
        highlight_replay=False,
    )
    if not highlight_replay or duration < 1.5:
        return plan

    source_duration = float(_CONTEXT.get("source_duration", duration))
    trim = max(0.0, (source_duration - duration) / 2)
    scene_start = max(0.0, float(_CONTEXT.get("scene_start", 0.0)) - trim)
    scene_end = min(duration, float(_CONTEXT.get("scene_end", source_duration)) - trim)
    replay_start = max(scene_start + 0.04, peak - 0.44)
    replay_end = min(scene_end - 0.04, peak + 0.24)
    if replay_end - replay_start < 0.36:
        replay_start = max(scene_start + 0.02, peak - 0.30)
        replay_end = min(scene_end - 0.02, peak + 0.22)
    if replay_end - replay_start < 0.24:
        return plan

    _CONTEXT["replay_start"] = replay_start + trim
    _CONTEXT["replay_end"] = replay_end + trim
    replay = {
        "kind": "clip", "start": replay_start, "end": replay_end,
        "speed": 0.50 if short_slowmo else 0.72,
        "reframe_zoom": 1.035, "shift_x": 0.03, "shift_y": -0.02,
        "role": "scene_limited_principal_impact_replay",
    }
    hold = {
        "kind": "freeze",
        "start": max(replay_start, peak - 0.018),
        "end": min(replay_end, peak + 0.018),
        "duration": 0.10,
        "role": "principal_impact_hold",
    }

    insertion = len(plan)
    for index, item in enumerate(plan):
        start = float(item["start"])
        end = float(item["end"])
        if start <= peak <= end:
            insertion = index + 1
            while insertion < len(plan) and plan[insertion].get("kind") == "freeze":
                insertion += 1
            break
        if start > peak:
            insertion = index
            break
    return [*plan[:insertion], replay, hold, *plan[insertion:]]


def _run_final_pass(
    *,
    output_path: str,
    remove_audio: bool,
    remove_text_overlays: bool,
    output_fps: str,
    fade: bool,
    quality_crf: int,
) -> dict[str, Any]:
    probe = v5._probe(output_path)
    streams = probe.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    has_audio = any(item.get("codec_type") == "audio" for item in streams)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    duration = float((probe.get("format") or {}).get("duration") or 0.0)
    source_fps = v7._parse_fps(str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0"))
    if width <= 0 or height <= 0 or duration <= 0:
        raise video_processor.VideoProcessingError("Não foi possível medir o vídeo antes da etapa final.")

    filters: list[str] = []
    if remove_text_overlays:
        filters.extend([
            "crop=iw:trunc(ih*0.90/2)*2:0:trunc(ih*0.05/2)*2",
            f"scale={width}:{height}:flags=lanczos",
        ])
    frame_rate_conversion = output_fps == "29.97" and abs(source_fps - (30000 / 1001)) > 0.02
    if frame_rate_conversion:
        filters.append("fps=30000/1001")
    if fade and duration > 0.5:
        fade_in = min(0.18, duration / 6)
        fade_out = min(0.28, duration / 5)
        filters.extend([
            f"fade=t=in:st=0:d={fade_in:.3f}",
            f"fade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}",
        ])
    filters.append("format=yuv420p")

    final_crf = max(14, quality_crf - 3)
    temp_path = f"{output_path}.stable-final.mp4"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", output_path,
        "-map", "0:v:0", "-vf", ",".join(filters),
        "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
        "-crf", str(final_crf), "-pix_fmt", "yuv420p",
    ]
    if remove_audio or not has_audio:
        command.append("-an")
    else:
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "160k"])
    command.extend(["-movflags", "+faststart", temp_path])
    result = video_processor._run(command, timeout=480)
    if result.returncode != 0 or not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        raise video_processor.VideoProcessingError(f"A etapa final de qualidade/fade falhou: {result.stderr[-320:]}")
    os.replace(temp_path, output_path)

    final_probe = v5._probe(output_path)
    final_stream = next((item for item in final_probe.get("streams", []) if item.get("codec_type") == "video"), {})
    return {
        "text_bands_removed": remove_text_overlays,
        "temporal_interpolation": False,
        "frame_rate_conversion": frame_rate_conversion,
        "source_fps": round(source_fps, 4),
        "output_fps": round(v7._parse_fps(str(final_stream.get("avg_frame_rate") or "0")), 4),
        "fade_in_out": fade,
        "final_crf": final_crf,
        "final_duration": round(float((final_probe.get("format") or {}).get("duration") or duration), 3),
    }


def process_dynamic_video(
    *,
    input_path: str,
    output_path: str,
    remove_audio: bool = True,
    strip_metadata: bool = True,
    remove_text_overlays: bool = False,
    **options: Any,
) -> dict[str, Any]:
    safe_options = dict(options)
    safe_options.setdefault("flip_horizontal", False)
    safe_options.setdefault("output_fps", "source")

    with _PATCH_LOCK:
        original_detector = v7._detect_principal_impact
        original_builder = v7._build_emphasized_plan
        original_final_pass = v7._run_final_pass
        v7._detect_principal_impact = _detect_principal_impact
        v7._build_emphasized_plan = _build_emphasized_plan
        v7._run_final_pass = _run_final_pass
        context_snapshot: dict[str, Any] = {}
        try:
            report = v7.process_dynamic_video(
                input_path=input_path,
                output_path=output_path,
                remove_audio=remove_audio,
                strip_metadata=strip_metadata,
                remove_text_overlays=remove_text_overlays,
                **safe_options,
            )
            context_snapshot = dict(_CONTEXT)
        finally:
            v7._detect_principal_impact = original_detector
            v7._build_emphasized_plan = original_builder
            v7._run_final_pass = original_final_pass
            _CONTEXT.clear()

    report["engine"] = "dynamic_montage_v8_stable"
    replay = report.setdefault("replay", {})
    replay.update({
        "start": round(float(context_snapshot.get("replay_start", replay.get("start", 0.0))), 3),
        "end": round(float(context_snapshot.get("replay_end", replay.get("end", 0.0))), 3),
        "speed": 0.50 if bool(safe_options.get("short_slowmo", True)) else 0.72,
        "restricted_to_same_scene": True,
    })
    final_pass = report.setdefault("final_pass", {})
    final_pass["temporal_interpolation"] = False
    applied = report.setdefault("applied_effects", {})
    applied.update({
        "flip_horizontal": bool(safe_options.get("flip_horizontal", False)),
        "text_bands_removed": remove_text_overlays,
        "temporal_interpolation": False,
        "frame_rate_conversion": bool(final_pass.get("frame_rate_conversion")),
    })
    warnings = report.setdefault("warnings", [])
    if remove_text_overlays:
        warnings.append("Recorte de texto foi ativado manualmente; permanece desligado por padrão para preservar o quadro.")
    if bool(final_pass.get("frame_rate_conversion")):
        warnings.append("FPS convertido sem interpolação para evitar ghosting; permanece desligado por padrão.")
    return report
