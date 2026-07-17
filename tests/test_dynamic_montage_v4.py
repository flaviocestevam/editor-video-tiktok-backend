from app.services import dynamic_montage
from app.services import dynamic_montage_v3


def test_rotation_metadata_is_normalised():
    assert dynamic_montage_v3._normalise_rotation(90) == 90
    assert dynamic_montage_v3._normalise_rotation(-90) == 270
    assert dynamic_montage_v3._normalise_rotation(181) == 180
    assert dynamic_montage_v3._normalise_rotation(None) == 0


def test_display_dimensions_swap_rotated_video_and_are_even():
    assert dynamic_montage_v3._display_dimensions(1081, 1921, 90) == (1920, 1080)
    assert dynamic_montage_v3._display_dimensions(721, 1281, 0) == (720, 1280)


def test_4k_video_is_bounded_for_railway_memory():
    assert dynamic_montage._bounded_dimensions(2160, 3840) == (1080, 1920)
    assert dynamic_montage._bounded_dimensions(3840, 2160) == (1920, 1080)


def test_small_video_keeps_original_dimensions():
    assert dynamic_montage._bounded_dimensions(720, 1280) == (720, 1280)
