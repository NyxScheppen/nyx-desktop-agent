# 配置加载

> 范围：`nyx/config.py`（`Config` + 7 个分段 dataclass + `load_config()` + `validate_config()` + `ConfigError`）+ `config.yaml`。
> 纯配置 spec：只做加载与校验，不含 Facade、不含 DDL、不含 API。
> **本文件自包含**：`config.yaml` 与 `config.py` 的完整定义都内联在下文，实现不依赖任何其它文档。

## 元信息

- **前置依赖**：无（配置项内联在本文件）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一份启动时同步加载、带默认值、类型安全、错配即报的配置对象，以便各 Facade 用 `config.llm.model` 这种点号访问读参数。

## 验收标准

- [ ] `config.py` 含 `Config` + 7 分段 dataclass + `ActivityEnergyDelta`，字段与「`nyx/config.py`（完整）」段代码逐字一致
- [ ] `load_config()` 同步返回 `Config`；缺键填默认值、未知键（含嵌套 `energy_delta` 内部）报 `ConfigError`
- [ ] `config.activity.energy_delta` 是 `ActivityEnergyDelta` 实例（不是裸 dict），`config.activity.energy_delta.reading` 点号访问成立
- [ ] `validate_config()` 是纯函数，逐字段校验，非法报 `ConfigError`
- [ ] `pyright` strict 下零报错
- [ ] 秘密不进 yaml：`llm.api_key_env` 只存环境变量名，key 本体由 03-llm 构造时读 `os.environ`

## 技术方案

- **新文件**：`nyx/config.py`、`config.yaml`（无 Facade、无 API、无数据变更）
- **库**：PyYAML（`yaml.safe_load`）
- **公开面**：`from nyx.config import Config, load_config, validate_config`（不加 `__all__`）
- **同步加载**（启动时一次性，event loop 未起，非运行期 I/O）
- **递归构造**：`_build` 看到字段类型是 dataclass 就递归构造，所以 `energy_delta` 会变成 `ActivityEnergyDelta`
- **类型标注**：`_build` 用 `Any`（`dc: Any, raw: Any -> Any`）而非泛型 `_T`——`dataclasses.Field.type` 与 `yaml.safe_load` 都返回 `Any`，pyright strict 下 `type[_T]` 不满足 `DataclassInstance` 协议、返回类型无法静态验证。用 `Any` + `cast(dict[str, Any], raw)` 诚实承认反射构造是动态的，不假装类型精确。
- **缺文件即报错**：`config.yaml` 缺失 → `ConfigError`（错误可溯源；"用全默认值"的场景由「缺键」覆盖，不靠「缺文件」）

### config.yaml（完整）

```yaml
llm:
  provider: deepseek          # deepseek | openai | ollama；其它 OpenAI 兼容服务配 base_url
  model: deepseek-chat
  api_key_env: DEEPSEEK_API_KEY
  # base_url: http://localhost:11434/v1   # 可选：覆盖/自定义 endpoint

embedding:
  model: all-MiniLM-L6-v2     # 本地 sentence-transformers

memory:
  short_term_capacity: 100    # 容量上限
  promote_threshold: 3        # 想起 3 次升级长期
  freshness_decay: 0.01       # 新鲜度衰减率

desire:
  peak_threshold: 0.8         # 值达峰阈值
  retry_limit: 3              # 未达成重试上限
  long_term_capacity: 5       # 长期欲望上限
  value_decay: 0.02           # 值缓慢衰减率

activity:
  grid_minutes: 60            # 每小时一块
  energy_delta:
    reading: -20
    creation: -25
    free_exploration: -30
    observe_user: -10
    idle_reflection: 10
    rest: 30

expression:
  slow_threshold: 0.5         # 快慢通道阈值：classifier 加权 5 因子→归一化得分(0-1)→比此值
  max_context_len: 20         # 回溯上下文上限
  slow_max_rounds: 3          # 慢通道最多轮数
  ask_timeout: 600.0          # ask 后等用户回答超时（秒）
  chat_ignore_timeout: 1800.0 # 搭话被忽略判定超时（秒）

exploration:
  web_enabled: false          # 联网搜索 opt-in
  rate_limit_hours: 1         # 自由探索频率上限
```

### nyx/config.py（完整）

```python
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
    timeout: float = 60.0                  # 单次 LLM 请求超时（秒）
    max_retries: int = 2                   # 请求失败重试次数
    temperature: float = 0.8               # 采样温度 0-2，比默认 1.0 略收紧


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


@dataclass
class ExplorationConfig:
    web_enabled: bool = False
    rate_limit_hours: int = 1


@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    desire: DesireConfig = field(default_factory=DesireConfig)
    activity: ActivityConfig = field(default_factory=ActivityConfig)
    expression: ExpressionConfig = field(default_factory=ExpressionConfig)
    exploration: ExplorationConfig = field(default_factory=ExplorationConfig)


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


def _temperature(v: Any, path: str) -> None:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0.0 <= v <= 2.0):
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
                    ("expression.slow_threshold", cfg.expression.slow_threshold)):
        _unit_interval(v, path)

    _pos_num(cfg.desire.value_decay, "desire.value_decay")          # 数 > 0
    _pos_num(cfg.llm.timeout, "llm.timeout")
    _int(cfg.llm.max_retries, "llm.max_retries")
    _temperature(cfg.llm.temperature, "llm.temperature")            # 数 ∈ [0, 2]
    _pos_num(cfg.expression.ask_timeout, "expression.ask_timeout")
    _pos_num(cfg.expression.chat_ignore_timeout, "expression.chat_ignore_timeout")
    _flag(cfg.exploration.web_enabled, "exploration.web_enabled")   # bool

    # energy_delta 6 键全为 int（可为负）
    for name, v in vars(cfg.activity.energy_delta).items():
        _int(v, f"activity.energy_delta.{name}")
```

**校验规则表**（`validate_config` 实现与此逐条对应）：

| 字段 | 约束 |
|---|---|
| `llm.provider` / `llm.model` / `llm.api_key_env` | 非空 `str` |
| `llm.base_url` | 非 `None` 时非空 `str`（`""` 静默回退映射 → 报错） |
| `llm.timeout` | 数 `> 0` |
| `llm.max_retries` | `int`（非 bool） |
| `llm.temperature` | 数 ∈ `[0, 2]`（非 bool） |
| `embedding.model` | 非空 `str` |
| `memory.short_term_capacity` / `memory.promote_threshold` | `int > 0` |
| `memory.freshness_decay` | 数 ∈ `[0, 1]` |
| `desire.peak_threshold` | 数 ∈ `[0, 1]` |
| `desire.retry_limit` / `desire.long_term_capacity` | `int > 0` |
| `desire.value_decay` | 数 `> 0` |
| `activity.grid_minutes` | `int > 0` |
| `activity.energy_delta.*` | 6 键全为 `int`（可为负） |
| `expression.slow_threshold` | 数 ∈ `[0, 1]` |
| `expression.max_context_len` / `expression.slow_max_rounds` | `int > 0` |
| `expression.ask_timeout` / `expression.chat_ignore_timeout` | 数 `> 0` |
| `exploration.web_enabled` | `bool` |
| `exploration.rate_limit_hours` | `int > 0` |

## 测试要点

- [ ] 单元测试 `tests/test_config/`：
  - [ ] `validate_config` 纯函数：合法 `Config()` 通过；越界值（`slow_threshold=1.5`、`freshness_decay=-0.1`）报错；非正（`short_term_capacity=0`、`grid_minutes=-1`、`ask_timeout=-1`、`chat_ignore_timeout=0`、`timeout=-1`）报错；错类型（改字段为 `"20"` / `True` / `max_retries="2"`）报错；`temperature` 越界（`-0.1` / `2.1`）报错、边界 `0.0`/`2.0` 合法（直接构造 `Config` 后改字段再调 `validate_config`）
  - [ ] `load_config`（tmp yaml + `monkeypatch` 环境变量）：
    - [ ] 缺键填默认（只写 `llm.provider` → 其余字段=默认）
    - [ ] **energy_delta 递归构造**：写部分键 → `cfg.activity.energy_delta` 是 `ActivityEnergyDelta` 实例，`isinstance` 通过、未写键用默认
    - [ ] **energy_delta 未知键**：`energy_delta: {reading: -20, bogus: 99}` → `ConfigError`
    - [ ] **嵌套 dataclass 字段给非 dict 值**：`memory: 100` / `energy_delta: 123` → `ConfigError`（不是 `AttributeError`/`TypeError` 裸崩溃）
    - [ ] 未知顶层键 / 段内键报 `ConfigError`
    - [ ] 文件缺失报 `ConfigError`
    - [ ] 坏 YAML 报 `ConfigError`
    - [ ] `NYX_CONFIG` 覆盖路径生效；`path=None` 时读 `config.yaml`
- [ ] 集成测试：无（无 Facade 管道）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] `load_config()` 能从 `config.yaml`（或显式 path）拿到 `Config`，点号访问到 `config.activity.energy_delta.reading`（组合根 `main.py` 归 18-api，本 spec 不创建）
