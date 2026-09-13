from nyx.activity.reading_runner import ReadingActivityRunner


def test_reading_runner_has_single_activity_entrypoint() -> None:
    """读书执行细节通过 runner 的单一入口暴露给 facade。"""
    assert hasattr(ReadingActivityRunner, "run")
