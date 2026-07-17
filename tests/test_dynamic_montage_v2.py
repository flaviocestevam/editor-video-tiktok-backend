from app.services import dynamic_montage


def test_slow_motion_is_visible_and_uses_zoom_out():
    duration = 7.2105646
    peak = 2.6102483
    plan = dynamic_montage._build_plan(
        duration,
        peak,
        hard_cuts=True,
        speed_ramp=True,
        short_slowmo=True,
        short_speedup=True,
        freeze_frame=True,
        highlight_replay=True,
    )

    slowmo = next(item for item in plan if float(item.get("zoom_out", 0.0)) > 0)
    source_length = float(slowmo["end"]) - float(slowmo["start"])
    output_length = source_length / float(slowmo["speed"])

    assert 1.0 <= output_length <= 2.0
    assert float(slowmo["speed"]) < 0.7


def test_jump_cuts_remove_visible_gaps_and_reframe_segments():
    plan = []
    dynamic_montage._append_jump_cuts(plan, 0.0, 4.0, True)

    assert len(plan) >= 3
    gaps = [
        float(plan[index + 1]["start"]) - float(plan[index]["end"])
        for index in range(len(plan) - 1)
    ]
    assert all(0.12 <= gap <= 0.25 for gap in gaps)
    assert len({float(item.get("reframe_zoom", 1.0)) for item in plan}) > 1
