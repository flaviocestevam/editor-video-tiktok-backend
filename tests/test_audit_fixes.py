from app.services import dynamic_montage_v5 as montage


def test_fade_is_moved_after_visual_layers(monkeypatch):
    def fake_builder(*args, **kwargs):
        assert kwargs["fade"] is False
        return "[montage]noise=alls=1,format=yuv420p[vout]", 5.0

    monkeypatch.setattr(montage, "_BASE_BUILDER", fake_builder)
    graph, duration = montage._fade_last_builder(fade=True)
    assert duration == 5.0
    assert graph.index("noise=alls=1") < graph.index("fade=t=in")
    assert graph.index("fade=t=out") < graph.index("format=yuv420p")


def test_metadata_report_requires_all_fields(monkeypatch):
    monkeypatch.setattr(
        montage,
        "_probe",
        lambda _: {
            "format": {
                "tags": {
                    "creation_time": "2026-07-18T00:00:00Z",
                    "com.apple.quicktime.make": "Apple",
                    "com.apple.quicktime.model": "iPhone 15 Pro Max",
                    "com.apple.quicktime.software": "iOS",
                    "com.apple.quicktime.location.ISO6709": "-22.9068-043.1729+002.0/",
                    "com.apple.quicktime.location.name": "Rio de Janeiro, Brasil",
                }
            }
        },
    )
    report = montage._metadata_report("video.mp4")
    assert report["written"] is True
    assert all(report["fields"].values())


def test_metadata_args_enable_quicktime_tags():
    args = montage._metadata_args()
    joined = " ".join(args)
    assert "com.apple.quicktime.make=Apple" in joined
    assert "com.apple.quicktime.model=iPhone 15 Pro Max" in joined
    assert "com.apple.quicktime.location.ISO6709" in joined
