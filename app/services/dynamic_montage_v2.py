from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from . import dynamic_montage as legacy
from . import video_processor

logger = logging.getLogger(__name__)
_clamp = legacy._clamp


def _append_jump_cuts(
    plan: list[dict[str, float | str]],
    start: float,
    end: float,
    enabled: bool,
) -> None:
    """Remove visible 0.12–0.25 s gaps and vary framing between pieces."""
    length = end - start
    if length < 0.06:
        return
    if not enabled or length < 1.05:
        plan.append({"kind": "clip", "start": start, "end": end, "speed": 1.0})
        return

    pieces = 4 if length >= 4.0 else 3 if length >= 2.35 else 2
    gap = _clamp(length * 0.055, 0.12, 0.25)
    usable = length - gap * (pieces - 1)
    if usable <= pieces * 0.16:
        plan.append({"kind": "clip", "start": start, "end": end, "speed": 1.0})
        return

    piece_length = usable / pieces
    cursor = start
    zooms = (1.000, 1.032, 1.055, 1.024)
    shifts_x = (0.00, -0.22, 0.20, -0.10)
    shifts_y = (0.00, 0.10, -0.08, 0.05)
    for index in range(pieces):
        piece_end = end if index == pieces - 1 else cursor + piece_length
        if piece_end - cursor >= 0.10:
            style = index % len(zooms)
            plan.append({
                "kind": "clip",
                "start": cursor,
                "end": piece_end,
                "speed": 1.0,
                "reframe_zoom": zooms[style],
                "shift_x": shifts_x[style],
                "shift_y": shifts_y[style],
            })
        cursor = piece_end + gap


def _build_plan(
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
    if duration < 0.75:
        return [{"kind": "clip", "start": 0.0, "end": duration, "speed": 1.0}]

    margin = min(0.28, duration * 0.075)
    slow_speed = 0.62 if short_slowmo else 1.0
    target_output = (
        _clamp(duration * 0.23, 1.10, 1.90)
        if short_slowmo
        else _clamp(duration * 0.13, 0.55, 0.95)
    )
    highlight_length = target_output * slow_speed
    max_highlight = max(0.28, duration - margin * 2)
    highlight_length = _clamp(highlight_length, 0.52, min(1.24, max_highlight))
    if duration < 2.0:
        highlight_length = min(highlight_length, duration * 0.42)

    slow_start = _clamp(
        peak - highlight_length * 0.48,
        margin,
        max(margin, duration - highlight_length - margin),
    )
    slow_end = min(duration - margin, slow_start + highlight_length)

    ramp_length = _clamp(duration * 0.11, 0.42, 0.82) if speed_ramp else 0.0
    ramp_start = max(0.0, slow_start - ramp_length)
    speedup_length = _clamp(duration * 0.085, 0.34, 0.68) if short_speedup else 0.0
    speedup_end = min(duration, slow_end + speedup_length)

    plan: list[dict[str, float | str]] = []
    _append_jump_cuts(plan, 0.0, ramp_start, hard_cuts)

    if speed_ramp and slow_start - ramp_start >= 0.24:
        phase = (slow_start - ramp_start) / 3
        for index, speed in enumerate((1.06, 1.22, 1.42)):
            start = ramp_start + index * phase
            end = slow_start if index == 2 else start + phase
            plan.append({
                "kind": "clip",
                "start": start,
                "end": end,
                "speed": speed,
                "reframe_zoom": 1.0 + index * 0.018,
                "shift_x": (-0.08, 0.10, -0.05)[index],
                "shift_y": (0.04, -0.05, 0.02)[index],
            })
    elif slow_start > ramp_start:
        plan.append({
            "kind": "clip",
            "start": ramp_start,
            "end": slow_start,
            "speed": 1.30 if short_speedup else 1.0,
            "reframe_zoom": 1.025,
        })

    plan.append({
        "kind": "clip",
        "start": slow_start,
        "end": slow_end,
        "speed": slow_speed,
        "zoom_out": 1.0 if short_slowmo else 0.0,
    })

    if freeze_frame and duration >= 1.2:
        frame_span = min(0.05, max(0.025, duration / 250))
        plan.append({
            "kind": "freeze",
            "start": max(slow_start, slow_end - frame_span),
            "end": slow_end,
            "duration": _clamp(duration * 0.055, 0.32, 0.52),
        })

    if highlight_replay and duration >= 1.5:
        replay_length = min(_clamp(duration * 0.105, 0.52, 0.82), slow_end - slow_start)
        plan.append({
            "kind": "clip",
            "start": max(slow_start, slow_end - replay_length),
            "end": slow_end,
            "speed": 0.90 if short_slowmo else 1.08,
            "reframe_zoom": 1.045,
            "shift_x": 0.12,
            "shift_y": -0.05,
        })

    if speedup_end > slow_end:
        plan.append({
            "kind": "clip",
            "start": slow_end,
            "end": speedup_end,
            "speed": 1.34 if short_speedup else 1.0,
            "reframe_zoom": 1.035,
            "shift_x": -0.10,
            "shift_y": 0.04,
        })
    _append_jump_cuts(plan, speedup_end, duration, hard_cuts)
    return [item for item in plan if float(item["end"]) - float(item["start"]) >= 0.025]


def _segment_reframe_filter(
    *,
    item: dict[str, float | str],
    width: int,
    height: int,
    output_duration: float,
) -> str:
    if float(item.get("zoom_out", 0.0)) > 0:
        progress = f"min(t/{max(output_duration, 0.001):.6f},1)"
        zoom = f"1+0.095*(1-{progress})"
        return (
            "scale="
            f"w='trunc(iw*({zoom})/2)*2':"
            f"h='trunc(ih*({zoom})/2)*2':eval=frame:flags=lanczos,"
            f"crop={width}:{height}:x='(iw-ow)/2':y='(ih-oh)/2'"
        )

    zoom = float(item.get("reframe_zoom", 1.0))
    if zoom <= 1.0005:
        return ""
    shift_x = float(item.get("shift_x", 0.0))
    shift_y = float(item.get("shift_y", 0.0))
    return (
        f"scale=trunc(iw*{zoom:.6f}/2)*2:trunc(ih*{zoom:.6f}/2)*2:flags=lanczos,"
        f"crop={width}:{height}:"
        f"x='(iw-ow)/2+({shift_x:.4f})*(iw-ow)':"
        f"y='(ih-oh)/2+({shift_y:.4f})*(ih-oh)'"
    )


def _build_filter_complex(
    *,
    trim: float,
    source_duration: float,
    output_duration: float,
    width: int,
    height: int,
    spatial_filters: list[str],
    peak_source_time: float,
    hard_cuts: bool,
    speed_ramp: bool,
    short_slowmo: bool,
    short_speedup: bool,
    freeze_frame: bool,
    highlight_replay: bool,
    output_fps: str,
    fade: bool,
) -> tuple[str, float]:
    plan = _build_plan(
        output_duration,
        _clamp(peak_source_time - trim, 0.0, output_duration),
        hard_cuts=hard_cuts,
        speed_ramp=speed_ramp,
        short_slowmo=short_slowmo,
        short_speedup=short_speedup,
        freeze_frame=freeze_frame,
        highlight_replay=highlight_replay,
    )
    if not plan:
        plan = [{"kind": "clip", "start": 0.0, "end": output_duration, "speed": 1.0}]

    labels = "".join(f"[s{i}]" for i in range(len(plan)))
    base = [
        f"trim=start={trim:.6f}:end={source_duration - trim:.6f}",
        "setpts=PTS-STARTPTS",
        *spatial_filters,
        "setsar=1",
    ]
    graph = [f"[0:v]{','.join(base)},split={len(plan)}{labels}"]
    outputs: list[str] = []
    final_duration = 0.0

    for index, item in enumerate(plan):
        start = float(item["start"])
        end = float(item["end"])
        label = f"v{index}"
        outputs.append(f"[{label}]")
        if item["kind"] == "freeze":
            freeze_duration = float(item["duration"])
            graph.append(
                f"[s{index}]trim=start={start:.6f}:end={end:.6f},"
                f"setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={freeze_duration:.6f},"
                f"setsar=1,settb=AVTB[{label}]"
            )
            final_duration += end - start + freeze_duration
            continue

        speed = float(item.get("speed", 1.0))
        segment_duration = (end - start) / speed
        filters = [
            f"trim=start={start:.6f}:end={end:.6f}",
            f"setpts=(PTS-STARTPTS)/{speed:.6f}",
        ]
        reframe = _segment_reframe_filter(
            item=item,
            width=width,
            height=height,
            output_duration=segment_duration,
        )
        if reframe:
            filters.append(reframe)
        filters.extend(["setsar=1", "settb=AVTB"])
        graph.append(f"[s{index}]{','.join(filters)}[{label}]")
        final_duration += segment_duration

    post: list[str] = []
    if output_fps == "29.97":
        post.append("fps=30000/1001")
    if fade and final_duration > 0.4:
        length = min(0.22, final_duration / 4)
        post.extend([
            f"fade=t=in:st=0:d={length:.3f}",
            f"fade=t=out:st={max(0.0, final_duration - length):.3f}:d={length:.3f}",
        ])
    post.append("format=yuv420p")
    graph.append(
        f"{''.join(outputs)}concat=n={len(plan)}:v=1:a=0[montage];"
        f"[montage]{','.join(post)}[vout]"
    )
    return ";".join(graph), final_duration


def process_dynamic_video(
    *,
    input_path: str,
    output_path: str,
    flip_horizontal: bool = True,
    random_trim: bool = True,
    crop_zoom: bool = True,
    color_adjust: bool = True,
    fade: bool = True,
    strip_metadata: bool = True,
    sensor_noise: int = 2,
    crop_pixels: int = 4,
    zoom_factor: float = 1.02,
    hue_degrees: float = 1.0,
    color_grade: str = "cinematic",
    output_fps: str = "29.97",
    smooth_motion: bool = True,
    adaptive_sharpen: bool = True,
    hard_cuts: bool = True,
    speed_ramp: bool = True,
    short_slowmo: bool = True,
    short_speedup: bool = True,
    freeze_frame: bool = True,
    highlight_replay: bool = True,
    quality_crf: int = 18,
) -> None:
    if not 0 <= sensor_noise <= 4:
        raise video_processor.VideoProcessingError("O ruído deve estar entre 0 e 4.")
    if not 0 <= crop_pixels <= 8:
        raise video_processor.VideoProcessingError("O recorte deve estar entre 0 e 8 pixels por borda.")
    if not 1.0 <= zoom_factor <= 1.05:
        raise video_processor.VideoProcessingError("O zoom deve estar entre 1.00x e 1.05x.")
    if not -3.0 <= hue_degrees <= 3.0:
        raise video_processor.VideoProcessingError("A matiz deve estar entre -3 e 3 graus.")
    if color_grade not in {"none", "warm", "cool", "cinematic", "vintage"}:
        raise video_processor.VideoProcessingError("Preset de cor inválido.")
    if output_fps not in {"source", "29.97"}:
        raise video_processor.VideoProcessingError("FPS de saída inválido.")
    if not 17 <= quality_crf <= 20:
        raise video_processor.VideoProcessingError("A qualidade CRF deve estar entre 17 e 20.")

    duration, _, width, height = video_processor.probe_video(input_path)
    trim = min(duration * 0.025, 0.35) if random_trim and duration > 1 else 0.0
    output_duration = duration - trim * 2
    if output_duration <= 0.2:
        raise video_processor.VideoProcessingError("O vídeo é curto demais para ser processado.")

    spatial_filters = legacy._spatial_filters(
        source_width=width,
        source_height=height,
        output_duration=output_duration,
        flip_horizontal=flip_horizontal,
        crop_zoom=crop_zoom,
        crop_pixels=crop_pixels,
        zoom_factor=zoom_factor,
        color_adjust=color_adjust,
        hue_degrees=hue_degrees,
        color_grade=color_grade,
        smooth_motion=smooth_motion,
        adaptive_sharpen=adaptive_sharpen,
        sensor_noise=sensor_noise,
    )
    peak = legacy._detect_motion_peak(input_path, duration)
    filter_complex, final_duration = _build_filter_complex(
        trim=trim,
        source_duration=duration,
        output_duration=output_duration,
        width=width,
        height=height,
        spatial_filters=spatial_filters,
        peak_source_time=peak,
        hard_cuts=hard_cuts,
        speed_ramp=speed_ramp,
        short_slowmo=short_slowmo,
        short_speedup=short_speedup,
        freeze_frame=freeze_frame,
        highlight_replay=highlight_replay,
        output_fps=output_fps,
        fade=fade,
    )
    logger.info("Motion peak %.3fs; estimated dynamic output %.3fs", peak, final_duration)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", input_path,
        "-filter_complex", filter_complex, "-map", "[vout]",
        "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
        "-crf", str(quality_crf), "-pix_fmt", "yuv420p", "-an",
    ]
    if strip_metadata:
        processed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        command.extend([
            "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact",
            "-flags:v", "+bitexact", "-metadata:s:v:0", "encoder=H.264",
            "-metadata", f"creation_time={processed_at}",
            "-metadata", "com.apple.quicktime.make=Apple",
            "-metadata", "com.apple.quicktime.model=iPhone 15 Pro Max",
            "-metadata", "com.apple.quicktime.software=iOS",
            "-metadata", "com.apple.quicktime.location.ISO6709=-22.9068-043.1729+002.0/",
            "-metadata", "com.apple.quicktime.location.name=Rio de Janeiro, Brasil",
        ])
    command.extend(["-movflags", "+faststart+use_metadata_tags", output_path])

    result = video_processor._run(command, timeout=420)
    if result.returncode != 0:
        logger.error("Dynamic v2 ffmpeg failed: %s", result.stderr[-6000:])
        try:
            os.remove(output_path)
        except FileNotFoundError:
            pass
        raise video_processor.VideoProcessingError("Não foi possível criar a montagem dinâmica.")
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise video_processor.VideoProcessingError("A montagem dinâmica não gerou um arquivo válido.")
