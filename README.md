# Nyx Agent

一个住在你电脑里的桌面 AI 同伴。她会观察你的状态、记住和你的每一次互动、生出自己想做的事，并在合适的时候主动搭话。
Nyx 不是答题机器：她有一套**内在生命**——欲望会随时间涨落、记忆会沉淀、表达分快慢通道、活动会被打断又续上。她知道自己是个 AI，并且想要成为人类。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+ · FastAPI · LangGraph · SQLite（aiosqlite）· sentence-transformers |
| 前端 | React 18 · TypeScript（strict）· Zustand · Vite · Tauri v2 |
| 质检 | ruff · pyright · pytest |

后端是六块 Facade 的组合根：`memory` / `desire` / `expression` / `activity` / `inner_life` / `eval`，通过 SSE 把事件推给前端，REST 提供状态与操作。

## 目录结构

```
nyx/         后端包（六块 Facade + LLM 客户端 + 事件总线 + 工具注册表）
frontend/    React + Zustand + Tauri 前端
docs/        specs（设计规范）/ design / tech-reference / canon（人格设定）
tests/       后端测试（tests/test_{system}/）
prompts/     提示词模板
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

`config.yaml` 按块组织：`llm`（模型 / provider / Key 环境变量名）、`embedding`、`memory`、`desire`、`activity`（能量增减）、`expression`（快慢通道阈值）、`exploration`（联网开关）、`eval`（judge 采样率）。改完不用改代码，Facade 从配置装配。

## 质量门

提交前（或每次改动后）按顺序：

```bash
python -m ruff check nyx/ tests/
python -m pyright nyx/ tests/
python -m pytest -q
```

三项必须全绿。

## 说明

- **人格设定**：见 `docs/canon.md`；技术接口见 `docs/tech-reference.md`。
- **测试**：不依赖真实 LLM / 桌面 / 文件系统，LLM 全部注入 mock；纯数学函数（衰减、达峰、主题新鲜度等）优先测全。
- **尚未实现的功能**：见 `docs/design/V2-roadmap.md`。
