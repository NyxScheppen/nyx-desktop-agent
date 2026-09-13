from nyx.activity.paths import path_hash_suffix, sanitize_filename


def test_sanitize_filename_removes_unsafe_chars() -> None:
    assert sanitize_filename("小狐狸的日记") == "小狐狸的日记"
    assert sanitize_filename("a/b:c") == "abc"


def test_sanitize_filename_empty_falls_back() -> None:
    assert sanitize_filename("") == "untitled"
    assert sanitize_filename("///") == "untitled"


def test_path_hash_suffix_is_stable_short_hash() -> None:
    assert path_hash_suffix("workspace/book.txt") == path_hash_suffix(
        "workspace/book.txt"
    )
    assert len(path_hash_suffix("workspace/book.txt")) == 8
