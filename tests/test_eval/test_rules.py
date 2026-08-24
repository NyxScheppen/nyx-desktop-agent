from nyx.eval.rules import ooc_score


def test_ooc_score() -> None:
    assert ooc_score("普通输出") == 1.0              # 无命中默认满分
    assert ooc_score("无法回答") == 0.5             # 黑 1 白 0
    assert ooc_score("无法回答让我来帮你") == 0.0     # 黑 2 白 0
    assert ooc_score("无法回答让我来帮你your request") == 0.0  # 黑 3 白 0 封顶
    assert ooc_score("无法回答小狐狸") == 1.0        # 黑 1 白 1 抵消
    assert ooc_score("小狐狸夏本") == 1.0           # 白 2 黑 0 封顶不越界
