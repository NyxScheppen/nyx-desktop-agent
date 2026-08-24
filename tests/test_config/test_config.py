from pathlib import Path

import pytest

from nyx.config import (
    ActivityEnergyDelta,
    Config,
    ConfigError,
    load_config,
    validate_config,
)


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ---- validate_config：纯函数，逐字段校验 ----


def test_validate_config_accepts_defaults() -> None:
    validate_config(Config())


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("expression", "slow_threshold", 1.5),    # 越界 >1
        ("memory", "freshness_decay", -0.1),      # 越界 <0
        ("memory", "short_term_capacity", 0),     # 非正
        ("activity", "grid_minutes", -1),         # 非正
        ("memory", "short_term_capacity", "20"),  # 错类型 str
        ("exploration", "web_enabled", "yes"),    # 错类型 str 当 bool
        ("expression", "ask_timeout", -1.0),          # 非正
        ("expression", "chat_ignore_timeout", 0.0),   # 非正
        ("vision", "interval_seconds", 0),            # 非正
        ("vision", "interval_seconds", "60"),         # 错类型 str
        ("vision", "enabled", "yes"),                 # 错类型 str 当 bool
        ("vision", "provider", ""),                   # 空 str
        ("vision", "api_key_env", ""),                # 空 str
        ("llm", "timeout", -1.0),                     # 非正
        ("llm", "timeout", "60"),                     # 错类型 str
        ("llm", "max_retries", "2"),                  # 错类型 str
        ("llm", "max_retries", True),                 # 错类型 bool
    ],
)
def test_validate_config_rejects_invalid(
    section: str, field: str, value: object
) -> None:
    cfg = Config()
    setattr(getattr(cfg, section), field, value)
    with pytest.raises(ConfigError):
        validate_config(cfg)


# ---- load_config：缺键填默认 / 递归构造 / 未知键 / 错误路径 ----


def test_load_config_fills_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, "llm:\n  provider: openai\n")
    cfg = load_config(path)
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.activity.energy_delta.reading == -20


def test_load_config_builds_energy_delta_recursively(tmp_path: Path) -> None:
    path = _write(tmp_path, "activity:\n  energy_delta:\n    reading: -99\n")
    cfg = load_config(path)
    assert isinstance(cfg.activity.energy_delta, ActivityEnergyDelta)
    assert cfg.activity.energy_delta.reading == -99
    assert cfg.activity.energy_delta.rest == 30


def test_load_config_rejects_unknown_energy_delta_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "activity:\n  energy_delta:\n    bogus: 99\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_rejects_nested_non_dict(tmp_path: Path) -> None:
    path = _write(tmp_path, "memory: 100\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "bogus_section: {}\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_rejects_mixed_type_unknown_key(tmp_path: Path) -> None:
    # YAML 把 1: 解析成 int 键，与 str 键混合时 sorted 默认排序会 TypeError
    path = _write(tmp_path, "bogus: b\n1: a\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_rejects_unknown_section_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "llm:\n  bogus: 1\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "missing.yaml"))


def test_load_config_rejects_bad_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, "llm: [unclosed\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, "llm:\n  provider: from-env\n")
    monkeypatch.setenv("NYX_CONFIG", path)
    cfg = load_config()
    assert cfg.llm.provider == "from-env"


def test_load_config_empty_file_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, "")
    cfg = load_config(path)
    assert cfg.llm.provider == "deepseek"


@pytest.mark.parametrize("yaml_text", ["0\n", '""\n', "[]\n"])
def test_load_config_rejects_scalar_top_level(
    tmp_path: Path, yaml_text: str
) -> None:
    path = _write(tmp_path, yaml_text)
    with pytest.raises(ConfigError):
        load_config(path)
