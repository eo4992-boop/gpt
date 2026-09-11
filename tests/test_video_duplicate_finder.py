from pathlib import Path

from video_duplicate_finder import VideoInfo, find_duplicate_groups, name_similarity


def info(name: str, size: int, duration: float = 100.0) -> VideoInfo:
    return VideoInfo(Path(name), size, duration, 1920, 1080, "h264")


def test_name_similarity_is_high_for_numbered_copy() -> None:
    assert name_similarity(Path("holiday.mp4"), Path("holiday (1).mp4")) >= 85


def test_same_size_and_similar_name_are_grouped() -> None:
    groups = find_duplicate_groups([
        info("holiday.mp4", 1000),
        info("holiday (1).mp4", 1000),
        info("other.mp4", 2000),
    ])
    assert len(groups) == 1
    assert {item.path.name for item in groups[0]} == {"holiday.mp4", "holiday (1).mp4"}


def test_same_size_unrelated_name_is_not_grouped_without_matching_content() -> None:
    groups = find_duplicate_groups([
        info("holiday.mp4", 1000),
        info("family-video.mp4", 1000),
    ])
    assert groups == []


def test_different_size_with_unrelated_name_is_not_grouped() -> None:
    groups = find_duplicate_groups([
        info("holiday.mp4", 1000),
        info("family-video.mp4", 2000),
    ])
    assert groups == []


def test_different_size_highly_similar_name_and_metadata_are_grouped() -> None:
    groups = find_duplicate_groups([
        info("holiday.mp4", 1000, 100.0),
        info("holiday_final.mp4", 2000, 100.5),
    ])
    assert len(groups) == 1
    assert {item.path.name for item in groups[0]} == {"holiday.mp4", "holiday_final.mp4"}


def test_different_size_85_percent_name_threshold_is_used_by_default() -> None:
    groups = find_duplicate_groups([
        info("holiday.mp4", 1000, 100.0),
        info("holiday (1).mp4", 2000, 100.5),
    ])
    assert len(groups) == 1
