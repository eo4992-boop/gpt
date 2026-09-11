from pathlib import Path

from video_duplicate_finder import VideoInfo, find_duplicate_groups, name_similarity


def info(name: str, size: int) -> VideoInfo:
    return VideoInfo(Path(name), size)


def test_numbered_copy_has_high_name_similarity() -> None:
    assert name_similarity(Path("holiday.mp4"), Path("holiday (1).mp4")) >= 90


def test_same_size_and_similar_name_are_grouped() -> None:
    groups = find_duplicate_groups([
        info("holiday.mp4", 1000),
        info("holiday (1).mp4", 1000),
        info("other.mp4", 2000),
    ])
    assert len(groups) == 1
    assert {item.path.name for item in groups[0]} == {"holiday.mp4", "holiday (1).mp4"}


def test_different_size_and_similar_name_are_still_candidates() -> None:
    groups = find_duplicate_groups([
        info("holiday.mp4", 1000),
        info("holiday (1).mp4", 2000),
    ])
    assert len(groups) == 1


def test_unrelated_name_is_not_grouped() -> None:
    groups = find_duplicate_groups([
        info("holiday.mp4", 1000),
        info("family-video.mp4", 2000),
    ])
    assert groups == []


def test_threshold_can_be_changed() -> None:
    groups = find_duplicate_groups([
        info("holiday.mp4", 1000),
        info("holiday-copy.mp4", 2000),
    ], 50)
    assert len(groups) == 1
