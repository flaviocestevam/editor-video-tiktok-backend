from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

from . import dynamic_montage_v2 as v2
from . import dynamic_montage_v4 as v4
from . import dynamic_montage_v5 as v5
from . import video_processor

logger = logging.getLogger(__name__)
_PROCESS_LOCK = threading.Lock()
_ORIGINAL_PLAN_BUILDER = v2._build_plan


def _motion_samples(video_path: str, duration: float) -> list[tuple[float, float]]:
    """Retorna energia de movimento amostrada a 12 fps."""
    result = video_processor._run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", video_path,
            "-vf",
            "fps=12,scale=160:-2:flags=bilinear,"
            "tblend=all_mode=difference,signalstats,metadata=print",
            "-an", "-f", "null", "-",
        ],
        timeout=min(150, max(45, int(duration * 5))),
    )
    if result.returncode != 0:
        logger.warning("Impact analysis failed: %s", result.stderr[-1200:])
        return []

    current_time: float | None = None
    samples: list[tuple[float, float]] = []
    for line in f"{result.stderr}\n{result.stdout}".splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
            continue
        score_match = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if score_match and current_time is not None:
            samples.append((current_time, float(score_match.group(1))))
    return samples


def _detect_principal_impact(video_path: str, duration: float) -> tuple[float, dict[str, Any]]:
    """Escolhe o golpe principal: pico forte seguido de desaceleração/contato."""
    fallback = duration * 0.72
    if duration < 1.2:
        return fallback, {"method": "fallback", "sample_count": 0}

    samples = _motion_samples(video_path, duration)
    if len(samples) < 8:
        return fallback, {"method": "fallback", "sample_count": len(samples)}

    times = [item[0] for item in samples]
    values = [item[1] for item in samples]
    lower = duration * 0.18
    upper = duration * 0.92
    best: tuple[float, int, float, float, float] | None = None

    for index, (timestamp, value) in enumerate(samples):
        if not lower <= timestamp <= upper:
            continue
        local_values = values[max(0, index - 2): min(len(values), index + 3)]
        future_values = values[index + 1: min(len(values), index + 4)]
        if not future_values:
            continue
        local_mean = sum(local_values) / len(local_values)
        future_mean = sum(future_values) / len(future_values)
        drop_after_contact = max(0.0, value - future_mean)
        local_prominence = max(0.0, value - local_mean)
        late_bonus = timestamp / max(duration, 0.001)
        impact_score = (
            value
            + 0.55 * drop_after_contact
            + 0.35 * local_prominence
            + 0.30 * late_bonus
        )
        candidate = (impact_score, index, drop_after_contact, local_prominence, value)
        if best is None or candidate[0] > best[0]:
            best = candidate

    if best is None:
        return fallback, {"method": "fallback", "sample_count": len(samples)}

    score, index, drop, prominence, motion = best
    impact_time = times[index]
    return impact_time, {
        "method": "motion_peak_followed_by_drop",
        "sample_count": len(samples),
        "impact_score": round(score, 4),
        "motion_score": round(motion, 4),
        "drop_after_contact": round(drop, 4),
        "local_prominence": round(prominence, 4),
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
    """Mantém a montagem e injeta um replay lento logo após o golpe principal."""
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

    before = max(0.34, min(0.52, duration * 0.060))
    after = max(0.28, min(0.42, duration * 0.048))
    replay_start = max(0.0, peak - before)
    replay_end = min(duration, peak + after)
    if replay_end - replay_start < 0.36:
        replay_start = max(0.0, peak - 0.30)
        replay_end = min(duration, peak + 0.30)

    replay: dict[str, float | str] = {
        "kind": "clip",
        "start": replay_start,
        "end": replay_end,
        "speed": 0.42 if short_slowmo else 0.68,
        "reframe_zoom": 1.065,
        "shift_x": 0.08,
        "shift_y": -0.04,
        "role": "principal_impact_replay",
    }
    replay_hold: dict[str, float | str] = {
        "kind": "freeze",
        "start": max(replay_start, replay_end - 0.040),
        "end": replay_end,
        "duration": 0.18,
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

    return [*plan[:insertion], replay, replay_hold, *plan[insertion:]]


def _parse_fps(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            return float(numerator) / max(float(denominator), 1e-9)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _run_final_pass(
    *,
    output_path: str,
    remove_audio: bool,
    remove_text_overlays: bool,
    output_fps: str,
    fade: bool,
    quality_crf: int,
) -> dict[str, Any]:
    """Aplica crop de faixas de texto, FPS interpolado e fade usando a duração real."""
    probe = v5._probe(output_path)
    streams = probe.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    has_audio = any(item.get("codec_type") == "audio" for item in streams)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    duration = float((probe.get("format") or {}).get("duration") or 0.0)
    source_fps = _parse_fps(str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0"))
    if width <= 0 or height <= 0 or duration <= 0:
        raise video_processor.VideoProcessingError("Não foi possível medir o vídeo antes da etapa final.")

    filters: list[str] = []
    if remove_text_overlays:
        filters.extend([
            "crop=iw:trunc(ih*0.87/2)*2:0:trunc(ih*0.065/2)*2",
            f"scale={width}:{height}:flags=lanczos",
        ])

    temporal_interpolation = output_fps == "29.97" and abs(source_fps - (30000 / 1001)) > 0.02
    if temporal_interpolation:
        filters.append("framerate=fps=30000/1001:interp_start=0:interp_end=255:scene=100")

    if fade and duration > 0.5:
        fade_in = min(0.22, duration / 5)
        fade_out = min(0.35, duration / 4)
        fade_out_start = max(0.0, duration - fade_out)
        filters.extend([
            f"fade=t=in:st=0:d={fade_in:.3f}",
            f"fade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}",
        ])
    filters.append("format=yuv420p")

    temp_path = f"{output_path}.final.mp4"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", output_path,
        "-map", "0:v:0",
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-profile:v", "high",
        "-preset", "veryfast", "-crf", str(quality_crf),
        "-pix_fmt", "yuv420p",
    ]
    if remove_audio or not has_audio:
        command.append("-an")
    else:
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k"])
    command.extend(["-movflags", "+faststart", temp_path])

    result = video_processor._run(command, timeout=420)
    if result.returncode != 0 or not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        raise video_processor.VideoProcessingError(
            f"A etapa final de FPS/fade/texto falhou: {result.stderr[-320:]}"
        )
    os.replace(temp_path, output_path)

    final_probe = v5._probe(output_path)
    final_video_stream = next(
        (item for item in final_probe.get("streams", []) if item.get("codec_type") == "video"),
        {},
    )
    final_fps = _parse_fps(str(final_video_stream.get("avg_frame_rate") or "0"))
    return {
        "text_bands_removed": remove_text_overlays,
        "temporal_interpolation": temporal_interpolation,
        "source_fps": round(source_fps, 4),
        "output_fps": round(final_fps, 4),
        "fade_in_out": fade,
        "final_duration": round(float((final_probe.get("format") or {}).get("duration") or duration), 3),
    }


def process_dynamic_video(
    *,
    input_path: str,
    output_path: str,
    remove_audio: bool = True,
    strip_metadata: bool = True,
    remove_text_overlays: bool = True,
    **options: Any,
) -> dict[str, Any]:
    duration, has_audio, source_width, source_height = video_processor.probe_video(input_path)
    impact_time, impact_analysis = _detect_principal_impact(input_path, duration)

    if has_audio and not remove_audio:
        report = v5._standard_audio_safe(
            input_path=input_path,
            output_path=output_path,
            strip_metadata=False,
            flip_horizontal=bool(options.get("flip_horizontal", True)),
            random_trim=bool(options.get("random_trim", True)),
            crop_zoom=bool(options.get("crop_zoom", True)),
            color_adjust=bool(options.get("color_adjust", True)),
            fade=False,
            sensor_noise=int(options.get("sensor_noise", 2)),
            crop_pixels=int(options.get("crop_pixels", 4)),
            zoom_factor=float(options.get("zoom_factor", 1.02)),
            hue_degrees=float(options.get("hue_degrees", 1.0)),
            color_grade=str(options.get("color_grade", "cinematic")),
            output_fps="source",
            smooth_motion=bool(options.get("smooth_motion", True)),
            adaptive_sharpen=bool(options.get("adaptive_sharpen", True)),
            quality_crf=int(options.get("quality_crf", 18)),
        )
        final_pass = _run_final_pass(
            output_path=output_path,
            remove_audio=False,
            remove_text_overlays=remove_text_overlays,
            output_fps=str(options.get("output_fps", "29.97")),
            fade=bool(options.get("fade", True)),
            quality_crf=int(options.get("quality_crf", 18)),
        )
        metadata = v5._inject_metadata(output_path) if strip_metadata else {"written": False, "fields": {}}
        report.update({
            "engine": "dynamic_montage_v7_audio_safe",
            "metadata": metadata,
            "impact_time": round(impact_time, 3),
            "impact_analysis": impact_analysis,
            "final_pass": final_pass,
        })
        report.setdefault("applied_effects", {}).update({
            "text_bands_removed": remove_text_overlays,
            "temporal_interpolation": bool(final_pass.get("temporal_interpolation")),
            "fade_in_out": bool(options.get("fade", True)),
            "custom_metadata": bool(metadata.get("written")),
        })
        return report

    with _PROCESS_LOCK:
        original_detector = v4.legacy._detect_motion_peak
        original_plan_builder = v4.v2._build_plan
        original_attempt = v4._run_attempt
        state = {"count": 0, "successful_attempt": 0}

        def tracked_attempt(*args: Any, **kwargs: Any) -> tuple[bool, str]:
            state["count"] += 1
            valid, error = original_attempt(*args, **kwargs)
            if valid:
                state["successful_attempt"] = state["count"]
            return valid, error

        v4.legacy._detect_motion_peak = lambda *_args, **_kwargs: impact_time
        v4.v2._build_plan = _build_emphasized_plan
        v4._run_attempt = tracked_attempt
        try:
            v4.process_dynamic_video(
                input_path=input_path,
                output_path=output_path,
                strip_metadata=False,
                output_fps="source",
                fade=False,
                **{key: value for key, value in options.items() if key not in {"output_fps", "fade"}},
            )
        finally:
            v4.legacy._detect_motion_peak = original_detector
            v4.v2._build_plan = original_plan_builder
            v4._run_attempt = original_attempt

    attempt = state["successful_attempt"] or state["count"] or 1
    safe_mode = max(0, attempt - 1)
    final_pass = _run_final_pass(
        output_path=output_path,
        remove_audio=True,
        remove_text_overlays=remove_text_overlays,
        output_fps=str(options.get("output_fps", "29.97")),
        fade=bool(options.get("fade", True)),
        quality_crf=int(options.get("quality_crf", 18)),
    )
    metadata = v5._inject_metadata(output_path) if strip_metadata else {"written": False, "fields": {}}
    probe = v5._probe(output_path)
    video_stream = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), {})

    warnings: list[str] = []
    if safe_mode == 1:
        warnings.append("Modo de compatibilidade: movimento de câmera e ruído de sensor foram reduzidos.")
    elif safe_mode >= 2:
        warnings.append("Modo de compatibilidade máximo: movimento, nitidez e ruído foram reduzidos.")
    if remove_text_overlays:
        warnings.append("Remoção de textos é feita por recorte das faixas superior e inferior; textos centrais podem permanecer.")

    replay_before = max(0.34, min(0.52, duration * 0.060))
    replay_after = max(0.28, min(0.42, duration * 0.048))
    applied = {
        key: bool(value)
        for key, value in options.items()
        if key in {
            "flip_horizontal", "random_trim", "crop_zoom", "color_adjust",
            "hard_cuts", "speed_ramp", "short_slowmo", "short_speedup",
            "freeze_frame", "highlight_replay", "dynamic_reframe",
            "animated_grain_overlay", "scene_color_variation", "light_texture_overlay",
        }
    }
    applied.update({
        "fade_in_out": bool(options.get("fade", True)),
        "sensor_noise": bool(options.get("sensor_noise", 2)) and safe_mode == 0,
        "output_29_97_fps": abs(float(final_pass.get("output_fps", 0.0)) - (30000 / 1001)) < 0.03,
        "temporal_interpolation": bool(final_pass.get("temporal_interpolation")),
        "smooth_motion": bool(options.get("smooth_motion", True)) and safe_mode == 0,
        "adaptive_sharpen": bool(options.get("adaptive_sharpen", True)) and safe_mode < 2,
        "audio_removed": True,
        "audio_preserved": False,
        "custom_metadata": bool(metadata.get("written")),
        "text_bands_removed": remove_text_overlays,
        "principal_impact_replay": bool(options.get("highlight_replay", True)),
    })
    return {
        "engine": "dynamic_montage_v7",
        "attempt": attempt,
        "compatibility_mode": safe_mode,
        "source_duration": round(duration, 3),
        "source_resolution": {"width": source_width, "height": source_height},
        "output_resolution": {
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
        },
        "impact_time": round(impact_time, 3),
        "impact_analysis": impact_analysis,
        "replay": {
            "start": round(max(0.0, impact_time - replay_before), 3),
            "end": round(min(duration, impact_time + replay_after), 3),
            "speed": 0.42 if bool(options.get("short_slowmo", True)) else 0.68,
            "placed_after_principal_impact": True,
        },
        "final_pass": final_pass,
        "metadata": metadata,
        "applied_effects": applied,
        "warnings": warnings,
    }
