from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from app.services import video_processor

logger = logging.getLogger(__name__)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def _detect_motion_peak(video_path: str, duration: float) -> float:
    fallback = duration * 0.55
    if duration < 1.2:
        return fallback

    result = video_processor._run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", video_path,
            "-vf", "fps=8,scale=160:-2:flags=bilinear,tblend=all_mode=difference,signalstats,metadata=print",
            "-an", "-f", "null", "-",
        ],
        timeout=min(120, max(30, int(duration * 4))),
    )
    if result.returncode != 0:
        logger.warning("Motion analysis failed; using fallback: %s", result.stderr[-1200:])
        return fallback

    current_time: float | None = None
    samples: list[tuple[float, float]] = []
    for line in f"{result.stderr}\n{result.stdout}".splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
            continue
        score_match = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if score_match and current_time is not None:
            samples.append((float(score_match.group(1)), current_time))

    lower = duration * 0.14
    upper = duration * 0.86
    eligible = [(score, timestamp) for score, timestamp in samples if lower <= timestamp <= upper]
    if not eligible:
        return fallback

    eligible.sort(key=lambda item: item[0], reverse=True)
    for score, timestamp in eligible[:8]:
        neighbors = [value for value, moment in eligible if abs(moment - timestamp) <= 0.28]
        if len(neighbors) >= 2 and sum(neighbors) / len(neighbors) >= score * 0.60:
            return timestamp
    return eligible[0][1]


def _spatial_filters(
    *,
    source_width: int,
    source_height: int,
    output_duration: float,
    flip_horizontal: bool,
    crop_zoom: bool,
    crop_pixels: int,
    zoom_factor: float,
    color_adjust: bool,
    hue_degrees: float,
    color_grade: str,
    smooth_motion: bool,
    adaptive_sharpen: bool,
    sensor_noise: int,
) -> list[str]:
    filters: list[str] = []
    if flip_horizontal:
        filters.append("hflip")

    if crop_pixels or zoom_factor > 1.0:
        ratio = 1.0 / zoom_factor
        filters.extend([
            "crop="
            f"trunc((iw-{crop_pixels * 2})*{ratio:.8f}/2)*2:"
            f"trunc((ih-{crop_pixels * 2})*{ratio:.8f}/2)*2",
            f"scale={source_width}:{source_height}:flags=lanczos",
        ])
    elif crop_zoom:
        filters.extend([
            "crop=trunc(iw*0.96/2)*2:trunc(ih*0.96/2)*2",
            f"scale={source_width}:{source_height}:flags=lanczos",
        ])

    if color_adjust:
        filters.append("eq=brightness=0.01:contrast=1.03:saturation=1.04")
    if hue_degrees:
        filters.append(f"hue=h={hue_degrees:.3f}")

    grades = {
        "warm": "colorbalance=rs=.025:gs=.008:bs=-.018",
        "cool": "colorbalance=rs=-.018:gs=.004:bs=.025",
        "cinematic": "eq=contrast=1.035:saturation=.96:gamma=.99,colorbalance=rs=.012:bs=.015",
        "vintage": "eq=contrast=.97:saturation=.88:gamma=1.015,colorbalance=rs=.022:bs=-.012",
    }
    if color_grade != "none":
        filters.append(grades[color_grade])

    if smooth_motion:
        progress = f"min(t/{max(output_duration, 0.001):.6f},1)"
        filters.extend([
            "scale="
            f"w='trunc(iw*(1+0.025*{progress})/2)*2':"
            f"h='trunc(ih*(1+0.025*{progress})/2)*2':eval=frame:flags=lanczos",
            f"crop={source_width}:{source_height}:"
            "x='(iw-ow)/2+sin(t*0.70)*(iw-ow)*0.12':"
            "y='(ih-oh)/2+cos(t*0.55)*(ih-oh)*0.10'",
        ])

    if adaptive_sharpen:
        amount = 0.25 if source_width >= 1080 or source_height >= 1920 else 0.35
        filters.append(f"unsharp=5:5:{amount:.2f}:5:5:0.0")
    if sensor_noise:
        filters.append(f"noise=alls={sensor_noise}:allf=t")
    return filters


def _append_jump_cuts(plan: list[dict[str, float | str]], start: float, end: float, enabled: bool) -> None:
    length = end - start
    if length < 0.06:
        return
    if not enabled or length < 1.25:
        plan.append({"kind": "clip", "start": start, "end": end, "speed": 1.0})
        return

    pieces = 3 if length >= 3.2 else 2
    gap = min(0.085, max(0.045, length * 0.018))
    usable = length - gap * (pieces - 1)
    if usable <= 0.2:
        plan.append({"kind": "clip", "start": start, "end": end, "speed": 1.0})
        return

    piece_length = usable / pieces
    cursor = start
    for index in range(pieces):
        piece_end = end if index == pieces - 1 else cursor + piece_length
        if piece_end - cursor >= 0.04:
            plan.append({"kind": "clip", "start": cursor, "end": piece_end, "speed": 1.0})
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

    margin = min(0.24, duration * 0.08)
    highlight_length = _clamp(duration * 0.115, 0.42, 0.82)
    if duration < 2.0:
        highlight_length = min(highlight_length, duration * 0.38)
    slow_start = _clamp(
        peak - highlight_length * 0.46,
        margin,
        max(margin, duration - highlight_length - margin),
    )
    slow_end = min(duration - margin, slow_start + highlight_length)

    ramp_length = _clamp(duration * 0.10, 0.34, 0.72) if speed_ramp else 0.0
    ramp_start = max(0.0, slow_start - ramp_length)
    speedup_length = _clamp(duration * 0.075, 0.28, 0.56) if short_speedup else 0.0
    speedup_end = min(duration, slow_end + speedup_length)

    plan: list[dict[str, float | str]] = []
    _append_jump_cuts(plan, 0.0, ramp_start, hard_cuts)

    if speed_ramp and slow_start - ramp_start >= 0.21:
        phase = (slow_start - ramp_start) / 3
        for index, speed in enumerate((1.05, 1.18, 1.36)):
            start = ramp_start + index * phase
            end = slow_start if index == 2 else start + phase
            plan.append({"kind": "clip", "start": start, "end": end, "speed": speed})
    elif slow_start > ramp_start:
        plan.append({
            "kind": "clip", "start": ramp_start, "end": slow_start,
            "speed": 1.28 if short_speedup else 1.0,
        })

    plan.append({
        "kind": "clip", "start": slow_start, "end": slow_end,
        "speed": 0.72 if short_slowmo else 1.0,
    })

    if freeze_frame and duration >= 1.2:
        frame_span = min(0.05, max(0.025, duration / 250))
        plan.append({
            "kind": "freeze",
            "start": max(slow_start, slow_end - frame_span),
            "end": slow_end,
            "duration": _clamp(duration * 0.065, 0.34, 0.62),
        })

    if highlight_replay and duration >= 1.5:
        replay_length = min(_clamp(duration * 0.10, 0.46, 0.72), slow_end - slow_start)
        plan.append({
            "kind": "clip",
            "start": max(slow_start, slow_end - replay_length),
            "end": slow_end,
            "speed": 0.88 if short_slowmo else 1.08,
        })

    if speedup_end > slow_end:
        plan.append({
            "kind": "clip", "start": slow_end, "end": speedup_end,
            "speed": 1.32 if short_speedup else 1.0,
        })
    _append_jump_cuts(plan, speedup_end, duration, hard_cuts)
    return [item for item in plan if float(item["end"]) - float(item["start"]) >= 0.025]


def _build_filter_complex(
    *,
    trim: float,
    source_duration: float,
    output_duration: float,
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

    labels = "".join(f"[s{index}]" for index in range(len(plan)))
    base = [
        f"trim=start={trim:.6f}:end={source_duration - trim:.6f}",
        "setpts=PTS-STARTPTS", *spatial_filters, "setsar=1",
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
                f"settb=AVTB[{label}]"
            )
            final_duration += end - start + freeze_duration
        else:
            speed = float(item.get("speed", 1.0))
            graph.append(
                f"[s{index}]trim=start={start:.6f}:end={end:.6f},"
                f"setpts=(PTS-STARTPTS)/{speed:.6f},settb=AVTB[{label}]"
            )
            final_duration += (end - start) / speed

    post: list[str] = []
    if output_fps == "29.97":
        post.append("fps=30000/1001")
    if fade and final_duration > 0.4:
        length = min(0.25, final_duration / 4)
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

    filters = _spatial_filters(
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
    peak = _detect_motion_peak(input_path, duration)
    filter_complex, final_duration = _build_filter_complex(
        trim=trim,
        source_duration=duration,
        output_duration=output_duration,
        spatial_filters=filters,
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
        logger.error("Dynamic ffmpeg failed: %s", result.stderr[-6000:])
        try:
            os.remove(output_path)
        except FileNotFoundError:
            pass
        raise video_processor.VideoProcessingError("Não foi possível criar a montagem dinâmica.")
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise video_processor.VideoProcessingError("A montagem dinâmica não gerou um arquivo válido.")
