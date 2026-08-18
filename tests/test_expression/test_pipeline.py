# pyright: reportPrivateUsage=false
from collections import deque

from nyx.expression.pipeline import _backtrack, _is_question, _rounds_block
from nyx.types import Message


def test_is_question() -> None:
    assert _is_question("你今天好吗？") is True
    assert _is_question("你今天怎么样") is True
    assert _is_question("我很好。") is False


def test_backtrack_short() -> None:
    history = deque(
        [
            Message(role="user", content="a", timestamp=1.0),
            Message(role="nyx", content="b", timestamp=2.0),
        ]
    )
    assert _backtrack(history, 5) == list(history)


def test_backtrack_long() -> None:
    history: deque[Message] = deque(
        Message(role="user", content=f"m{i}", timestamp=float(i))
        for i in range(5)
    )
    got = _backtrack(history, 2)
    assert [m.content for m in got] == ["m3", "m4"]


def test_backtrack_empty() -> None:
    assert _backtrack(deque[Message](), 5) == []


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
