from __future__ import annotations

import inspect

from app.services import dynamic_montage_v7 as v7


def test_principal_impact_prefers_peak_followed_by_drop(monkeypatch):
    samples = [
        (4.000, 4.421),
        (4.083, 3.643),
        (4.167, 5.502),
        (4.250, 4.717),
        (4.333, 5.417),
        (4.417, 3.199),
        (5.250, 2.213),
        (5.333, 2.848),
        (5.417, 5.605),
        (5.500, 3.850),
        (5.583, 4.133),
        (5.667, 2.631),
    ]
    monkeypatch.setattr(v7, "_motion_samples", lambda *_args: samples)
    impact, report = v7._detect_principal_impact("sample.mp4", 7.103)
    assert impact == 5.417
    assert report["method"] == "motion_peak_followed_by_drop"
    assert report["drop_after_contact"] > 1


def test_replay_is_slow_and_inserted_after_principal_impact():
    peak = 5.417
    plan = v7._build_emphasized_plan(
        7.103,
        peak,
        hard_cuts=True,
        speed_ramp=True,
        short_slowmo=True,
        short_speedup=True,
        freeze_frame=True,
        highlight_replay=True,
    )
    replay_index = next(i for i, item in enumerate(plan) if item.get("role") == "principal_impact_replay")
    replay = plan[replay_index]
    assert replay["speed"] == 0.42
    assert float(replay["start"]) < peak < float(replay["end"])
    assert replay_index > 0
    assert plan[replay_index + 1].get("role") == "principal_impact_hold"


def test_final_pass_uses_interpolation_and_real_end_fade():
    source = inspect.getsource(v7._run_final_pass)
    assert "framerate=fps=30000/1001" in source
    assert "duration - fade_out" in source
    assert "crop=iw:trunc(ih*0.87/2)*2" in source
