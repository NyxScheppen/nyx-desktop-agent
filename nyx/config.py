import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, cast

import yaml


class ConfigError(Exception):
    """配置加载或校验失败。"""


@dataclass
class ActivityEnergyDelta:              # activity.energy_delta：6 键 = ActivityType 值
    reading: int = -20
    creation: int = -25
    free_exploration: int = -30
    observe_user: int = -10
    idle_reflection: int = 10
    rest: int = 30


@dataclass
class LlmConfig:
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key_env: str = "DEEPSEEK_API_KEY"  # 存环境变量名，key 本体由 03-llm 读
    base_url: str | None = None            # 可选 endpoint 覆盖；缺省查 provider 映射


@dataclass
class EmbeddingConfig:
    model: str = "all-MiniLM-L6-v2"


@dataclass
class MemoryConfig:
    short_term_capacity: int = 100
    promote_threshold: int = 3
    freshness_decay: float = 0.01


@dataclass
class DesireConfig:
    peak_threshold: float = 0.8
    retry_limit: int = 3
    long_term_capacity: int = 5
    value_decay: float = 0.02


@dataclass
class ActivityConfig:
    grid_minutes: int = 60
    energy_delta: ActivityEnergyDelta = field(default_factory=ActivityEnergyDelta)


@dataclass
class ExpressionConfig:
    slow_threshold: float = 0.5
    max_context_len: int = 20
    slow_max_rounds: int = 3
    ask_timeout: float = 600.0           # ask 后等用户回答超时（秒）
    chat_ignore_timeout: float = 1800.0  # 搭话被忽略判定超时（秒）
    context_time_gap: float = 3600.0     # 回溯上下文相邻消息隔超此值即停（秒）


@dataclass
class ExplorationConfig:
    web_enabled: bool = False
    rate_limit_hours: int = 4


@dataclass
class EvalConfig:
    judge_sample_rate: float = 0.1


@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    desire: DesireConfig = field(default_factory=DesireConfig)
    activity: ActivityConfig = field(default_factory=ActivityConfig)
    expression: ExpressionConfig = field(default_factory=ExpressionConfig)
    exploration: ExplorationConfig = field(default_factory=ExplorationConfig)
    eval: EvalConfig = field(default_factory=EvalConfig)


def _build(dc: Any, raw: Any) -> Any:
    """按 dataclass 字段递归构造：未知键报错、缺键用默认值、嵌套 dataclass 字段递归。"""
    if not isinstance(raw, dict):
        raise ConfigError(f"{dc.__name__} 必须是映射")
    data = cast(dict[str, Any], raw)
    unknown = set(data) - {f.name for f in fields(dc)}
    if unknown:
        # key=str：YAML 键类型可混合（1:/true:/日期），默认排序会 TypeError
        raise ConfigError(f"未知配置键 {sorted(unknown, key=str)} in {dc.__name__}")
    kwargs: dict[str, Any] = {}
    for f in fields(dc):
        if f.name not in data:
            continue  # 缺键用 dataclass 默认值
        value = data[f.name]
        # f.type 是字段注解（无 from __future__ import annotations，故为实际类）
        if is_dataclass(f.type):
            if not isinstance(value, dict):
                raise ConfigError(
                    f"{dc.__name__}.{f.name} 必须是映射，得到 {type(value).__name__}"
                )
            value = _build(f.type, value)  # 嵌套 dataclass 递归构造
        kwargs[f.name] = value
    return dc(**kwargs)


def load_config(path: str | None = None) -> Config:
    # 1) 解析路径：显式 path > NYX_CONFIG 环境变量 > 默认 "config.yaml"
    resolved = path or os.environ.get("NYX_CONFIG") or "config.yaml"
    try:
        raw: Any = yaml.safe_load(Path(resolved).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ConfigError(f"配置加载失败 {resolved}: {exc}") from exc
    if raw is None:
        # 空文件/顶层 null → 空配置；falsy 标量（0/""/[]）仍交 _build 报「必须是映射」
        raw = {}
    # 2) 递归构造（未知键/嵌套 dataclass 由 _build 处理）
    cfg = _build(Config, raw)
    # 3) 范围校验
    validate_config(cfg)
    return cfg


def _nonempty(v: Any, path: str) -> None:
    if not isinstance(v, str) or not v:
        raise ConfigError(f"{path} 非法: {v!r}")


def _pos_int(v: Any, path: str) -> None:
    if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
        raise ConfigError(f"{path} 非法: {v!r}")


def _int(v: Any, path: str) -> None:
    if not isinstance(v, int) or isinstance(v, bool):
        raise ConfigError(f"{path} 非法: {v!r}")


def _unit_interval(v: Any, path: str) -> None:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0.0 <= v <= 1.0):
        raise ConfigError(f"{path} 非法: {v!r}")


def _pos_num(v: Any, path: str) -> None:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not (v > 0.0):
        raise ConfigError(f"{path} 非法: {v!r}")


def _flag(v: Any, path: str) -> None:
    if not isinstance(v, bool):
        raise ConfigError(f"{path} 非法: {v!r}")


def validate_config(cfg: Config) -> None:
    # 非空 str
    _nonempty(cfg.llm.provider, "llm.provider")
    _nonempty(cfg.llm.model, "llm.model")
    _nonempty(cfg.llm.api_key_env, "llm.api_key_env")
    _nonempty(cfg.embedding.model, "embedding.model")
    if cfg.llm.base_url is not None:
        _nonempty(cfg.llm.base_url, "llm.base_url")  # 非 None 必须非空，防 "" 静默回退

    # int > 0
    for path, v in (("memory.short_term_capacity", cfg.memory.short_term_capacity),
                    ("memory.promote_threshold", cfg.memory.promote_threshold),
                    ("desire.retry_limit", cfg.desire.retry_limit),
                    ("desire.long_term_capacity", cfg.desire.long_term_capacity),
                    ("activity.grid_minutes", cfg.activity.grid_minutes),
                    ("expression.max_context_len", cfg.expression.max_context_len),
                    ("expression.slow_max_rounds", cfg.expression.slow_max_rounds),
                    ("exploration.rate_limit_hours", cfg.exploration.rate_limit_hours)):
        _pos_int(v, path)

    # 数 ∈ [0, 1]
    for path, v in (("memory.freshness_decay", cfg.memory.freshness_decay),
                    ("desire.peak_threshold", cfg.desire.peak_threshold),
                    ("expression.slow_threshold", cfg.expression.slow_threshold),
                    ("eval.judge_sample_rate", cfg.eval.judge_sample_rate)):
        _unit_interval(v, path)

    _pos_num(cfg.desire.value_decay, "desire.value_decay")          # 数 > 0
    _pos_num(cfg.expression.ask_timeout, "expression.ask_timeout")
    _pos_num(cfg.expression.chat_ignore_timeout, "expression.chat_ignore_timeout")
    _pos_num(cfg.expression.context_time_gap, "expression.context_time_gap")
    _flag(cfg.exploration.web_enabled, "exploration.web_enabled")   # bool

    # energy_delta 6 键全为 int（可为负）
    for name, v in vars(cfg.activity.energy_delta).items():
        _int(v, f"activity.energy_delta.{name}")
