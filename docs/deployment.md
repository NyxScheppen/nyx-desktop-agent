# 修改后同步 Railway 线上环境

> 核对日期：2026-11-05。本文是现有生产环境的操作入口。
> 安装、启动与迁移见 [本地操作](operations.md)，存储边界见 [基础设施事实](facts/infrastructure-facts.md)，历史证据见 [验收记录](eval/README.md)。项目标识与部署快照在操作前重新核对。

## 1. 先确认目标与发布方式

- 仓库：`NyxScheppen/nyx-desktop-agent`，分支：`main`。
- Railway 项目：`lucid-reprieve`，ID：`b06ca77c-10d8-4b6d-9def-7f72558e12ed`。
- 环境：`production`，ID：`0ec993f5-d2db-454b-926f-367bacbac3f5`。
- 浏览器入口：<https://nyx-frontend-production.up.railway.app>。

| 服务 | 当前制品来源 | 修改后的同步方式 | 持久化 |
|---|---|---|---|
| `nyx-desktop-agent` | GitHub `main`，根 `Dockerfile` | 推送 `main` 后检查自动部署 | 托管 PostgreSQL + `/app/data` 卷 |
| `nyx-frontend` | CLI 上传，`frontend/Dockerfile` | 单独上传 `frontend/`；推送 Git 不会更新它 | 无应用数据卷 |
| `napcat` | 独立容器服务 | 只有其镜像/配置确实改变时单独部署 | `/app/.config/QQ` 卷保留登录态 |
| `Postgres` | Railway 托管服务 | 应用修改不重建或清空数据库 | PostgreSQL 数据卷 |

后端在 2026-11-04 因 README 提交 `f1cedef` 自动部署；前端当前部署 ID 为
`39188fa4-a4c7-42eb-8b52-dec2dd84f223`，来自 CLI 上传，没有 Git commit 元数据。
这是核对时的快照；每次发布以 Railway 实际部署详情为准。

## 2. 代码变更先完成本地验证

1. 按 [开发约定](development.md) 完成调查、方案审核与最小实现；基本事实或操作方式改变时更新对应文档。
2. 运行相关静态检查与回归。代码发布验收必须重跑全部 E2E，不能用定向回归代替；日常修改按影响范围验证，不机械增加单测。
3. 检查差异及敏感文件，再只暂存本次明确修改的源码、测试和文档。

在仓库根运行后端与完整 E2E 检查：

```powershell
uv sync --locked --extra dev
if ($LASTEXITCODE -ne 0) { throw "开发依赖同步失败，停止发布" }
uv run --no-sync python -m ruff check nyx/ tests/ scripts/
if ($LASTEXITCODE -ne 0) { throw "Ruff 检查失败，停止发布" }
uv run --no-sync python -m pyright nyx/ tests/ scripts/
if ($LASTEXITCODE -ne 0) { throw "Pyright 检查失败，停止发布" }

@'
import os
import tempfile
from pathlib import Path

import pytest

with tempfile.TemporaryDirectory(prefix="nyx-release-check-") as directory:
    config = Path(directory) / "config.yaml"
    config.write_text("{}\n", encoding="utf-8")
    os.environ.update(
        NYX_CONFIG=str(config),
        NYX_AUTH_TOKEN="",
        NYX_DATABASE_URL="",
        NYX_PUBLIC_ORIGIN="",
        COVERAGE_FILE=str(Path(directory) / ".coverage"),
    )
    result = pytest.main([
        "-q", "--cov=nyx", "--cov-config=pyproject.toml",
        "--cov-report=term-missing", "--cov-fail-under=90",
    ])
raise SystemExit(result)
'@ | uv run --no-sync python -
if ($LASTEXITCODE -ne 0) { throw "后端发布检查失败，停止发布" }
```

全量 pytest 已包含统一成功 E2E 集合；coverage 会采集 Python 子进程并在本次会话合并，无需先重复运行独立 E2E。默认重新采集，不使用 `--cov-append` 合入历史结果。独立 runner 仍用于[定向路径诊断](eval/README.md#复用已有驱动)。

前端检查：

```powershell
Push-Location frontend
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "前端依赖安装失败，停止发布" }
    npm test -- --run
    if ($LASTEXITCODE -ne 0) { throw "前端测试失败，停止发布" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "前端构建失败，停止发布" }
} finally {
    Pop-Location
}
```

桌面壳变更还需执行 [桌面构建](operations.md#桌面构建) 中的 Rust/sidecar 检查。
上述测试进程使用临时默认配置，关闭 OneBot，并覆盖认证、数据库及公网部署变量；不改本机 `.env` 或调用终端的环境。测试使用隔离数据库，不能连接生产库。独立 runner 通过不能替代发布命令成功，场景失败、超时、空 E2E 集合或覆盖率不足均应停止发布。
纯文档整理检查链接、差异、规则同步及敏感内容即可，不把未运行的测试写成通过。

## 3. 提交与线上同步

先检查：

```powershell
git status --short
git diff --check
git diff --cached --stat
```

`data/`、`data-postgres/` 是当前本机未跟踪运行数据，不属于代码改动；`.env`、数据库、
备份、token 文件、二维码和临时 SSH 私钥也不能提交。不要使用无范围的 `git add .`，
不要对含上述文件的仓库根目录执行未经检查的 CLI 上传；`.dockerignore` 不能代替 Git
暂存检查或 CLI 上传内容检查。

### 后端：GitHub 自动部署

选择文件暂存、审查并提交后执行：

```powershell
git push origin main
railway link --project b06ca77c-10d8-4b6d-9def-7f72558e12ed --environment production
railway status
railway deployment list --service nyx-desktop-agent --environment production --limit 3
```

确认项目、环境与服务均匹配，再检查部署 commit 是否等于本次 `git rev-parse HEAD`，
状态是否成功。Railway 不会等待本地检查，推送前须完成验证。纯文档 push 也可能触发后端重建，发布前说明
这一影响；没有同步线上要求时，不为整理文档额外触发发布。

### 前端：单独上传

先把前端修改提交并推送，核对 Git 差异与上传目录，再从仓库根执行：

```powershell
railway up frontend --path-as-root --project b06ca77c-10d8-4b6d-9def-7f72558e12ed --service nyx-frontend --environment production --ci --message "frontend commit $(git rev-parse --short HEAD)"
railway deployment list --service nyx-frontend --environment production --limit 3
```

上传只使用 `frontend/` 作为根；构建成功后仍要等新实例上线并验收。前后端均有改动时，
分别核对两个服务的部署；不兼容 API 变更应先另定切换方案，不能仅靠顺序发布保证兼容。
仅修改后端时不要顺手重新部署 NapCat/Postgres，避免额外中断。

## 4. 保持代理、配置与 Secret 边界

- 前端 `NYX_BACKEND_URL`：`http://nyx-desktop-agent.railway.internal:8080`。
- 后端 `NYX_PUBLIC_ORIGIN`：`https://nyx-frontend-production.up.railway.app`。
- `frontend/Caddyfile` 的 `header_up Host` 与该前端域名一致；`flush_interval -1` 保持 SSE 及时转发。
- 后端 `NYX_DATABASE_URL` 引用托管 PostgreSQL 的连接变量；应用卷继续挂载 `/app/data`。
- 云端 QQ 已启用时，后端使用 `NYX_CONFIG=/app/nyx.config.yaml`、
  `NYX_ONEBOT_WS_URL=ws://napcat.railway.internal:3001` 及既有角色绑定。

不要用后端公网 URL 代替私网上游：公网路由依赖 Host，改成前端 Host 可能绕回前端；
改成后端 Host 又会被 Nyx 的 `NYX_PUBLIC_ORIGIN` 校验以 `invalid_host` 拒绝。
更换域名时同步检查前端公开域名、代理 Host 与后端 origin。

`DEEPSEEK_API_KEY`、`NYX_AUTH_TOKEN`、`ONEBOT_ACCESS_TOKEN` 和带凭证的数据库 URL
只通过已授权的本机凭证来源或 Railway Variables/Secrets 注入。不要输出完整变量 JSON、
在命令参数中粘贴真实值、写入前端 `VITE_*`/镜像/仓库，或通过 URL/query 传 token。
前端静态服务不需要持有后端根 token；客户端通过既有登录/设备配对流程访问 API。
NapCat WebUI 不配置公网域名；临时隧道、临时 key 和登录二维码用完即清理。

## 5. 线上验收与留证

1. 在 Railway 核对两个应用服务的部署 ID、来源 commit/上传说明、状态和挂载；查看近期
   日志，确认无启动失败、数据库方言错误或持续重启。日志有用户内容，记录时脱敏。
2. 从前端 HTTPS 域名访问首页，确认返回 200、静态资源加载正常；匿名访问受保护 API
   应被拒绝。后端公网域名不是当前浏览器业务入口；公网 `/api/ready` 也不是健康验收入口。
3. 用现有已授权浏览器会话检查 `/api/characters`、当前角色 `/api/characters/{id}/state`
   返回 200；`/api/characters/{id}/events` 返回 200、`text/event-stream` 并收到帧。
   兼容别名 `/api/state`、`/api/events` 也用于之前的 Nyx 生产验收。
4. 检查登录/配对、角色状态、消息与内心话、头像、刷新历史以及 SSE 断线恢复。对认证
   或设备功能的改动再用临时测试设备确认撤销；不要撤销用户正在使用的凭证。
5. 涉及 QQ 时沿用 Nyx 的唯一 OneBot 会话，先看连接状态；真实外发必须在已授权范围内，
   演练只发“你好”，不能另起竞争会话或把历史 outbox 重新批量发给用户。

页面 200 或 Railway 显示 Online 都不足以证明业务通过；SSE 主动限时结束也不等于失败，
需同时确认响应头与收到的帧。日常部署不自动重复生产库恢复或强制 outbox 演练。
将日期、commit、两个 deployment ID、执行的检查、失败原因/修复和未覆盖范围写入
[验收记录](eval/README.md)，剩余事项只维护在 [TODO](TODO.md)。

## 6. 失败回退与备份入口

- 先保留脱敏错误证据，检查构建日志、Host/origin/私网上游、数据库连接与卷权限，再修复；
  不循环重启 Postgres、清卷或覆盖生产数据来“修复”应用错误。
- 无不兼容数据迁移时，可在 Railway 部署历史选择对应服务的已验证制品回退；前端 CLI
  上传部署不能仅用 Git 后端回退代替。重新验收 API/SSE，并记录代码与线上版本的差异。
- 有数据迁移时先确认旧代码兼容当前 schema；代码回退不会回退数据库。正式恢复须另行
  确认停写与恢复范围，并一起恢复匹配的 PostgreSQL 和应用卷。
- 已有备份：`D:\nyx-production-backups\20261104-114505`，包含 `postgres-railway.dump`、
  `app-data.tar.gz`、`SHA256SUMS.txt` 和 `README.txt`，详见 [恢复演练记录](eval/README.md#railway-生产备份与恢复演练2026-11-04)。
  该路径是 2026-11-04 的恢复点，不是当前最新数据保证；用户负责保管、轮换和正式恢复，
  备份不上传 Git，本次未配置平台 PITR。
