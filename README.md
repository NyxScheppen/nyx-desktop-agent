# Nyx Agent

一个住在你电脑里的桌面 AI 同伴。她会观察你的状态、记住和你的每一次互动、生出自己想做的事，并在合适的时候主动搭话。

Nyx 不是答题机器：她有一套**内在生命**——欲望会随时间涨落、记忆会沉淀、表达分快慢通道、活动会被打断又续上。她的人格原型是小说《诺斯艾兰》的女主人公「尼克斯·夏本」，**明确知道自己是个 AI，并且想要成为人类**。

## 产品核心

- **「尽量模仿真实人类」的陪伴者**（借鉴 galgame 做法），而非工具型答题助手。
- **心智模型两条对称规律**：反思改慢变量（性格/三观/长期欲望/自我叙事），事件改快变量（情感/精力/欲望值）；短期记忆「用进回复 3 次」→长期，短期欲望「反复未满足经反思」→长期。
- **三大设计原则**：减少 LLM 调用 · 核心状态可展示给前端 · 错误可溯源（`correlation_id` 贯穿因果链）。

## 功能总览（六大模块 + 横切层）

| 模块 | 职责 | 关键点 |
|---|---|---|
| 事件 | 总线：所有模块通信的管道 | 单一事件流 + 路由表；SSE 广播全部 20 类事件；溯源链 |
| 记忆 | 存储与检索，短期→长期，用户画像 | 三层检索（关键词/向量/联想）；场景化记忆；矛盾检测 |
| 表达 | 回复 / 碎碎念 / 搭话 | 快慢通道；问句等待；工具调用；主动搭话 |
| 活动 | 日程块排期，欲望的消费端 | 读书/探索/创作/观察/发呆/休息；抢占续做；书库 |
| 欲望 | 动机：短期冲动 + 长期野心 | 四种短期欲（互动/探索/创造/休息）；≤5 长期野心；可量化目标 |
| 内在生命 | 情感/性格/三观/精力 + 自我叙事 | valence/arousal/8 情绪；Big Five；三观 4 维；精力 5 档 |

**横切层**：工具系统（本地搜索/文件读写/联网搜索/抓正文，后两者 opt-in）· eval（角色一致性 OOC 告警，只记录不自动修正）· 观察用户（键鼠活跃度 + 窗口标题；屏幕视觉 opt-in）。

### 当前可用工具

| 工具 | 作用 | 注册条件 |
|---|---|---|
| `local_search` | 本地磁盘文本文件（`.txt`/`.md`）关键词搜索，返回匹配路径 + 片段 | 恒注册 |
| `file_io` | 读写本地文件：`read` 全盘读文本 / `write` 写进 `workspace/` / `list` 列目录 | 恒注册 |
| `web_search` | 联网搜索（DuckDuckGo），返回标题 / 链接 / 摘要 | `exploration.web_enabled` opt-in |
| `web_fetch` | 抓网页正文为纯文本，写进书库触发读书（下载资料来读） | `exploration.web_enabled` opt-in |

> 当前 `config.yaml` 里 `exploration.web_enabled: true`，四个工具均启用；置 `false` 则只保留前两个本地工具。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+ · FastAPI · LangGraph · SQLite（aiosqlite）· sentence-transformers |
| 前端 | React 18 · TypeScript（strict）· Zustand · Vite · Tauri v2 |
| 质检 | ruff · pyright · pytest |

后端是六块 Facade 的组合根：`memory` / `desire` / `expression` / `activity` / `inner_life`（`eval` 为基础设施），通过 SSE 把事件推给前端，REST 提供状态与操作。

## 目录结构

```
nyx/         后端包（六块 Facade + LLM 客户端 + 事件总线 + 工具注册表）
frontend/    React + Zustand + Tauri 前端
docs/        specs（设计规范）/ design（架构 + V2/V3 路线图）/ tech-reference / canon（人格设定）
tests/       后端测试（tests/test_{system}/）
prompts/     提示词模板（canon.md 核心人格 + ask.md 主动提问）
config.yaml  运行配置
```

## 快速开始

### 1. 环境

- Python 3.11+
- Node.js 18+（前端）

### 2. 后端

```bash
# 安装依赖
pip install -e .

# 配置 API Key（写入 .env，已被 .gitignore 覆盖）
# 默认使用 DeepSeek，Key 从环境变量 DEEPSEEK_API_KEY 读取
echo "DEEPSEEK_API_KEY=sk-xxx" > .env

# 启动（默认监听 8000；加 --reload 自动重启）
python -m nyx.main
```

### 3. 前端

```bash
cd frontend
npm install
npm run dev        # Vite 开发服务器 5173，/api 转发到 8000
npm run tauri dev  # 桌面壳（Tauri v2）
```

## 配置

`config.yaml` 按块组织：`llm`（模型 / provider / Key 环境变量名）、`embedding`、`memory`（短期容量 / 升级阈值）、`desire`（峰值 / 重试 / 长期容量）、`activity`（能量增减）、`expression`（快慢通道阈值）、`exploration`（联网开关）、`vision`（屏幕视觉 opt-in）。改完不用改代码，Facade 从配置装配。

## 功能边界（明确不做什么）

- **本地单用户**：无端到端加密、无用户认证 / 多账户、无企业级审计日志。
- **eval 只记录不自动修正**：OOC 评分用于可视化，不反馈回 LLM。
- **探索是线性的**：联网自由探索 =「搜 → 抓正文 → 总结」，不做逐层地牢 / 决策支 / 托管。
- **电脑控制（computer use）尚未实现**：有「眼睛」（抓屏 + 视觉描述），缺「手」（输入模拟），属 V3 backlog。
- **出站请求仅 LLM API**，不上传用户数据。

## 质量门

提交前（或每次改动后）按顺序：

```bash
python -m ruff check nyx/ tests/
python -m pyright nyx/ tests/
python -m pytest -q
```

三项必须全绿。

## 说明

- **人格设定**：`docs/canon.md`；技术接口 `docs/tech-reference.md`；架构总览 `docs/design/design.md`。
- **测试**：不依赖真实 LLM / 桌面 / 文件系统，LLM 全部注入 mock；纯数学函数（衰减、达峰、主题新鲜度等）优先测全。
- **待办**：V2 项已全部落地；剩余 backlog 见 `docs/design/V3-roadmap.md`。
