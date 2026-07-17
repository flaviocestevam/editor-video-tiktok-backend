from app.services import dynamic_montage


def test_dynamic_reframe_changes_each_segment():
    base = {"kind": "clip", "start": 0.0, "end": 1.0, "speed": 1.0}
    first = dynamic_montage._reframed_item(base, 0, True)
    second = dynamic_montage._reframed_item(base, 1, True)
    assert first["reframe_zoom"] != second["reframe_zoom"]
    assert first["shift_x"] != second["shift_x"]


def test_scene_color_changes_between_segments():
    assert dynamic_montage._scene_color_filter(0) != dynamic_montage._scene_color_filter(1)


def test_filter_graph_contains_discreet_visual_layers():
    graph, duration = dynamic_montage._build_originality_filter_complex(
        trim=0.0,
        source_duration=4.0,
        output_duration=4.0,
        width=720,
        height=1280,
        spatial_filters=["format=yuv420p"],
        peak_source_time=2.0,
        hard_cuts=True,
        speed_ramp=True,
        short_slowmo=True,
        short_speedup=True,
        freeze_frame=True,
        highlight_replay=True,
        output_fps="29.97",
        fade=True,
        dynamic_reframe=True,
        animated_grain_overlay=True,
        scene_color_variation=True,
        light_texture_overlay=True,
    )
    assert duration > 0
    assert "noise=alls=1:allf=t+u" in graph
    assert "vignette=angle=" in graph
    assert "eq=brightness=" in graph
    assert "reframe" not in graph  # efeito é compilado em scale/crop, não em marcador textual
    assert "crop=720:1280" in graph
