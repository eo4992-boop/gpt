from pathlib import Path

from video_duplicate_finder import (
    VideoInfo,
    candidate_reason,
    find_duplicate_groups,
    name_similarity,
    normalized_name,
)


def info(name: str, size: int) -> VideoInfo:
    return VideoInfo(Path(name), size)


def test_numbered_copy_has_high_name_similarity() -> None:
    assert name_similarity(Path("holiday.mp4"), Path("holiday (1).mp4")) >= 90
    assert normalized_name(Path("holiday (123).mp4")) == "holiday"


def test_same_size_and_similar_name_are_strong_candidates() -> None:
    reason = candidate_reason(info("holiday.mp4", 1000), info("holiday (1).mp4", 1000), 90)
    assert reason is not None
    assert "강한 후보" in reason
    assert "크기 동일" in reason


def test_different_size_and_similar_name_are_warning_candidates() -> None:
    reason = candidate_reason(info("holiday.mp4", 1000), info("holiday (1).mp4", 2000), 90)
    assert reason is not None
    assert "주의 후보" in reason
    assert "크기 다름" in reason


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


def test_grouping_uses_connected_components() -> None:
    # B bridges A and C. A-C itself is intentionally below the threshold.
    groups = find_duplicate_groups([
        info("movie 2026.mp4", 1000),
        info("movie 2026 final.mp4", 900),
        info("movie final 2026 remastered.mp4", 800),
    ], 80)
    assert len(groups) == 1
    assert {item.path.name for item in groups[0]} == {
        "movie 2026.mp4",
        "movie 2026 final.mp4",
        "movie final 2026 remastered.mp4",
    }


def test_group_recommends_largest_file_first_without_marking_it_for_deletion() -> None:
    groups = find_duplicate_groups([
        info("copy.mp4", 2000),
        info("original.mp4", 1000),
    ], 50)
    assert groups[0][0].size == 2000
    # The model layer has no deletion side effect; the GUI deletion checkbox is opt-in.


def test_same_name_different_sizes_are_not_called_exact_duplicates() -> None:
    reason = candidate_reason(info("lecture.mp4", 1000), info("lecture (1).mp4", 3000), 90)
    assert reason is not None
    assert "주의 후보" in reason
    assert "크기 다름" in reason
