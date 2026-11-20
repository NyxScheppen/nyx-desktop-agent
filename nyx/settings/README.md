# 系统策略配置

这里是系统默认行为的唯一来源。每个系统一份 JSON，代码只读取数据、实施算法与校验。

| 文件 | 所属规则 |
|---|---|
| `memory.json` | 记忆、事实、对话分块、召回与 embedding |
| `desire.json` | 欲望压力、生命周期、强化与抑制 |
| `inner_life.json` | 情绪、精力、内在文档与反思 |
| `activity.json` | 活动选择、执行、创作、观察与问卷 |
| `reading.json` | 阅读、分段、陪读冲动、进度与阅读界面 |
| `expression.json` | 表达分类、上下文、工具流程与执行提示词 |
| `research.json` | 探索预算、研究决策与研究记录 |
| `behavior.json` | 行为反思、内置 skill 默认策略与工具授权 |
| `events.json` | 总线投递、重试、租约与恢复 |
| `runtime.json` | 服务地址、宿主循环、启动关闭、时区与桌面进程 |
| `storage.json` | 数据库路径、锁、操作超时与熔断 |
| `llm.json`、`vision.json` | 模型客户端、provider 地址与视觉默认值 |
| `onebot.json` | OneBot 接入、出向队列与重连 |
| `tools.json` | 搜索、抓取、文件与工具回执预算 |
| `character.json` | 角色卡与宿主角色数量限制 |
| `api.json` | API 查询、验证与接入展示 |
| `eval.json` | 评估与偏离检测 |
| `frontend.json` | 通用界面、外观默认值、通知与客户端计时 |

字段按消费者模块分组；`defaults` 是可由公开运行配置覆盖的默认值，`parameters` 是模块内策略，`templates` 保存含参数的文案。`frontend` 与 `copy` 保存界面数据。同一规则的重复引用可使用 `{"$ref":"系统:字段.路径"}`，数组可用数字下标引用；Python 消费者会解析引用并拒绝循环。前端应直接引用拥有原值的字段。

Python 使用 `policy("系统", "字段.路径", 类型)`，不提供 fallback；容器、枚举、正则与路径也会校验和重建。返回的可变容器彼此隔离，角色不能改坏其他角色读取的默认值。`scripts/check_policy.py` 校验全部 Python 引用、文件引用和模块级策略常量；它不是证明仓库不存在任何字面量的穷举工具。

公开运行字段的优先级是：已支持的环境覆盖 > `config.yaml`（或显式路径 / `NYX_CONFIG` 指定的配置）> 本目录默认值。运行文件只保存确实需要覆盖的字段，不复制整份默认值。角色卡、外部知识、用户状态和运行中已修订的 skill 是独立输入；`NYX_CANON_DIR` 指定的自定义 prompt/skill 目录仍按显式输入加载。

策略在进程内缓存；修改后重启后端。前端与 Rust 桌面壳在构建时读取 JSON，修改相关字段后重新构建。Python 包和桌面 sidecar 必须携带 `settings/*.json`；前端 bundle 和 Rust `include_str!` 取自同一目录。

保留在代码中的结构性内容包括算法、协议/类型字段、SQL 与迁移、文件格式、数学边界与单位换算。测试与评测的输入、预期结果不迁入默认配置，以免预期结果跟随实现一起变化。

验证入口：`uv run --no-sync python -m scripts.check_policy`，然后按修改涉及的消费路径执行现有回归与构建。
