from nyx.activity.observe import classify_presence


def test_classify_presence_online() -> None:
    assert classify_presence(True, False, "") == "online"
    assert classify_presence(False, True, "") == "online"


def test_classify_presence_busy() -> None:
    assert classify_presence(False, False, "编辑器") == "busy"


def test_classify_presence_away() -> None:
    assert classify_presence(False, False, "") == "away"
