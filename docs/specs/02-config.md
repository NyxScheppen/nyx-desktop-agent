# 配置加载

> 范围：`nyx/config.py`（`Config` + 7 个分段 dataclass + `load_config()` + `validate_config()` + `ConfigError`）+ `config.yaml`。
> 纯配置 spec：只做加载与校验，不含 Facade、不含 DDL、不含 API。
> spec 只定义契约（分段 + 字段约束 + 加载/校验语义）；字段与默认值以 `config.yaml` / `nyx/config.py` 源文件为准。

## 元信息

- **前置依赖**：无（配置项在 `config.yaml` / `nyx/config.py`，此处只给契约）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一份启动时同步加载、带默认值、类型安全、错配即报的配置对象，以便各 Facade 用 `config.llm.model` 这种点号访问读参数。

## 验收标准

- [ ] `config.py` 含 `Config` + 7 分段 dataclass + `ActivityEnergyDelta`，字段与源文件一致（实现见 `nyx/config.py`）
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
- **分段清单（8 个 dataclass）**：顶层 `Config` 聚合 7 段——`LlmConfig` / `EmbeddingConfig` / `MemoryConfig` / `DesireConfig` / `ActivityConfig`（含 `ActivityEnergyDelta`：reading/creation/free_exploration/observe_user/idle_reflection/rest 6 键能量增减）/ `ExpressionConfig` / `ExplorationConfig`。字段与默认值以 `nyx/config.py` 为准，约束见下方校验规则表。

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
