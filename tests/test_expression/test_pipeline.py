# pyright: reportPrivateUsage=false
from nyx.expression.pipeline import _is_question, _rounds_block


def test_is_question() -> None:
    assert _is_question("你今天好吗？") is True
    assert _is_question("你今天怎么样") is True
    assert _is_question("我很好。") is False


def test_rounds_block_empty() -> None:
    assert _rounds_block([], []) == ""


def test_rounds_block_single() -> None:
    out = _rounds_block(["t1"], ["s1"])
    assert "第1轮内心：t1" in out
    assert "第1轮对外：s1" in out


def test_rounds_block_two() -> None:
    out = _rounds_block(["t1", "t2"], ["s1", "s2"])
    assert out.index("第1轮内心：t1") < out.index("第1轮对外：s1")
    assert out.index("第1轮对外：s1") < out.index("第2轮内心：t2")
    assert out.index("第2轮内心：t2") < out.index("第2轮对外：s2")
