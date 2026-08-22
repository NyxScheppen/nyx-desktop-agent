from nyx.activity.observe import build_observation_summary, classify_presence


def test_classify_presence_online() -> None:
    assert classify_presence(True, False, "") == "online"
    assert classify_presence(False, True, "") == "online"


def test_classify_presence_busy() -> None:
    assert classify_presence(False, False, "编辑器") == "busy"


def test_classify_presence_away() -> None:
    assert classify_presence(False, False, "") == "away"


def test_build_observation_summary_window_title() -> None:
    assert (
        build_observation_summary("online", "编辑器", "")
        == "用户（online）正在浏览 编辑器"
    )


def test_build_observation_summary_no_window() -> None:
    assert build_observation_summary("away", "", "") == "用户（away）"


def test_build_observation_summary_with_screen() -> None:
    assert (
        build_observation_summary("online", "编辑器", "写代码")
        == "用户（online）正在浏览 编辑器，屏幕：写代码"
    )


def test_build_observation_summary_screen_only() -> None:
    assert (
        build_observation_summary("busy", "", "看视频")
        == "用户（busy），屏幕：看视频"
    )
