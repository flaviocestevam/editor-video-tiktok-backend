from __future__ import annotations

from dataclasses import dataclass

from app.services import dynamic_montage
from app.services import video_processor


PHRASE_BANK: dict[str, list[str]] = {
    "opening": [
        "Ele veio explicar. Ela preferiu demonstrar.",
        "Tutorial de hoje: como perder o controle com confiança.",
        "Ele achou que era aproximação. Era aviso prévio.",
    ],
    "approach": [
        "A pegada parecia romântica. A intenção, nem tanto.",
        "Ele entrou cheio de atitude. Ela ajustou a posição.",
        "Relacionamento saudável começa com comunicação. Ou uma boa pegada.",
    ],
    "highlight": [
        "Ele pediu pressão. Ela levou o pedido a sério.",
        "Domínio masculino? O replay pediu revisão.",
        "O encaixe foi perfeito. Para ela.",
    ],
    "freeze": [
        "Controle da situação transferido com sucesso.",
        "Foi aqui que ele percebeu quem mandava na relação.",
        "Resistência masculina detectada. Duração: limitada.",
    ],
    "replay": [
        "De novo, porque ele ainda estava processando.",
        "Replay oficial da troca de poder.",
        "Ele jurou que mandava. A câmera pediu provas.",
    ],
    "ending": [
        "Ela não discutiu a relação. Finalizou o assunto.",
        "Posição definida. Ego em recuperação.",
        "Igualdade é isso: ela também sabe colocar pressão.",
    ],
}


@dataclass(frozen=True)
class Moment:
    id: str
    label: str
    start: float
    end: float
    position: str
    suggestions: list[str]


def _round_time(value: float) -> float:
    return round(max(0.0, value), 3)


def build_humor_plan(video_path: str) -> dict[str, object]:
    duration, _, width, height = video_processor.probe_video(video_path)
    trim = min(duration * 0.025, 0.35) if duration > 1 else 0.0
    usable = max(0.3, duration - trim * 2)
    peak_source = dynamic_montage.legacy._detect_motion_peak(video_path, duration)
    peak = max(0.0, min(usable, peak_source - trim))

    highlight_start = max(0.35, peak - min(0.70, usable * 0.10))
    highlight_end = min(usable - 0.25, highlight_start + min(1.85, max(1.10, usable * 0.23)))
    if highlight_end <= highlight_start:
        highlight_end = min(usable, highlight_start + 0.9)

    freeze_start = min(usable - 0.18, highlight_end)
    replay_start = min(usable - 0.25, freeze_start + 0.42)
    ending_start = max(replay_start + 0.6, usable - min(1.15, usable * 0.16))

    moments = [
        Moment("opening", "Abertura", 0.08, min(0.95, usable * 0.15), "top", PHRASE_BANK["opening"]),
        Moment("approach", "Aproximação", min(0.85, usable * 0.16), max(1.45, highlight_start - 0.08), "top", PHRASE_BANK["approach"]),
        Moment("highlight", "Câmera lenta", highlight_start, highlight_end, "bottom", PHRASE_BANK["highlight"]),
        Moment("freeze", "Freeze", freeze_start, min(usable, freeze_start + 0.52), "middle", PHRASE_BANK["freeze"]),
        Moment("replay", "Replay", replay_start, min(usable, replay_start + 1.05), "bottom", PHRASE_BANK["replay"]),
        Moment("ending", "Final", ending_start, min(usable, ending_start + 1.0), "top", PHRASE_BANK["ending"]),
    ]

    payload = []
    for moment in moments:
        start = min(moment.start, max(0.0, usable - 0.12))
        end = max(start + 0.12, min(moment.end, usable))
        payload.append({
            "id": moment.id,
            "label": moment.label,
            "start": _round_time(start),
            "end": _round_time(end),
            "position": moment.position,
            "suggestions": moment.suggestions,
            "selected_text": moment.suggestions[0],
            "enabled": True,
        })

    return {
        "duration": _round_time(usable),
        "source_duration": _round_time(duration),
        "width": width,
        "height": height,
        "motion_peak": _round_time(peak),
        "style": {
            "font": "Lato Heavy",
            "max_lines": 2,
            "safe_width_percent": 84,
            "text_color": "#FFFFFF",
            "outline_color": "#000000",
            "outline_width": 6,
            "shadow": True,
        },
        "moments": payload,
    }
