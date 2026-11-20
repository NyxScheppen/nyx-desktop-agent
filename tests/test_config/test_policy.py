"""Policy failures and caller isolation at the actual loading boundary."""

# pyright: reportPrivateUsage=false

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

import nyx.policy as policy_module
from nyx.activity.observe import classify_presence
from nyx.policy import PolicyError, policy, validate_policy_catalog


@pytest.fixture
def catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(policy_module, "__file__", str(tmp_path / "policy.py"))
    policy_module._document.cache_clear()
    directory = tmp_path / "settings"
    directory.mkdir()
    yield directory
    policy_module._document.cache_clear()


def test_missing_policy_never_uses_a_code_default(catalog: Path) -> None:
    with pytest.raises(PolicyError, match="策略加载失败"):
        policy("activity", "required", int)
    (catalog / "activity.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PolicyError, match="缺少策略"):
        policy("activity", "required", int)


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_invalid_json_policy_is_rejected(catalog: Path, raw: str) -> None:
    (catalog / "activity.json").write_text(raw, encoding="utf-8")
    with pytest.raises(PolicyError):
        validate_policy_catalog()


def test_references_fail_on_missing_targets_and_cycles(catalog: Path) -> None:
    (catalog / "activity.json").write_text(
        json.dumps({"a": {"$ref": "activity:b"}, "b": {"$ref": "activity:a"}}),
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="引用循环"):
        validate_policy_catalog()
    policy_module._document.cache_clear()
    (catalog / "activity.json").write_text(
        '{"a":{"$ref":"activity:missing"}}',
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="缺少策略"):
        validate_policy_catalog()


def test_one_caller_cannot_mutate_another_callers_policy(catalog: Path) -> None:
    (catalog / "activity.json").write_text(
        '{"weights":{"reading":[0.2,0.8]}}',
        encoding="utf-8",
    )
    first = policy("activity", "weights", dict[str, list[float]])
    first["reading"].clear()
    assert policy("activity", "weights", dict[str, list[float]]) == {
        "reading": [0.2, 0.8],
    }
    with pytest.raises(PolicyError, match="类型错误"):
        policy("activity", "weights", dict[str, int])


def test_observation_follows_the_policy_instead_of_old_thresholds(
    catalog: Path,
) -> None:
    (catalog / "activity.json").write_text(
        json.dumps(
            {
                "observe": {
                    "parameters": {"online_idle_seconds": 10, "busy_idle_seconds": 20}
                },
            }
        ),
        encoding="utf-8",
    )
    assert classify_presence(9) == "online"
    assert classify_presence(10) == "busy"
    assert classify_presence(20) == "away"
