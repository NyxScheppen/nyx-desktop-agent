import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import yaml

from nyx.policy import policy


class ConfigError(Exception):
    """配置加载或校验失败。"""


@dataclass
class ActivityEnergyDelta:  # activity.energy_delta：6 键 = ActivityType 值
    reading: int = policy("activity", "energy_delta.reading", int)
    creation: int = policy("activity", "energy_delta.creation", int)
    free_exploration: int = policy("activity", "energy_delta.free_exploration", int)
    observe_user: int = policy("activity", "energy_delta.observe_user", int)
    idle_reflection: int = policy("activity", "energy_delta.idle_reflection", int)
    rest: int = policy("activity", "energy_delta.rest", int)


@dataclass
class LlmConfig:
    provider: str = policy("llm", "defaults.provider", str)
    model: str = policy("llm", "defaults.model", str)
    api_key_env: str = policy(
        "llm", "defaults.api_key_env", str
    )  # 存环境变量名，key 本体由 LLM 客户端读
    base_url: str | None = policy(
        "llm", "defaults.base_url", str | None
    )  # 可选 endpoint 覆盖；缺省查 provider 映射
    timeout: float = policy("llm", "defaults.timeout", float)  # 单次 LLM 请求超时（秒）
    max_retries: int = policy("llm", "defaults.max_retries", int)  # 请求失败重试次数
    temperature: float = policy(
        "llm", "defaults.temperature", float
    )  # 采样温度 0-2，比默认 1.0 略收紧


@dataclass
class EmbeddingConfig:
    model: str = policy("memory", "embedding.model", str)


@dataclass
class MemoryConfig:
    short_term_capacity: int = policy("memory", "defaults.short_term_capacity", int)
    promote_threshold: int = policy("memory", "defaults.promote_threshold", int)
    freshness_decay: float = policy("memory", "defaults.freshness_decay", float)


@dataclass
class DesireConfig:
    peak_threshold: float = policy("desire", "defaults.peak_threshold", float)
    retry_limit: int = policy("desire", "defaults.retry_limit", int)
    short_term_capacity: int = policy("desire", "defaults.short_term_capacity", int)
    long_term_capacity: int = policy("desire", "defaults.long_term_capacity", int)
    value_decay: float = policy("desire", "defaults.value_decay", float)


@dataclass
class ActivityConfig:
    grid_minutes: int = policy("activity", "defaults.grid_minutes", int)
    energy_delta: ActivityEnergyDelta = field(default_factory=ActivityEnergyDelta)


@dataclass
class ExpressionConfig:
    autonomous_followups_enabled: bool = policy(
        "initiative", "defaults.autonomous_enabled", bool
    )
    slow_threshold: float = policy("expression", "defaults.slow_threshold", float)
    max_context_len: int = policy("expression", "defaults.max_context_len", int)
    slow_max_rounds: int = policy("expression", "defaults.slow_max_rounds", int)
    ask_timeout: float = policy(
        "expression", "defaults.ask_timeout", float
    )  # ask 后等用户回答超时（秒）
    chat_ignore_timeout: float = policy(
        "expression", "defaults.chat_ignore_timeout", float
    )  # 搭话被忽略判定超时（秒）


@dataclass
class ExplorationConfig:
    web_enabled: bool = policy("research", "defaults.web_enabled", bool)
    rate_limit_hours: int = policy("research", "defaults.rate_limit_hours", int)


@dataclass
class VisionConfig:
    enabled: bool = policy("vision", "defaults.enabled", bool)  # 屏幕视觉 opt-in
    provider: str = policy(
        "vision", "defaults.provider", str
    )  # 复用 provider→base_url 映射
    model: str = policy("vision", "defaults.model", str)  # 本地视觉模型 tag（Ollama）
    api_key_env: str = policy(
        "vision", "defaults.api_key_env", str
    )  # 存环境变量名，key 本体由 LLM 客户端读
    base_url: str | None = policy(
        "vision", "defaults.base_url", str | None
    )  # 可选 endpoint 覆盖；缺省查 provider 映射
    interval_seconds: int = policy(
        "vision", "defaults.interval_seconds", int
    )  # 抓屏周期（秒）


@dataclass
class OneBotConfig:
    enabled: bool = policy("onebot", "defaults.enabled", bool)
    ws_url: str = policy("onebot", "defaults.ws_url", str)
    access_token_env: str = policy("onebot", "defaults.access_token_env", str)
    self_id: int = policy("onebot", "defaults.self_id", int)
    owner_id: int = policy("onebot", "defaults.owner_id", int)
    character_id: str = policy("onebot", "defaults.character_id", str)


@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    desire: DesireConfig = field(default_factory=DesireConfig)
    activity: ActivityConfig = field(default_factory=ActivityConfig)
    expression: ExpressionConfig = field(default_factory=ExpressionConfig)
    exploration: ExplorationConfig = field(default_factory=ExplorationConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    onebot: OneBotConfig = field(default_factory=OneBotConfig)


def _build(dc: Any, raw: Any) -> Any:
    """按 dataclass 字段递归构造：未知键报错、缺键用默认值、嵌套 dataclass 字段递归。"""
    if not isinstance(raw, dict):
        raise ConfigError(
            policy("runtime", "config.templates._build_extra_text", str).format(
                p0=dc.__name__
            )
        )
    data = cast(dict[str, Any], raw)
    unknown = set(data) - {f.name for f in fields(dc)}
    if unknown:
        # key=str：YAML 键类型可混合（1:/true:/日期），默认排序会 TypeError
        raise ConfigError(
            policy("runtime", "config.templates._build_extra_text_2", str).format(
                p0=sorted(unknown, key=str), p1=dc.__name__
            )
        )
    kwargs: dict[str, Any] = {}
    for f in fields(dc):
        if f.name not in data:
            continue  # 缺键用 dataclass 默认值
        value = data[f.name]
        # f.type 是字段注解（无 from __future__ import annotations，故为实际类）
        if is_dataclass(f.type):
            if not isinstance(value, dict):
                raise ConfigError(
                    policy(
                        "runtime", "config.templates._build_extra_text_3", str
                    ).format(p0=dc.__name__, p1=f.name, p2=type(value).__name__)
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
        raise ConfigError(
            policy("runtime", "config.templates.load_config_extra_text", str).format(
                p0=resolved, p1=exc
            )
        ) from exc
    if raw is None:
        # 空文件/顶层 null → 空配置；falsy 标量（0/""/[]）仍交 _build 报「必须是映射」
        raw = {}
    # 2) 递归构造（未知键/嵌套 dataclass 由 _build 处理）
    cfg = _build(Config, raw)
    onebot_ws_url = os.environ.get("NYX_ONEBOT_WS_URL")
    if onebot_ws_url:
        cfg.onebot.ws_url = onebot_ws_url
    # 3) 范围校验
    validate_config(cfg)
    return cfg


def _nonempty(v: Any, path: str) -> None:
    if not isinstance(v, str) or not v:
        raise ConfigError(
            policy("runtime", "config.templates._nonempty_extra_text", str).format(
                p0=path, p1=v
            )
        )


def _pos_int(v: Any, path: str) -> None:
    if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
        raise ConfigError(
            policy("runtime", "config.templates._nonempty_extra_text", str).format(
                p0=path, p1=v
            )
        )


def _int(v: Any, path: str) -> None:
    if not isinstance(v, int) or isinstance(v, bool):
        raise ConfigError(
            policy("runtime", "config.templates._nonempty_extra_text", str).format(
                p0=path, p1=v
            )
        )


def _unit_interval(v: Any, path: str) -> None:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0.0 <= v <= 1.0):
        raise ConfigError(
            policy("runtime", "config.templates._nonempty_extra_text", str).format(
                p0=path, p1=v
            )
        )


def _temperature(v: Any, path: str) -> None:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0.0 <= v <= 2.0):
        raise ConfigError(
            policy("runtime", "config.templates._nonempty_extra_text", str).format(
                p0=path, p1=v
            )
        )


def _pos_num(v: Any, path: str) -> None:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not (v > 0.0):
        raise ConfigError(
            policy("runtime", "config.templates._nonempty_extra_text", str).format(
                p0=path, p1=v
            )
        )


def _flag(v: Any, path: str) -> None:
    if not isinstance(v, bool):
        raise ConfigError(
            policy("runtime", "config.templates._nonempty_extra_text", str).format(
                p0=path, p1=v
            )
        )


def validate_config(cfg: Config) -> None:
    # 非空 str
    _nonempty(cfg.llm.provider, "llm.provider")
    _nonempty(cfg.llm.model, "llm.model")
    _nonempty(cfg.llm.api_key_env, "llm.api_key_env")
    _nonempty(cfg.embedding.model, "embedding.model")
    _nonempty(cfg.vision.provider, "vision.provider")
    _nonempty(cfg.vision.model, "vision.model")
    _nonempty(cfg.vision.api_key_env, "vision.api_key_env")
    if cfg.llm.base_url is not None:
        _nonempty(cfg.llm.base_url, "llm.base_url")  # 非 None 必须非空，防 "" 静默回退
    if cfg.vision.base_url is not None:
        _nonempty(cfg.vision.base_url, "vision.base_url")

    # int > 0
    for path, v in (
        ("memory.short_term_capacity", cfg.memory.short_term_capacity),
        ("memory.promote_threshold", cfg.memory.promote_threshold),
        ("desire.retry_limit", cfg.desire.retry_limit),
        ("desire.short_term_capacity", cfg.desire.short_term_capacity),
        ("desire.long_term_capacity", cfg.desire.long_term_capacity),
        ("activity.grid_minutes", cfg.activity.grid_minutes),
        ("expression.max_context_len", cfg.expression.max_context_len),
        ("expression.slow_max_rounds", cfg.expression.slow_max_rounds),
        ("exploration.rate_limit_hours", cfg.exploration.rate_limit_hours),
        ("vision.interval_seconds", cfg.vision.interval_seconds),
    ):
        _pos_int(v, path)

    # 数 ∈ [0, 1]
    for path, v in (
        ("memory.freshness_decay", cfg.memory.freshness_decay),
        ("desire.peak_threshold", cfg.desire.peak_threshold),
        ("expression.slow_threshold", cfg.expression.slow_threshold),
    ):
        _unit_interval(v, path)

    _pos_num(cfg.desire.value_decay, "desire.value_decay")  # 数 > 0
    _pos_num(cfg.llm.timeout, "llm.timeout")
    _int(cfg.llm.max_retries, "llm.max_retries")
    _temperature(cfg.llm.temperature, "llm.temperature")  # 数 ∈ [0, 2]
    _pos_num(cfg.expression.ask_timeout, "expression.ask_timeout")
    _pos_num(cfg.expression.chat_ignore_timeout, "expression.chat_ignore_timeout")
    _flag(
        cfg.expression.autonomous_followups_enabled,
        "expression.autonomous_followups_enabled",
    )
    _flag(cfg.exploration.web_enabled, "exploration.web_enabled")  # bool
    _flag(cfg.vision.enabled, "vision.enabled")
    _flag(cfg.onebot.enabled, "onebot.enabled")
    if cfg.onebot.enabled:
        _pos_int(cfg.onebot.self_id, "onebot.self_id")
        _pos_int(cfg.onebot.owner_id, "onebot.owner_id")
        if cfg.onebot.self_id == cfg.onebot.owner_id:
            raise ConfigError(
                policy("runtime", "config.copy.validate_config_extra_text", str)
            )
        _nonempty(cfg.onebot.character_id, "onebot.character_id")
        _nonempty(cfg.onebot.access_token_env, "onebot.access_token_env")
        _nonempty(cfg.onebot.ws_url, "onebot.ws_url")
        try:
            url = urlsplit(cfg.onebot.ws_url)
            valid = (
                url.scheme in ("ws", "wss")
                and (
                    url.hostname in ("127.0.0.1", "localhost", "::1")
                    or (
                        url.hostname is not None
                        and url.hostname.endswith(".railway.internal")
                    )
                )
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
                and (url.port is None or url.port > 0)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ConfigError(
                policy("runtime", "config.copy.validate_config_extra_text_2", str)
            )

    # energy_delta 6 键全为 int（可为负）
    for name, v in vars(cfg.activity.energy_delta).items():
        _int(v, f"activity.energy_delta.{name}")
