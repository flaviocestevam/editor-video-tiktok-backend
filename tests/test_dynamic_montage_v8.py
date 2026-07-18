from app.services import dynamic_montage_v8 as v8


def test_replay_is_restricted_to_scene(monkeypatch):
    v8._CONTEXT.clear()
    v8._CONTEXT.update({"source_duration": 6.46, "scene_start": 0.0, "scene_end": 3.916})
    monkeypatch.setattr(v8, "_ORIGINAL_PLAN_BUILDER", lambda *args, **kwargs: [
        {"kind": "clip", "start": 0.0, "end": 6.46, "speed": 1.0}
    ])

    plan = v8._build_emphasized_plan(
        6.46,
        2.926,
        hard_cuts=True,
        speed_ramp=True,
        short_slowmo=True,
        short_speedup=True,
        freeze_frame=True,
        highlight_replay=True,
    )

    replay = next(item for item in plan if item.get("role") == "scene_limited_principal_impact_replay")
    assert 2.45 <= float(replay["start"]) <= 2.55
    assert 3.10 <= float(replay["end"]) <= 3.20
    assert float(replay["end"]) < 3.916
    assert float(replay["speed"]) == 0.50


def test_safe_defaults_do_not_flip_or_interpolate():
    source = open(v8.__file__, encoding="utf-8").read()
    assert 'safe_options.setdefault("flip_horizontal", False)' in source
    assert 'safe_options.setdefault("output_fps", "source")' in source
    assert '"temporal_interpolation": False' in source
    assert 'final_crf = max(14, quality_crf - 3)' in source


def test_text_crop_is_disabled_by_default():
    assert v8.process_dynamic_video.__kwdefaults__["remove_text_overlays"] is False
