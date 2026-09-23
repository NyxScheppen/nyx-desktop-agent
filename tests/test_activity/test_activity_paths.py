from nyx.activity.paths import (
    creation_output_path,
    path_hash_suffix,
    sanitize_filename,
)


def test_sanitize_filename_removes_unsafe_chars() -> None:
    assert sanitize_filename("小狐狸的日记") == "小狐狸的日记"
    assert sanitize_filename("a/b:c") == "abc"


def test_sanitize_filename_empty_falls_back() -> None:
    assert sanitize_filename("") == "untitled"
    assert sanitize_filename("///") == "untitled"


def test_sanitize_filename_caps_component_length() -> None:
    assert len(sanitize_filename("x" * 300)) <= 96


def test_creation_output_path_is_unique_per_activity() -> None:
    first = creation_output_path("同名作品", "activity-1")
    second = creation_output_path("同名作品", "activity-2")
    assert first != second
    assert first.startswith("creations/同名作品-")
    assert first.endswith(".md")


def test_path_hash_suffix_is_stable_short_hash() -> None:
    assert path_hash_suffix("workspace/book.txt") == path_hash_suffix(
        "workspace/book.txt"
    )
    assert len(path_hash_suffix("workspace/book.txt")) == 8
